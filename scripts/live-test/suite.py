# ruff: noqa: E501, S311
"""Independent migration test suite against a real AWS test account and a real New Relic account.

Each iteration builds a different mix of AWS resources (alarms, custom metrics, log metric filters,
X-Ray, composites, dashboards, topics), then runs four levels and tears everything down:

  1 sanity       doctor works; what the importer reads equals what was created
  2 integration  plan-only migrate; every element is classified as the docs say it should be
  3 e2e          migrate --apply into New Relic; every created object is read back and checked
  4 regression   re-apply creates nothing new; output is stable across iterations; rollback and
                 teardown leave both accounts empty

Expectations come from docs/what-migrates.md, not from the converter, so a disagreement is a
finding about the tool (or about the docs). Read docs/testing-migration.md before running.

    uv run python scripts/live-test/suite.py --aws-account-id <aws-account-id> --iterations 12 --yes
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import boto3
from pydantic import SecretStr

from importer.core.apply import NewRelicClient
from importer.core.manifest import CreatedObject, load_manifest
from importer.core.migration_summary import load_summary
from importer.core.remediation import steps_for
from importer.core.rollback import current

RUN = f"-{int(time.time()) % 100000}"  # keeps dashboard names unique: New Relic lists deleted ones for a while
PREFIX = "ckt-"  # every AWS object this suite creates starts with this
TAG = {"Key": "owner", "Value": "conquest-importer-suite"}
NS = "Custom/ckt"
LOGNS = "Custom/ckt-logs"
REGION = "us-east-1"
OUT = Path("out/suite")
NRQL = "query($a: Int!, $n: Nrql!) { actor { account(id: $a) { nrql(query: $n) { results } } } }"
CONDITION = (
    "query($a: Int!, $id: ID!) { actor { account(id: $a) { alerts { nrqlCondition(id: $id) "
    "{ ... on AlertsNrqlBaselineCondition { id name enabled nrql { query } } "
    "... on AlertsNrqlStaticCondition { id name enabled nrql { query } } } } } } }"
)
DASH = (
    "query($g: EntityGuid!) { actor { entity(guid: $g) { name ... on DashboardEntity "
    "{ permissions } } } }"
)
WORKFLOW = (
    "query($a: Int!, $id: ID!) { actor { account(id: $a) { aiWorkflows "
    "{ workflows(filters: {id: $id}) { entities { id name enabled } } } } } }"
)
POLICIES = (
    "query($a: Int!, $q: String!) { actor { account(id: $a) { alerts { policiesSearch("
    "searchCriteria: {name: $q}) { policies { id name } } } } } }"
)
DASH_SEARCH = "query($q: String!) { actor { entitySearch(query: $q) { results { entities { guid name } } } } }"


@dataclass
class Ctx:
    account: str
    cw: Any
    logs: Any
    sns: Any
    sqs: Any
    xray: Any
    arns: dict[str, str] = field(default_factory=dict)


@dataclass
class Element:
    """One thing to create in AWS and what the docs say should happen to it."""

    key: str
    group: str  # alarm, composite, dashboard, topic, other
    expect: str  # migrated | not_migrated | either | not_reported
    reasons: tuple[str, ...] = ()  # words that must appear in the reasons when not migrated
    needs: tuple[str, ...] = ()
    create: Callable[[Ctx], None] | None = None
    tags: tuple[str, ...] = ()

    @property
    def name(self) -> str:
        return PREFIX + self.key + (RUN if self.group == "dashboard" else "")


@dataclass
class Finding:
    iteration: int
    level: str
    where: str
    what: str


def stat_alarm(
    key: str, ns: str, metric: str, dims: dict[str, str], **extra: Any
) -> Callable[[Ctx], None]:
    def make(ctx: Ctx) -> None:
        params: dict[str, Any] = {
            "AlarmName": PREFIX + key,
            "Namespace": ns,
            "MetricName": metric,
            "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
            "Statistic": "Sum",
            "Period": 300,
            "EvaluationPeriods": 1,
            "Threshold": 5.0,
            "ComparisonOperator": "GreaterThanThreshold",
            "Tags": [TAG],
        }
        params.update(extra)
        if "ExtendedStatistic" in params or "Metrics" in params:
            params.pop("Statistic", None)
        if "Metrics" in params:
            for k in ("Namespace", "MetricName", "Dimensions"):
                params.pop(k, None)
        action = params.pop("TopicKey", None)
        if action:
            params["AlarmActions"] = [ctx.arns[action]]
        ctx.cw.put_metric_alarm(**params)

    return make


def metric_ref(
    ns: str, name: str, dims: dict[str, str], stat: str, period: int = 300
) -> dict[str, Any]:
    return {
        "Metric": {
            "Namespace": ns,
            "MetricName": name,
            "Dimensions": [{"Name": k, "Value": v} for k, v in dims.items()],
        },
        "Period": period,
        "Stat": stat,
    }


def composite(key: str, rule: str) -> Callable[[Ctx], None]:
    def make(ctx: Ctx) -> None:
        ctx.cw.put_composite_alarm(AlarmName=PREFIX + key, AlarmRule=rule, Tags=[TAG])

    return make


def alarm_ref(key: str) -> str:
    return f'ALARM("{PREFIX}{key}")'


def widget(kind: str, **props: Any) -> dict[str, Any]:
    return {"type": kind, "x": 0, "y": 0, "width": 12, "height": 6, "properties": props}


def dashboard(key: str, widgets: list[dict[str, Any]]) -> Callable[[Ctx], None]:
    def make(ctx: Ctx) -> None:
        for i, w in enumerate(widgets):
            w["x"], w["y"] = (i % 2) * 12, (i // 2) * 6
        body = json.dumps({"widgets": widgets}).replace("ACCOUNT", ctx.account)
        ctx.cw.put_dashboard(DashboardName=PREFIX + key + RUN, DashboardBody=body)

    return make


def mline(
    *metrics: list[Any], view: str = "timeSeries", stacked: bool = False, title: str = "t"
) -> dict[str, Any]:
    return widget(
        "metric",
        metrics=list(metrics),
        view=view,
        stacked=stacked,
        region=REGION,
        title=title,
        period=300,
        stat="Sum",
    )


def topic(key: str, with_queue: bool) -> Callable[[Ctx], None]:
    def make(ctx: Ctx) -> None:
        arn = ctx.sns.create_topic(Name=PREFIX + key, Tags=[TAG])["TopicArn"]
        ctx.arns[key] = arn
        if with_queue:
            url = ctx.sqs.create_queue(
                QueueName=f"{PREFIX}{key}-q-{random.getrandbits(24):06x}",
                tags={"owner": TAG["Value"]},
            )["QueueUrl"]
            qarn = ctx.sqs.get_queue_attributes(QueueUrl=url, AttributeNames=["QueueArn"])[
                "Attributes"
            ]["QueueArn"]
            policy = {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "sns.amazonaws.com"},
                        "Action": "sqs:SendMessage",
                        "Resource": qarn,
                        "Condition": {"ArnEquals": {"aws:SourceArn": arn}},
                    }
                ],
            }
            ctx.sqs.set_queue_attributes(QueueUrl=url, Attributes={"Policy": json.dumps(policy)})
            ctx.sns.subscribe(TopicArn=arn, Protocol="sqs", Endpoint=qarn)

    return make


def custom_data(ctx: Ctx) -> None:
    data = [
        {
            "MetricName": "Orders",
            "Dimensions": [{"Name": "Shop", "Value": "web"}],
            "Value": float(v),
            "Unit": "Count",
        }
        for v in (3, 5, 8)
    ]
    ctx.cw.put_metric_data(Namespace=NS, MetricData=data)


def log_filter(ctx: Ctx) -> None:
    group = "/ckt/app"
    ctx.logs.create_log_group(logGroupName=group, tags={"owner": TAG["Value"]})
    ctx.logs.put_metric_filter(
        logGroupName=group,
        filterName=PREFIX + "errors",
        filterPattern="ERROR",
        metricTransformations=[
            {"metricName": "ckt-errors", "metricNamespace": LOGNS, "metricValue": "1"}
        ],
    )
    ctx.logs.create_log_stream(logGroupName=group, logStreamName="s1")
    now = int(time.time() * 1000)
    ctx.logs.put_log_events(
        logGroupName=group,
        logStreamName="s1",
        logEvents=[
            {"timestamp": now, "message": "ERROR boom"},
            {"timestamp": now + 1, "message": "INFO ok"},
        ],
    )


def trace_and_group(ctx: Ctx) -> None:
    ctx.xray.create_group(
        GroupName=PREFIX + "slow", FilterExpression="responsetime > 5", Tags=[TAG]
    )
    epoch = f"{int(time.time()):08x}"
    trace = f"1-{epoch}-{random.getrandbits(96):024x}"
    seg = {
        "trace_id": trace,
        "id": f"{random.getrandbits(64):016x}",
        "name": "ckt-service",
        "start_time": time.time() - 1,
        "end_time": time.time(),
    }
    ctx.xray.put_trace_segments(TraceSegmentDocuments=[json.dumps(seg)])


def anomaly(ctx: Ctx) -> None:
    metrics = [
        {
            "Id": "m1",
            "MetricStat": metric_ref(NS, "Orders", {"Shop": "web"}, "Average"),
            "ReturnData": True,
        },
        {
            "Id": "ad1",
            "Expression": "ANOMALY_DETECTION_BAND(m1, 2)",
            "Label": "band",
            "ReturnData": True,
        },
    ]
    ctx.cw.put_metric_alarm(
        AlarmName=PREFIX + "anomaly",
        Metrics=metrics,
        ThresholdMetricId="ad1",
        ComparisonOperator="GreaterThanUpperThreshold",
        EvaluationPeriods=1,
        Tags=[TAG],
    )


def math_alarm(key: str, expr: str) -> Callable[[Ctx], None]:
    def make(ctx: Ctx) -> None:
        metrics = [
            {
                "Id": "m1",
                "MetricStat": metric_ref("AWS/Lambda", "Errors", {"FunctionName": "ckt-fn"}, "Sum"),
                "ReturnData": False,
            },
            {
                "Id": "m2",
                "MetricStat": metric_ref(
                    "AWS/Lambda", "Invocations", {"FunctionName": "ckt-fn"}, "Sum"
                ),
                "ReturnData": False,
            },
            {"Id": "e1", "Expression": expr, "Label": key, "ReturnData": True},
        ]
        ctx.cw.put_metric_alarm(
            AlarmName=PREFIX + key,
            Metrics=metrics,
            ComparisonOperator="GreaterThanThreshold",
            Threshold=5.0,
            EvaluationPeriods=1,
            Tags=[TAG],
        )

    return make


FN = {"FunctionName": "ckt-fn"}
CATALOG: list[Element] = [
    # Standard alarms on the services the docs say are supported
    Element(
        "lambda-errors",
        "alarm",
        "migrated",
        create=stat_alarm("lambda-errors", "AWS/Lambda", "Errors", FN),
        tags=("std",),
    ),
    Element(
        "lambda-p99",
        "alarm",
        "migrated",
        create=stat_alarm(
            "lambda-p99", "AWS/Lambda", "Duration", FN, ExtendedStatistic="p99", Period=60
        ),
        tags=("std", "pct"),
    ),
    Element(
        "sqs-depth",
        "alarm",
        "migrated",
        create=stat_alarm(
            "sqs-depth",
            "AWS/SQS",
            "ApproximateNumberOfMessagesVisible",
            {"QueueName": "ckt-q"},
            Statistic="Average",
        ),
        tags=("std",),
    ),
    Element(
        "ddb-throttle",
        "alarm",
        "migrated",
        create=stat_alarm(
            "ddb-throttle", "AWS/DynamoDB", "ThrottledRequests", {"TableName": "ckt-t"}
        ),
        tags=("std",),
    ),
    Element(
        "apigw-5xx",
        "alarm",
        "migrated",
        create=stat_alarm("apigw-5xx", "AWS/ApiGateway", "5XXError", {"ApiName": "ckt-api"}),
        tags=("std",),
    ),
    Element(
        "kinesis-iter",
        "alarm",
        "migrated",
        create=stat_alarm(
            "kinesis-iter",
            "AWS/Kinesis",
            "GetRecords.IteratorAgeMilliseconds",
            {"StreamName": "ckt-s"},
            Statistic="Maximum",
        ),
        tags=("std",),
    ),
    Element(
        "ec2-cpu",
        "alarm",
        "migrated",
        create=stat_alarm(
            "ec2-cpu",
            "AWS/EC2",
            "CPUUtilization",
            {"InstanceId": "i-0123456789abcdef0"},
            Statistic="Average",
        ),
        tags=("std",),
    ),
    Element(
        "rds-cpu",
        "alarm",
        "migrated",
        create=stat_alarm(
            "rds-cpu",
            "AWS/RDS",
            "CPUUtilization",
            {"DBInstanceIdentifier": "ckt-db"},
            Statistic="Average",
        ),
        tags=("std", "unverified"),
    ),
    Element(
        "ecs-cpu",
        "alarm",
        "migrated",
        create=stat_alarm(
            "ecs-cpu",
            "AWS/ECS",
            "CPUUtilization",
            {"ClusterName": "ckt", "ServiceName": "svc"},
            Statistic="Average",
        ),
        tags=("std", "unverified"),
    ),
    Element(
        "alb-5xx",
        "alarm",
        "migrated",
        create=stat_alarm(
            "alb-5xx",
            "AWS/ApplicationELB",
            "HTTPCode_Target_5XX_Count",
            {"LoadBalancer": "app/ckt/abc"},
        ),
        tags=("std",),
    ),
    Element(
        "sns-failed",
        "alarm",
        "migrated",
        create=stat_alarm(
            "sns-failed", "AWS/SNS", "NumberOfNotificationsFailed", {"TopicName": "ckt-alerts"}
        ),
        tags=("std",),
    ),
    Element(
        "elasticache",
        "alarm",
        "migrated",
        create=stat_alarm(
            "elasticache",
            "AWS/ElastiCache",
            "CPUUtilization",
            {"CacheClusterId": "ckt-c"},
            Statistic="Average",
        ),
        tags=("std", "unverified"),
    ),
    # Custom metrics, logs, traces
    Element(
        "custom-orders",
        "alarm",
        "migrated",
        create=stat_alarm("custom-orders", NS, "Orders", {"Shop": "web"}),
        tags=("custom",),
    ),
    Element(
        "custom-nodim",
        "alarm",
        "migrated",
        create=stat_alarm(
            "custom-nodim",
            NS,
            "Heartbeat",
            {},
            Statistic="Minimum",
            ComparisonOperator="LessThanThreshold",
        ),
        tags=("custom",),
    ),
    Element(
        "log-errors",
        "alarm",
        "not_migrated",
        reasons=("letters and digits",),
        needs=("log-source",),
        create=stat_alarm("log-errors", LOGNS, "ckt-errors", {}),
        tags=("custom", "logs"),
    ),
    Element("log-source", "other", "not_reported", create=log_filter, tags=("logs",)),
    Element("xray", "other", "not_reported", create=trace_and_group, tags=("traces",)),
    Element("custom-data", "other", "not_reported", create=custom_data, tags=("custom",)),
    # Approximated behaviour the docs call out
    Element(
        "three-of-five",
        "alarm",
        "migrated",
        create=stat_alarm(
            "three-of-five", "AWS/Lambda", "Errors", FN, EvaluationPeriods=5, DatapointsToAlarm=3
        ),
        tags=("std", "notes"),
    ),
    Element(
        "notbreaching",
        "alarm",
        "migrated",
        create=stat_alarm(
            "notbreaching", "AWS/Lambda", "Throttles", FN, TreatMissingData="notBreaching"
        ),
        tags=("std", "notes"),
    ),
    Element(
        "math-rate",
        "alarm",
        "migrated",
        create=math_alarm("math-rate", "m1/m2*100"),
        tags=("math",),
    ),
    # Things the docs say do not migrate
    Element(
        "math-func",
        "alarm",
        "not_migrated",
        ("math",),
        create=math_alarm("math-func", "RATE(m1)"),
        tags=("math", "edge"),
    ),
    Element(
        "anomaly",
        "alarm",
        "not_migrated",
        ("anomaly",),
        create=anomaly,
        needs=("custom-data",),
        tags=("edge",),
    ),
    Element(
        "highres",
        "alarm",
        "not_migrated",
        ("high-resolution",),
        create=stat_alarm("highres", "AWS/Lambda", "Errors", FN, Period=30),
        tags=("edge",),
    ),
    Element(
        "samplecount",
        "alarm",
        "not_migrated",
        ("statistic",),
        create=stat_alarm("samplecount", "AWS/Lambda", "Errors", FN, Statistic="SampleCount"),
        tags=("edge",),
    ),
    Element(
        "oneday",
        "alarm",
        "not_migrated",
        ("window",),
        create=stat_alarm(
            "oneday",
            "AWS/S3",
            "BucketSizeBytes",
            {"BucketName": "ckt-b", "StorageType": "StandardStorage"},
            Statistic="Average",
            Period=86400,
        ),
        tags=("edge",),
    ),
    Element(
        "ec2-action",
        "alarm",
        "migrated",
        create=stat_alarm(
            "ec2-action",
            "AWS/EC2",
            "StatusCheckFailed",
            {"InstanceId": "i-0123456789abcdef0"},
            AlarmActions=["arn:aws:automate:us-east-1:ec2:reboot"],
        ),
        tags=("edge", "actions"),
    ),
    # Notifications
    Element(
        "alerts",
        "topic",
        "not_reported",
        needs=(),
        create=topic("alerts", with_queue=False),
        tags=("notify",),
    ),
    Element(
        "queue-topic",
        "topic",
        "not_migrated",
        ("sqs",),
        create=topic("queue-topic", with_queue=True),
        tags=("notify",),
    ),
    Element(
        "with-queue-topic",
        "alarm",
        "migrated",
        needs=("queue-topic",),
        create=stat_alarm(
            "with-queue-topic", "AWS/Lambda", "Throttles", FN, TopicKey="queue-topic"
        ),
        tags=("notify",),
    ),
    Element(
        "with-topic",
        "alarm",
        "migrated",
        needs=("alerts",),
        create=stat_alarm("with-topic", "AWS/Lambda", "Errors", FN, TopicKey="alerts"),
        tags=("notify",),
    ),
    # Composites
    Element(
        "comp-or",
        "composite",
        "not_migrated",
        ("composite",),
        needs=("lambda-errors", "sqs-depth"),
        create=composite("comp-or", f"{alarm_ref('lambda-errors')} OR {alarm_ref('sqs-depth')}"),
        tags=("composite",),
    ),
    Element(
        "comp-and",
        "composite",
        "not_migrated",
        ("composite",),
        needs=("lambda-errors", "ddb-throttle"),
        create=composite(
            "comp-and", f"{alarm_ref('lambda-errors')} AND {alarm_ref('ddb-throttle')}"
        ),
        tags=("composite",),
    ),
    Element(
        "comp-complex",
        "composite",
        "not_migrated",
        ("composite",),
        needs=("lambda-errors", "sqs-depth", "apigw-5xx", "highres"),
        create=composite(
            "comp-complex",
            f"({alarm_ref('lambda-errors')} AND {alarm_ref('sqs-depth')}) OR (NOT {alarm_ref('apigw-5xx')} AND {alarm_ref('highres')})",
        ),
        tags=("composite",),
    ),
    # Dashboards
    Element(
        "dash-metrics",
        "dashboard",
        "migrated",
        create=dashboard(
            "dash-metrics",
            [
                mline(["AWS/Lambda", "Errors", "FunctionName", "ckt-fn"], title="line"),
                mline(
                    ["AWS/Lambda", "Errors", "FunctionName", "ckt-fn"], stacked=True, title="stack"
                ),
                mline(
                    ["AWS/SQS", "NumberOfMessagesSent", "QueueName", "ckt-q"],
                    view="bar",
                    title="bar",
                ),
                mline(
                    ["AWS/SQS", "NumberOfMessagesSent", "QueueName", "ckt-q"],
                    view="singleValue",
                    title="single",
                ),
                widget("text", markdown="# ckt dashboard\nTest."),
            ],
        ),
        tags=("dash",),
    ),
    Element(
        "dash-custom",
        "dashboard",
        "migrated",
        create=dashboard(
            "dash-custom",
            [
                mline([NS, "Orders", "Shop", "web"], title="orders"),
                mline([LOGNS, "ckt-errors"], title="log errors"),
            ],
        ),
        tags=("dash", "custom"),
    ),
    Element(
        "dash-logs",
        "dashboard",
        "either",
        ("log",),
        create=dashboard(
            "dash-logs",
            [
                mline(["AWS/Lambda", "Errors", "FunctionName", "ckt-fn"], title="ok"),
                widget(
                    "log",
                    query="SOURCE '/ckt/app' | fields @message | limit 5",
                    region=REGION,
                    title="logs",
                    view="table",
                ),
            ],
        ),
        tags=("dash", "logs"),
    ),
    Element(
        "dash-alarm",
        "dashboard",
        "either",
        ("alarm",),
        create=dashboard(
            "dash-alarm",
            [
                mline(["AWS/Lambda", "Errors", "FunctionName", "ckt-fn"], title="ok"),
                widget(
                    "alarm",
                    title="status",
                    alarms=["arn:aws:cloudwatch:us-east-1:ACCOUNT:alarm:ckt-lambda-errors"],
                ),
            ],
        ),
        tags=("dash",),
    ),
    Element(
        "dash-math",
        "dashboard",
        "either",
        ("math",),
        create=dashboard(
            "dash-math",
            [
                mline(
                    ["AWS/Lambda", "Errors", "FunctionName", "ckt-fn", {"id": "m1"}],
                    ["AWS/Lambda", "Invocations", "FunctionName", "ckt-fn", {"id": "m2"}],
                    [{"expression": "m1/m2*100", "label": "rate", "id": "e1"}],
                    title="math",
                ),
            ],
        ),
        tags=("dash", "math"),
    ),
]
BY_KEY = {e.key: e for e in CATALOG}


def pick(profile: str, seed: int) -> list[Element]:
    """The elements for one iteration, plus everything they need, in a creatable order."""
    rng = random.Random(seed)
    if profile == "full":
        chosen = list(CATALOG)
    elif profile.startswith("tags:"):
        wanted = set(profile[5:].split(","))
        chosen = [e for e in CATALOG if wanted & set(e.tags)]
    else:
        chosen = [e for e in CATALOG if rng.random() < 0.5]
    keys: dict[str, Element] = {}

    def add(e: Element) -> None:
        for n in e.needs:
            add(BY_KEY[n])
        keys[e.key] = e

    for e in chosen:
        add(e)
    order = {"other": 0, "topic": 1, "alarm": 2, "composite": 3, "dashboard": 4}
    return sorted(keys.values(), key=lambda e: order[e.group])


PROFILES = [
    "full",
    "tags:std,pct",
    "tags:custom,logs,traces",
    "tags:composite",
    "tags:dash",
    "tags:notify,actions",
    "tags:edge,math,notes",
    "random",
    "random",
    "random",
    "random",
    "full",
]


LOG: list[Path] = []  # the current iteration's log folder (one entry, set by iterate)


def importer(*args: str) -> subprocess.CompletedProcess[str]:
    code = "import sys; from importer.cli import app; app()"
    done = subprocess.run(  # noqa: S603
        [sys.executable, "-c", code, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=900,
    )
    if LOG:
        n = len(list(LOG[0].glob("*.log"))) + 1
        (LOG[0] / f"{n:02d}-{args[0]}.log").write_text(
            f"$ importer {' '.join(args)}\nexit {done.returncode}\n\n{done.stdout}\n{done.stderr}"
        )
    return done


def load_env() -> None:
    for line in Path(".env").read_text().splitlines():
        if "=" in line and not line.startswith("#"):
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip("\"'"))


def make_ctx(account: str) -> Ctx:
    s = boto3.Session(region_name=REGION)
    got = s.client("sts").get_caller_identity()["Account"]
    if got != account:
        sys.exit(f"signed in to {got}, expected {account}; stopping")
    return Ctx(
        account,
        s.client("cloudwatch"),
        s.client("logs"),
        s.client("sns"),
        s.client("sqs"),
        s.client("xray"),
    )


def build(
    ctx: Ctx, elements: list[Element], iteration: int, findings: list[Finding]
) -> list[Element]:
    made: list[Element] = []
    for e in elements:
        if e.create is None:
            continue
        try:
            e.create(ctx)
            made.append(e)
        except Exception as exc:
            findings.append(
                Finding(iteration, "setup", e.key, f"AWS refused to create it: {str(exc)[:200]}")
            )
    return made


def teardown(ctx: Ctx) -> list[str]:
    """Remove everything this suite created, then return whatever is left (should be empty)."""
    cw = ctx.cw
    names = [
        a["AlarmName"]
        for a in cw.describe_alarms(AlarmNamePrefix=PREFIX, AlarmTypes=["CompositeAlarm"])[
            "CompositeAlarms"
        ]
    ]
    for name in names:  # AWS allows one composite alarm per delete request
        cw.delete_alarms(AlarmNames=[name])
    metric = [
        a["AlarmName"]
        for p in cw.get_paginator("describe_alarms").paginate(
            AlarmNamePrefix=PREFIX, AlarmTypes=["MetricAlarm"]
        )
        for a in p["MetricAlarms"]
    ]
    for i in range(0, len(metric), 100):
        cw.delete_alarms(AlarmNames=metric[i : i + 100])
    dashes = [
        d["DashboardName"]
        for d in cw.list_dashboards(DashboardNamePrefix=PREFIX)["DashboardEntries"]
    ]
    if dashes:
        cw.delete_dashboards(DashboardNames=dashes)
    for d in cw.describe_anomaly_detectors(Namespace=NS).get("AnomalyDetectors", []):
        sm = d.get("SingleMetricAnomalyDetector")
        if sm:
            cw.delete_anomaly_detector(SingleMetricAnomalyDetector=sm)
    for t in ctx.sns.list_topics()["Topics"]:
        if t["TopicArn"].split(":")[-1].startswith(PREFIX):
            ctx.sns.delete_topic(TopicArn=t["TopicArn"])
    for q in ctx.sqs.list_queues(QueueNamePrefix=PREFIX).get("QueueUrls", []):
        ctx.sqs.delete_queue(QueueUrl=q)
    for g in ctx.logs.describe_log_groups(logGroupNamePrefix="/ckt/")["logGroups"]:
        ctx.logs.delete_log_group(logGroupName=g["logGroupName"])
    for g in ctx.xray.get_groups().get("Groups", []):
        if g["GroupName"].startswith(PREFIX):
            ctx.xray.delete_group(GroupName=g["GroupName"])
    return leftovers(ctx)


def leftovers(ctx: Ctx) -> list[str]:
    left = [a["AlarmName"] for a in ctx.cw.describe_alarms(AlarmNamePrefix=PREFIX)["MetricAlarms"]]
    left += [
        a["AlarmName"]
        for a in ctx.cw.describe_alarms(AlarmNamePrefix=PREFIX, AlarmTypes=["CompositeAlarm"])[
            "CompositeAlarms"
        ]
    ]
    left += [
        d["DashboardName"]
        for d in ctx.cw.list_dashboards(DashboardNamePrefix=PREFIX)["DashboardEntries"]
    ]
    left += [
        t["TopicArn"]
        for t in ctx.sns.list_topics()["Topics"]
        if t["TopicArn"].split(":")[-1].startswith(PREFIX)
    ]
    return left


def digest(path: Path) -> dict[str, str]:
    """Hash of each converted alarm, by name, with ids and timestamps removed."""
    out: dict[str, str] = {}
    for f in sorted((path / "alerts" / "ir").glob("*.json")):
        d = json.loads(f.read_text())
        d.pop("source_id", None)
        out[d["name"]] = hashlib.sha256(json.dumps(d, sort_keys=True).encode()).hexdigest()[:16]
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower())


def level_sanity(
    ctx: Ctx, made: list[Element], run: Path, add: Callable[[str, str, str], None]
) -> None:
    doc = importer("doctor", "--region", REGION)
    if doc.returncode != 0:
        add(
            "sanity",
            "doctor",
            f"importer doctor exited {doc.returncode}: {(doc.stdout + doc.stderr)[-300:]}",
        )
    ex = importer(
        "extract-cloudwatch",
        "--account-id",
        ctx.account,
        "--region",
        REGION,
        "--out",
        str(run / "export"),
    )
    if ex.returncode != 0:
        add(
            "sanity",
            "extract",
            f"extract-cloudwatch exited {ex.returncode}: {(ex.stdout + ex.stderr)[-300:]}",
        )
        return
    alarms = json.loads(next((run / "export" / "alarms").glob("*.json")).read_text())
    got_metric = sorted(
        a["AlarmName"] for a in alarms.get("MetricAlarms", []) if a["AlarmName"].startswith(PREFIX)
    )
    got_comp = sorted(
        a["AlarmName"]
        for a in alarms.get("CompositeAlarms", [])
        if a["AlarmName"].startswith(PREFIX)
    )
    want_metric = sorted(e.name for e in made if e.group == "alarm")
    want_comp = sorted(e.name for e in made if e.group == "composite")
    if got_metric != want_metric:
        add(
            "sanity",
            "extract",
            f"metric alarms: created {len(want_metric)}, read {len(got_metric)}; missing {sorted(set(want_metric) - set(got_metric))}",
        )
    if got_comp != want_comp:
        add(
            "sanity", "extract", f"composite alarms: created {len(want_comp)}, read {len(got_comp)}"
        )


def level_integration(
    made: list[Element], plan: Path, add: Callable[[str, str, str], None]
) -> dict[str, Any]:
    summary = load_summary(plan)
    by_name = {a.name: a for a in summary.assets}
    for e in made:
        asset = by_name.get(e.name) or next(
            (a for n, a in by_name.items() if n.startswith(e.name + ": ")), None
        )
        if e.expect == "not_reported":
            if asset is not None:
                add(
                    "integration",
                    e.key,
                    f"expected to be ignored, but reported as {asset.kind}/{asset.confidence}",
                )
            continue
        if asset is None:
            add("integration", e.key, "missing from the report entirely")
            continue
        migrated = asset.migrated
        if e.expect == "migrated" and not migrated:
            add(
                "integration",
                e.key,
                f"docs say it migrates; got {asset.confidence.value}: {'; '.join(asset.reasons)[:200]}",
            )
        if e.expect == "not_migrated" and migrated:
            add("integration", e.key, f"docs say it does not migrate; got {asset.confidence.value}")
        if not migrated:
            text = norm(" ".join(asset.reasons))
            missing = [w for w in e.reasons if w not in text]
            if missing:
                add("integration", e.key, f"reason does not mention {missing}: {text[:160]}")
            if not steps_for(asset.reasons) and not asset.own_steps:
                add("integration", e.key, "not migrated and no remediation steps")
    counts = summary.counts()
    total = sum(counts.values())
    expected_total = sum(1 for e in made if e.expect != "not_reported")
    if total != expected_total:
        add("integration", "totals", f"report counts {total} assets, expected {expected_total}")
    if not (plan / "report.html").exists():
        add("integration", "report", "report.html was not written")
    return {"headline": summary.headline(), "assets": total}


def nr_get(client: NewRelicClient, nr: int, obj: CreatedObject) -> dict[str, Any] | None:
    q: str
    v: dict[str, Any]
    if obj.kind == "condition":
        q, v = CONDITION, {"a": nr, "id": obj.id}
        data = client.run(q, v, is_write=False)
        return data["actor"]["account"]["alerts"]["nrqlCondition"]  # type: ignore[no-any-return]
    if obj.kind == "dashboard":
        return client.run(DASH, {"g": obj.id}, is_write=False)["actor"]["entity"]  # type: ignore[no-any-return]
    if obj.kind == "workflow":
        ents = client.run(WORKFLOW, {"a": nr, "id": obj.id}, is_write=False)["actor"]["account"][
            "aiWorkflows"
        ]["workflows"]["entities"]
        return ents[0] if ents else None  # type: ignore[no-any-return]
    return current(client, nr, obj)


def level_e2e(
    client: NewRelicClient,
    nr: int,
    aws: str,
    made: list[Element],
    run: Path,
    add: Callable[[str, str, str], None],
) -> Path | None:
    done = importer(
        "migrate",
        "--aws-account-id",
        aws,
        "--nr-account-id",
        str(nr),
        "--region",
        REGION,
        "--out",
        str(run / "apply"),
        "--apply",
        "--confirm-account",
        str(nr),
    )
    if done.returncode != 0:
        add(
            "e2e",
            "migrate",
            f"migrate --apply exited {done.returncode}: {(done.stdout + done.stderr)[-400:]}",
        )
    if done.returncode != 0:
        # migrate stops at the first failed step; apply the rest directly so they still get tested.
        base = [
            "--account-id",
            str(nr),
            "--apply",
            "--confirm-account",
            str(nr),
            "--manifest",
            str(run / "apply" / "manifest.json"),
        ]
        for cmd, folder in (
            ("apply-notifications", "notifications"),
            ("apply-dashboards", "dashboards"),
        ):
            src = run / "apply" / folder
            if src.exists():
                importer(cmd, "--in", str(src), *base)
    manifest = run / "apply" / "manifest.json"
    if not manifest.exists():
        add("e2e", "manifest", "no manifest.json written, so nothing can be rolled back")
        return None
    rec = load_manifest(manifest)
    summary = load_summary(run / "apply")
    kinds = {k: sum(1 for o in rec.objects if o.kind == k) for k in {o.kind for o in rec.objects}}
    want_cond = sum(1 for e in made if e.group == "alarm" and e.expect in ("migrated",))
    if kinds.get("condition", 0) < want_cond - 3 or kinds.get("condition", 0) > want_cond + 3:
        add(
            "e2e",
            "conditions",
            f"created {kinds.get('condition', 0)} conditions, expected about {want_cond}",
        )
    for o in rec.objects:
        try:
            got = nr_get(client, nr, o)
        except Exception as exc:
            add("e2e", f"{o.kind}:{o.name}", f"read-back failed: {str(exc)[:160]}")
            continue
        if got is None:
            add("e2e", f"{o.kind}:{o.name}", "created but not found in New Relic")
            continue
        if o.kind in ("condition", "workflow") and got.get("enabled"):
            add("e2e", f"{o.kind}:{o.name}", "is ENABLED; everything must be created disabled")
        if o.kind == "dashboard" and "PRIVATE" not in str(got.get("permissions", "")):
            add(
                "e2e",
                f"dashboard:{o.name}",
                f"is not private (permissions={got.get('permissions')})",
            )
        if o.kind == "condition":
            query = (got.get("nrql") or {}).get("query", "")
            try:
                client.run(NRQL, {"a": nr, "n": f"{query} SINCE 10 minutes ago"}, is_write=False)
            except Exception as exc:
                add(
                    "e2e",
                    f"condition:{o.name}",
                    f"NRQL does not run in New Relic: {str(exc)[:200]} :: {query[:160]}",
                )
    created = summary.created or {}
    if created and sum(created.values()) != len(rec.objects):
        add(
            "e2e",
            "report",
            f"report says {sum(created.values())} objects created, manifest has {len(rec.objects)}",
        )
    return manifest


def dashboard_gone(client: NewRelicClient, guid: str) -> bool:
    """True when New Relic says the dashboard does not exist (deleting it again finds nothing)."""
    answer = client.run(
        "mutation($g: EntityGuid!) { dashboardDelete(guid: $g) { status errors { type } } }",
        {"g": guid},
        is_write=True,
    )["dashboardDelete"]
    return any(e.get("type") == "DASHBOARD_NOT_FOUND" for e in answer.get("errors") or [])


def level_regression(
    client: NewRelicClient,
    nr: int,
    aws: str,
    manifest: Path,
    run: Path,
    baseline: dict[str, str],
    add: Callable[[str, str, str], None],
) -> dict[str, str]:
    first = load_manifest(manifest)
    # Idempotency: a second apply into the same account must create nothing new.
    again = importer(
        "migrate",
        "--aws-account-id",
        aws,
        "--nr-account-id",
        str(nr),
        "--region",
        REGION,
        "--out",
        str(run / "again"),
        "--apply",
        "--confirm-account",
        str(nr),
    )
    second = run / "again" / "manifest.json"
    if again.returncode != 0:
        add(
            "regression",
            "idempotency",
            f"second apply exited {again.returncode}: {(again.stdout + again.stderr)[-300:]}",
        )
    if second.exists():
        extra = [o for o in load_manifest(second).objects if o.kind != "policy"]
        if extra:
            add(
                "regression",
                "idempotency",
                f"second apply created {len(extra)} objects again: {[o.name for o in extra][:5]}",
            )
    # Stability: the same alarm must convert to the same thing in every iteration.
    now = digest(run / "apply")
    for name, h in now.items():
        if name in baseline and baseline[name] != h:
            add("regression", name, "converted output changed since an earlier iteration")
    # Rollback, then check New Relic is empty of everything we made.
    dry = importer("rollback", "--manifest", str(manifest), "--account-id", str(nr))
    if dry.returncode != 0:
        add(
            "regression",
            "rollback-dry-run",
            f"exited {dry.returncode}: {(dry.stdout + dry.stderr)[-200:]}",
        )
    real = importer(
        "rollback",
        "--manifest",
        str(manifest),
        "--account-id",
        str(nr),
        "--apply",
        "--confirm-account",
        str(nr),
    )
    if real.returncode != 0:
        add(
            "regression",
            "rollback",
            f"exited {real.returncode}: {(real.stdout + real.stderr)[-300:]}",
        )
    for o in first.objects:
        if o.kind == "dashboard":
            # New Relic's entity index lists a deleted dashboard for a long time, so ask the
            # delete call: DASHBOARD_NOT_FOUND means it is gone.
            if not dashboard_gone(client, o.id):
                add("regression", f"dashboard:{o.name}", "still exists after rollback")
            continue
        try:
            if nr_get(client, nr, o):
                add("regression", f"{o.kind}:{o.name}", "still exists after rollback")
        except Exception:  # noqa: S110 - an error reading a deleted object counts as gone
            pass
    left = client.run(
        POLICIES, {"a": nr, "q": f"Imported from CloudWatch ({aws})"}, is_write=False
    )["actor"]["account"]["alerts"]["policiesSearch"]["policies"]
    if left:
        add("regression", "policy", f"{len(left)} policy(ies) left after rollback")
    return now


def iterate(
    i: int, profile: str, ctx: Ctx, nr: int, client: NewRelicClient, baseline: dict[str, str]
) -> dict[str, Any]:
    started = time.time()
    findings: list[Finding] = []
    run = OUT / f"iteration-{i:02d}"
    run.mkdir(parents=True, exist_ok=True)
    (run / "logs").mkdir(exist_ok=True)
    LOG[:] = [run / "logs"]

    def add(level: str, where: str, what: str) -> None:
        findings.append(Finding(i, level, where, what))

    elements = pick(profile, i)
    made: list[Element] = []
    info: dict[str, Any] = {"iteration": i, "profile": profile}
    try:
        for fn in (ctx.cw.describe_alarms,):
            fn(AlarmNamePrefix=PREFIX)
        made = build(ctx, elements, i, findings)
        info["created"] = len(made)
        # CloudWatch is eventually consistent; give the new alarms a moment to be listed.
        time.sleep(5)
        level_sanity(ctx, made, run, add)
        plan = run / "plan"
        planned = importer(
            "migrate",
            "--aws-account-id",
            ctx.account,
            "--nr-account-id",
            str(nr),
            "--region",
            REGION,
            "--out",
            str(plan),
        )
        if planned.returncode != 0:
            add(
                "integration",
                "migrate",
                f"plan-only migrate exited {planned.returncode}: {(planned.stdout + planned.stderr)[-400:]}",
            )
        else:
            info.update(level_integration(made, plan, add))
        manifest = level_e2e(client, nr, ctx.account, made, run, add)
        if manifest:
            for name, h in level_regression(
                client, nr, ctx.account, manifest, run, baseline, add
            ).items():
                baseline.setdefault(name, h)
    finally:
        left = teardown(ctx)
        if left:
            add("teardown", "aws", f"left behind in AWS: {left[:5]}")
    info["seconds"] = round(time.time() - started)
    info["findings"] = [f.__dict__ for f in findings]
    (run / "result.json").write_text(json.dumps(info, indent=1))
    return info


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--aws-account-id", required=True)
    p.add_argument("--iterations", type=int, default=12)
    p.add_argument("--only", type=int, help="run just this iteration number")
    p.add_argument("--yes", action="store_true", help="confirm you accept the cost and the writes")
    args = p.parse_args()
    if not args.yes:
        sys.exit(
            "This creates real AWS resources (pennies) and writes disabled objects to New Relic. Re-run with --yes."
        )
    load_env()
    nr = int(os.environ["NEW_RELIC_ACCOUNT_ID"])
    if str(nr) not in os.environ.get("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "").split(","):
        sys.exit("NEW_RELIC_ACCOUNT_ID is not in NEW_RELIC_ALLOWED_ACCOUNT_IDS; stopping")
    ctx = make_ctx(args.aws_account_id)
    if leftovers(ctx):
        sys.exit(
            f"the account already holds {PREFIX} resources; clean them first: {leftovers(ctx)[:5]}"
        )
    OUT.mkdir(parents=True, exist_ok=True)
    baseline_file = OUT / "baseline.json"
    baseline: dict[str, str] = (
        json.loads(baseline_file.read_text()) if baseline_file.exists() else {}
    )
    results = []
    with NewRelicClient(SecretStr(os.environ["NEW_RELIC_API_KEY"])) as client:
        for i in range(1, args.iterations + 1):
            if args.only and i != args.only:
                continue
            profile = PROFILES[(i - 1) % len(PROFILES)]
            print(f"iteration {i}/{args.iterations} [{profile}] ...", flush=True)
            res = iterate(i, profile, ctx, nr, client, baseline)
            baseline_file.write_text(json.dumps(baseline, indent=1))
            results.append(res)
            print(
                f"  created {res.get('created', 0)}, {len(res['findings'])} findings, {res['seconds']}s",
                flush=True,
            )
    (OUT / "summary.json").write_text(
        json.dumps({"at": datetime.now(UTC).isoformat(), "results": results}, indent=1)
    )


if __name__ == "__main__":
    main()
