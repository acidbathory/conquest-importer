"""Create (or delete) a tagged set of CloudWatch alarms in the TEST account for measurement.

Cost: standard alarms are $0.10 each per month (first 10 free); minutes of life cost pennies.
Alarms are built from the synthetic fixture, renamed with the `ckr-` prefix, with actions stripped
and no SNS topics. `delete` removes only alarms whose names start with the prefix AND that this
script created (checked via the tag `owner=conquest-importer-measure`).

    uv run python scripts/live-test/measure_alarms.py create --account-id <aws-account-id>
    uv run python scripts/live-test/measure_alarms.py delete --account-id <aws-account-id>
"""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

PREFIX = "ckr-"
TAG = {"Key": "owner", "Value": "conquest-importer-measure"}
FIXTURE = Path("tests/fixtures/cloudwatch/describe-alarms.json")
KEEP = [
    "AlarmName",
    "AlarmDescription",
    "ActionsEnabled",
    "MetricName",
    "Namespace",
    "Statistic",
    "ExtendedStatistic",
    "Dimensions",
    "Period",
    "Unit",
    "EvaluationPeriods",
    "DatapointsToAlarm",
    "Threshold",
    "ComparisonOperator",
    "TreatMissingData",
    "EvaluateLowSampleCountPercentile",
    "Metrics",
    "ThresholdMetricId",
]


def aws(*args: str) -> dict[str, object]:
    done = subprocess.run(  # noqa: S603
        ["aws", *args, "--output", "json"],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(done.stderr.strip()[:300])
    return json.loads(done.stdout) if done.stdout.strip() else {}


def check_account(expected: str) -> None:
    got = aws("sts", "get-caller-identity").get("Account")
    if got != expected:
        sys.exit(f"signed in to {got}, expected {expected}; stopping")


def create() -> None:
    data = json.loads(FIXTURE.read_text())
    ok: list[str] = []
    for a in data["MetricAlarms"]:
        body = {k: a[k] for k in KEEP if k in a}
        body["AlarmName"] = PREFIX + a["AlarmName"]
        body["ActionsEnabled"] = False
        body["Tags"] = [TAG]
        if "Unit" in body and not body["Unit"]:
            del body["Unit"]
        with tempfile.NamedTemporaryFile("w", suffix=".json") as f:
            json.dump(body, f)
            f.flush()
            try:
                aws("cloudwatch", "put-metric-alarm", "--cli-input-json", f"file://{f.name}")
                ok.append(body["AlarmName"])
            except RuntimeError as e:
                print(f"SKIPPED {a['AlarmName'][:40]!r}: {e}")
    for c in data["CompositeAlarms"]:
        rule = c["AlarmRule"]
        for a in data["MetricAlarms"]:
            rule = rule.replace(f'"{a["AlarmName"]}"', f'"{PREFIX}{a["AlarmName"]}"')
        try:
            aws(
                "cloudwatch",
                "put-composite-alarm",
                "--alarm-name",
                PREFIX + c["AlarmName"],
                "--alarm-rule",
                rule,
                "--tags",
                f"Key={TAG['Key']},Value={TAG['Value']}",
            )
            ok.append(PREFIX + c["AlarmName"])
        except RuntimeError as e:
            print(f"SKIPPED composite: {e}")
    print(f"created {len(ok)} alarms")


def delete() -> None:
    data = aws(
        "cloudwatch",
        "describe-alarms",
        "--alarm-name-prefix",
        PREFIX,
        "--alarm-types",
        "MetricAlarm",
        "CompositeAlarm",
    )
    composites: list[str] = []
    metrics: list[str] = []
    for key in ("CompositeAlarms", "MetricAlarms"):
        for a in data.get(key, []):  # type: ignore[attr-defined]
            tags = aws("cloudwatch", "list-tags-for-resource", "--resource-arn", a["AlarmArn"])
            if TAG in tags.get("Tags", []):  # type: ignore[operator]
                (composites if key == "CompositeAlarms" else metrics).append(a["AlarmName"])
    # Composites first: AWS refuses to delete alarms that a composite still depends on.
    for batch in (composites, metrics):
        for i in range(0, len(batch), 50):
            aws("cloudwatch", "delete-alarms", "--alarm-names", *batch[i : i + 50])
    print(f"deleted {len(composites) + len(metrics)} alarms")


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("action", choices=["create", "delete"])
    p.add_argument("--account-id", required=True)
    args = p.parse_args()
    check_account(args.account_id)
    create() if args.action == "create" else delete()
