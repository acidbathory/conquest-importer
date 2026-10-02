"""CloudWatch source models, in the shape of `aws cloudwatch describe-alarms` output.

Unknown fields are kept (extra="allow") so nothing is silently dropped.
"""

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

MAX_INPUT_BYTES = 50 * 1024 * 1024


class Dimension(BaseModel):
    model_config = ConfigDict(extra="allow")

    Name: str
    Value: str


class MetricRef(BaseModel):
    model_config = ConfigDict(extra="allow")

    Namespace: str
    MetricName: str
    Dimensions: list[Dimension] = []


class MetricStatSpec(BaseModel):
    model_config = ConfigDict(extra="allow")

    Metric: MetricRef
    Period: int
    Stat: str
    Unit: str | None = None


class MetricDataQuery(BaseModel):
    model_config = ConfigDict(extra="allow")

    Id: str
    MetricStat: MetricStatSpec | None = None
    Expression: str | None = None
    Label: str | None = None
    ReturnData: bool = True
    Period: int | None = None


class CloudWatchAlarm(BaseModel):
    model_config = ConfigDict(extra="allow")

    AlarmName: str
    AlarmArn: str = ""
    AlarmDescription: str | None = None
    ActionsEnabled: bool = True
    OKActions: list[str] = []
    AlarmActions: list[str] = []
    InsufficientDataActions: list[str] = []
    MetricName: str | None = None
    Namespace: str | None = None
    Statistic: str | None = None
    ExtendedStatistic: str | None = None
    Dimensions: list[Dimension] = []
    Period: int | None = None
    EvaluationPeriods: int
    DatapointsToAlarm: int | None = None
    Threshold: float | None = None
    ThresholdMetricId: str | None = None
    ComparisonOperator: str
    TreatMissingData: str | None = None
    Metrics: list[MetricDataQuery] | None = None
    Unit: str | None = None


class CompositeAlarm(BaseModel):
    model_config = ConfigDict(extra="allow")

    AlarmName: str
    AlarmArn: str = ""
    AlarmDescription: str | None = None
    ActionsEnabled: bool = True
    AlarmActions: list[str] = []
    AlarmRule: str


def _files(path: Path) -> list[Path]:
    return sorted(path.glob("*.json")) if path.is_dir() else [path]


def load_alarms(path: Path) -> tuple[list[CloudWatchAlarm], list[CompositeAlarm]]:
    """Read one describe-alarms JSON file, or every *.json file in a directory."""
    metric: list[CloudWatchAlarm] = []
    composite: list[CompositeAlarm] = []
    for file in _files(path):
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file.name} is larger than {MAX_INPUT_BYTES // 1024 // 1024} MB")
        data: Any = json.loads(file.read_text())
        if isinstance(data, list):  # a bare list of metric alarms
            data = {"MetricAlarms": data}
        if not isinstance(data, dict):
            raise ValueError(f"{file.name}: expected the JSON output of describe-alarms")
        metric += [CloudWatchAlarm.model_validate(a) for a in data.get("MetricAlarms", [])]
        composite += [CompositeAlarm.model_validate(a) for a in data.get("CompositeAlarms", [])]
    return metric, composite
