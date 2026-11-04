import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import NewRelicClient
from importer.sources import REGISTRY
from importer.sources.cloudwatch.extract import Reader
from importer.sources.cloudwatch.validate import (
    Status,
    compare,
    has_problems,
    render,
    utc,
    validate,
)

START, END = datetime(2026, 10, 5, 4, 30, tzinfo=UTC), datetime(2026, 10, 5, 4, 50, tzinfo=UTC)


def alarm(name: str, **fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "AlarmName": name,
        "AlarmArn": f"arn:aws:cloudwatch:us-east-1:123456789012:alarm:{name}",
        "ActionsEnabled": True,
        "AlarmActions": [],
        "EvaluationPeriods": 1,
        "ComparisonOperator": "GreaterThanThreshold",
        "Threshold": 1,
        "Period": 60,
        "Statistic": "Sum",
        "Namespace": "AWS/SQS",
        "MetricName": "NumberOfMessagesSent",
        "Dimensions": [{"Name": "QueueName", "Value": "q"}],
    }
    base.update(fields)
    return base


class World:
    """A fake CloudWatch plus a fake New Relic, keyed by metric name."""

    def __init__(self) -> None:
        self.cw: dict[str, list[dict[str, Any]]] = {}
        self.nr: dict[str, float | None] = {}
        self.sources: dict[str, dict[str, float]] = {}
        self.aws_calls: list[dict[str, Any]] = []

    def runner(
        self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        self.aws_calls.append(params)
        return {"Datapoints": self.cw.get(params["MetricName"], [])}

    def nr_handler(self, request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["variables"]["n"]
        metric = next(m for m in list(self.nr) if re.search(rf"\.{m}\b", query))
        if "FACET collector.name" in query:
            rows = [{"facet": k, "value": v} for k, v in self.sources.get(metric, {}).items()]
        else:
            value = self.nr[metric]
            rows = [] if value is None else [{"sum.x": value}]
        return httpx.Response(
            200, json={"data": {"actor": {"account": {"nrql": {"results": rows}}}}}
        )


def run(tmp_path: Path, world: World, alarms: list[dict[str, Any]]) -> list[Any]:
    src = tmp_path / "alarms"
    src.mkdir()
    (src / "a.json").write_text(json.dumps({"MetricAlarms": alarms}))
    out = tmp_path / "out"
    monitors = REGISTRY["cloudwatch"].convert(src)
    (out / "ir").mkdir(parents=True)
    for m in monitors:
        (out / "ir" / f"{m.source_id}.json").write_text(m.model_dump_json())
    reader = Reader("us-east-1", runner=world.runner)
    client = NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(world.nr_handler))
    return validate(reader, client, 1, src, out / "ir", START, END)


def by_name(checks: list[Any]) -> dict[str, Any]:
    return {c.name: c for c in checks}


def test_match_mismatch_and_no_data_are_told_apart(tmp_path: Path) -> None:
    world = World()
    world.cw = {
        "NumberOfMessagesSent": [{"Sum": 6, "SampleCount": 6, "Minimum": 1, "Maximum": 1}] * 2,
        "Count": [{"Sum": 50, "SampleCount": 1, "Minimum": 50, "Maximum": 50}],
        "Published": [{"Sum": 9, "SampleCount": 1, "Minimum": 9, "Maximum": 9}],
    }
    world.nr = {"NumberOfMessagesSent": 12.0, "Count": 100.0, "Published": None}
    world.sources = {"Count": {"cloudwatch-metric-streams": 50.0, "cloud-integrations": 50.0}}
    alarms = [
        alarm("same", MetricName="NumberOfMessagesSent"),
        alarm(
            "double",
            Namespace="AWS/ApiGateway",
            MetricName="Count",
            Dimensions=[{"Name": "ApiName", "Value": "a"}],
        ),
        alarm(
            "missing",
            Namespace="AWS/SNS",
            MetricName="Published",
            Dimensions=[{"Name": "TopicName", "Value": "t"}],
        ),
        alarm("quiet", MetricName="Quiet"),
    ]
    world.nr["Quiet"] = None
    checks = by_name(run(tmp_path, world, alarms))
    assert checks["same"].status == Status.MATCH
    assert checks["double"].status == Status.MISMATCH
    assert (
        "several sources" in checks["double"].note
        and "cloud-integrations=50" in checks["double"].note
    )
    assert checks["missing"].status == Status.NO_DATA_IN_NEW_RELIC
    assert checks["quiet"].status == Status.NO_DATA_ANYWHERE


def test_average_is_recomputed_from_sums_and_sample_counts(tmp_path: Path) -> None:
    world = World()
    world.cw = {
        "M": [
            {"Sum": 10, "SampleCount": 2, "Minimum": 1, "Maximum": 9},
            {"Sum": 2, "SampleCount": 2, "Minimum": 0, "Maximum": 2},
        ]
    }
    world.nr = {"M": 3.0}
    checks = by_name(run(tmp_path, world, [alarm("avg", MetricName="M", Statistic="Average")]))
    assert checks["avg"].cloudwatch == 3.0 and checks["avg"].status == Status.MATCH


def test_minimum_and_maximum_use_the_extremes(tmp_path: Path) -> None:
    world = World()
    world.cw = {
        "M": [
            {"Sum": 1, "SampleCount": 1, "Minimum": 2, "Maximum": 9},
            {"Sum": 1, "SampleCount": 1, "Minimum": 1, "Maximum": 4},
        ]
    }
    world.nr = {"M": 9.0}
    checks = by_name(
        run(
            tmp_path,
            world,
            [
                alarm("hi", MetricName="M", Statistic="Maximum"),
                alarm("lo", MetricName="M", Statistic="Minimum"),
            ],
        )
    )
    assert checks["hi"].cloudwatch == 9.0 and checks["lo"].cloudwatch == 1.0


def test_what_cannot_be_compared_is_listed_not_guessed(tmp_path: Path) -> None:
    world = World()
    alarms = [
        alarm(
            "math",
            Namespace=None,
            MetricName=None,
            Statistic=None,
            Period=None,
            Dimensions=[],
            Metrics=[{"Id": "e", "Expression": "1+1", "ReturnData": True}],
        ),
        alarm("sampled", Statistic="SampleCount"),
    ]
    checks = run(tmp_path, world, alarms)
    assert {c.status for c in checks} == {Status.NOT_COMPARED}
    assert not has_problems(checks) and not world.aws_calls


def test_dimensions_reach_the_cli_as_one_json_argument(tmp_path: Path) -> None:
    world = World()
    world.cw = {"M": []}
    world.nr = {"M": None}
    tricky = 'a,b" Name=x,Value=y'
    run(
        tmp_path,
        world,
        [alarm("t", MetricName="M", Dimensions=[{"Name": "QueueName", "Value": tricky}])],
    )
    (call,) = world.aws_calls
    assert call["Dimensions"] == [{"Name": "QueueName", "Value": tricky}]


@pytest.mark.parametrize(
    ("cw", "nr", "status"),
    [
        (100.0, 101.0, Status.MATCH),
        (100.0, 103.0, Status.MISMATCH),
        (0.0, 0.0, Status.MATCH),
        (None, None, Status.NO_DATA_ANYWHERE),
        (None, 0.0, Status.NO_DATA_ANYWHERE),
        (5.0, None, Status.NO_DATA_IN_NEW_RELIC),
        (5.0, 0.0, Status.NO_DATA_IN_NEW_RELIC),
        (None, 4.0, Status.MISMATCH),
    ],
)
def test_compare_rules(cw: float | None, nr: float | None, status: Status) -> None:
    assert compare(cw, nr) == status


def test_report_puts_problems_first_and_escapes_text(tmp_path: Path) -> None:
    world = World()
    world.cw = {"M": [{"Sum": 1, "SampleCount": 1, "Minimum": 1, "Maximum": 1}]}
    world.nr = {"M": 5.0}
    evil = "x|<script>\x1b[31m"
    checks = run(tmp_path, world, [alarm(evil, MetricName="M")])
    text = render(checks, START, END)
    assert "1 need attention" in text and "\x1b" not in text and "<script>" not in text


def test_utc_parsing_accepts_z_and_naive() -> None:
    assert utc("2026-10-05T04:30:00Z") == utc("2026-10-05 04:30:00") == START


def test_cli_needs_a_new_relic_account_and_a_sane_window() -> None:
    runner = CliRunner()
    assert runner.invoke(app, ["validate", "--account-id", "123456789012"]).exit_code == 1
    res = runner.invoke(
        app,
        [
            "validate",
            "--account-id",
            "123456789012",
            "--nr-account-id",
            "1",
            "--since",
            "2026-10-05T05:00:00Z",
            "--until",
            "2026-10-05T04:00:00Z",
        ],
    )
    assert res.exit_code == 1 and "earlier" in res.output


def test_percentile_alarms_compare_the_extreme_of_the_per_minute_values(tmp_path: Path) -> None:
    world = World()
    world.cw = {"Latency": [{"ExtendedStatistics": {"p99": v}} for v in (3.0, 9.0, 5.0)]}
    world.nr = {"Latency": 9.0}
    pct = alarm(
        "pct",
        Namespace="AWS/ApiGateway",
        MetricName="Latency",
        Statistic=None,
        ExtendedStatistic="p99",
        Dimensions=[{"Name": "ApiName", "Value": "a"}],
    )
    checks = by_name(run(tmp_path, world, [pct]))
    assert checks["pct"].cloudwatch == 9.0 and checks["pct"].status == Status.MATCH
    call = world.aws_calls[0]
    assert call["ExtendedStatistics"] == ["p99"]
    assert "Statistics" not in call


def test_a_below_threshold_percentile_alarm_compares_the_minimum(tmp_path: Path) -> None:
    world = World()
    world.cw = {"Latency": [{"ExtendedStatistics": {"p99": v}} for v in (3.0, 9.0)]}
    world.nr = {"Latency": 3.0}
    pct = alarm(
        "low",
        Namespace="AWS/ApiGateway",
        MetricName="Latency",
        Statistic=None,
        ExtendedStatistic="p99",
        ComparisonOperator="LessThanThreshold",
        Dimensions=[{"Name": "ApiName", "Value": "a"}],
    )
    checks = by_name(run(tmp_path, world, [pct]))
    assert checks["low"].cloudwatch == 3.0 and checks["low"].status == Status.MATCH
