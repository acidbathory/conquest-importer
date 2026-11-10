"""Side-by-side validation: run each converted query in New Relic and compare it with CloudWatch.

For every simple alarm (one metric, a plain statistic) the converted NRQL is run over a time
window in New Relic and the same window is read from CloudWatch with get-metric-statistics. A
mismatch is explained when possible (several data sources delivering the same metric). Alarms this
cannot compare (percentiles, metric math, not converted) are listed, never guessed. Read-only.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from importer.core.apply import NewRelicAPIError, NewRelicClient
from importer.core.emit.common import CannotEmit, nrql_for
from importer.core.ir import IRMonitor
from importer.core.report_brand import header_lines
from importer.core.text import printable
from importer.sources.cloudwatch.extract import AwsError, Reader
from importer.sources.cloudwatch.models import CloudWatchAlarm, load_alarms

NRQL_QUERY = (
    "query($a: Int!, $n: Nrql!) { actor { account(id: $a) { nrql(query: $n) { results } } } }"
)
STREAM_COLLECTOR = "cloudwatch-metric-streams"
TOLERANCE = 0.02
PLAIN_STATISTICS = {"Sum", "Average", "Minimum", "Maximum"}


class Status(StrEnum):
    MATCH = "match"
    MISMATCH = "MISMATCH"
    NO_DATA_IN_NEW_RELIC = "NO DATA IN NEW RELIC"
    NO_DATA_ANYWHERE = "no data in either"
    NOT_COMPARED = "not compared"


@dataclass
class Check:
    name: str
    status: Status
    cloudwatch: float | None = None
    new_relic: float | None = None
    note: str = ""


def utc(text: str) -> datetime:
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)


def cloudwatch_value(
    reader: Reader, alarm: CloudWatchAlarm, start: datetime, end: datetime
) -> float | None:
    """The alarm's statistic over the window, from CloudWatch's own per-minute data."""
    dims = [{"Name": d.Name, "Value": d.Value} for d in alarm.Dimensions]
    percentile = alarm.ExtendedStatistic
    kind: dict[str, Any] = (
        {"ExtendedStatistics": [percentile]}
        if percentile
        else {"Statistics": ["Sum", "SampleCount", "Minimum", "Maximum"]}
    )
    data = reader.call(
        "cloudwatch",
        "get_metric_statistics",
        Namespace=alarm.Namespace,
        MetricName=alarm.MetricName,
        Dimensions=dims,
        StartTime=start,
        EndTime=end,
        Period=60,
        **kind,
    )
    points = data.get("Datapoints", [])
    if not points:
        return None
    if percentile:
        # One value per minute; the converter takes the extreme that matches the alarm direction.
        values = [p["ExtendedStatistics"][percentile] for p in points]
        below = alarm.ComparisonOperator in (
            "LessThanThreshold",
            "LessThanOrEqualToThreshold",
        )
        return float(min(values) if below else max(values))
    stat = alarm.Statistic
    total = sum(p["Sum"] for p in points)
    if stat == "Sum":
        return float(total)
    if stat == "Average":
        count = sum(p["SampleCount"] for p in points)
        return float(total / count) if count else None
    if stat == "Minimum":
        return float(min(p["Minimum"] for p in points))
    return float(max(p["Maximum"] for p in points))


def _rows(client: NewRelicClient, account_id: int, query: str) -> list[dict[str, Any]]:
    data = client.run(NRQL_QUERY, {"a": account_id, "n": query}, is_write=False)
    rows: list[dict[str, Any]] = data["actor"]["account"]["nrql"]["results"]
    return rows


def _window(start: datetime, end: datetime) -> str:
    return f"SINCE '{start:%Y-%m-%d %H:%M:%S}' UNTIL '{end:%Y-%m-%d %H:%M:%S}' WITH TIMEZONE 'UTC'"


def new_relic_value(
    client: NewRelicClient, account_id: int, nrql: str, start: datetime, end: datetime
) -> float | None:
    query = f"{nrql} {_window(start, end)}"
    rows = client.run(NRQL_QUERY, {"a": account_id, "n": query}, is_write=False)["actor"][
        "account"
    ]["nrql"]["results"]
    if not rows:
        return None
    value = next(iter(rows[0].values()), None)
    return float(value) if isinstance(value, int | float) else None


def collectors(
    client: NewRelicClient, account_id: int, nrql: str, start: datetime, end: datetime
) -> dict[str, float]:
    """The same query split by data source, to explain a mismatch."""
    query = f"{nrql} FACET collector.name {_window(start, end)}"
    rows = client.run(NRQL_QUERY, {"a": account_id, "n": query}, is_write=False)["actor"][
        "account"
    ]["nrql"]["results"]
    out: dict[str, float] = {}
    for row in rows:
        values = [v for k, v in row.items() if k != "facet" and isinstance(v, int | float)]
        out[str(row.get("facet"))] = float(values[0]) if values else 0.0
    return out


def compare(cw: float | None, nr: float | None, tolerance: float = TOLERANCE) -> Status:
    if cw is None and (nr is None or nr == 0):
        return Status.NO_DATA_ANYWHERE
    if nr is None or (nr == 0 and cw):
        return Status.NO_DATA_IN_NEW_RELIC
    if cw is None:
        return Status.MISMATCH
    return (
        Status.MATCH if abs(nr - cw) <= tolerance * max(abs(cw), abs(nr), 1e-9) else Status.MISMATCH
    )


def explain(
    client: NewRelicClient, account_id: int, nrql: str, start: datetime, end: datetime, cw: float
) -> str:
    try:
        by_source = collectors(client, account_id, nrql, start, end)
    except NewRelicAPIError:
        return ""
    if len(by_source) > 1:
        parts = ", ".join(f"{printable(k, 30)}={v:g}" for k, v in sorted(by_source.items()))
        return (
            "the same metric arrives from several sources (" + parts + "): disable one, or "
            f"filter with collector.name = '{STREAM_COLLECTOR}'"
        )
    only = by_source.get(STREAM_COLLECTOR)
    if only is not None and cw and abs(only / cw - 2.0) < 0.1:
        return "about twice CloudWatch: likely a dimension set or duplicate rows counted twice"
    return ""


def validate(
    reader: Reader,
    client: NewRelicClient,
    account_id: int,
    alarms_path: Path,
    alerts_dir: Path,
    start: datetime,
    end: datetime,
) -> list[Check]:
    metric_alarms, composite = load_alarms(alarms_path)
    monitors = {
        m.name: m
        for m in (
            IRMonitor.model_validate_json(p.read_text()) for p in sorted(alerts_dir.glob("*.json"))
        )
    }
    checks = [Check(c.AlarmName, Status.NOT_COMPARED, note="composite alarm") for c in composite]

    def skip(name: str, note: str) -> None:
        checks.append(Check(name, Status.NOT_COMPARED, note=note))

    for alarm in metric_alarms:
        monitor = monitors.get(alarm.AlarmName)
        if monitor is None:
            checks.append(
                Check(alarm.AlarmName, Status.NOT_COMPARED, note="not in the converted set")
            )
            continue
        if alarm.Metrics is not None:
            skip(alarm.AlarmName, "metric math")
            continue
        comparable = alarm.Statistic in PLAIN_STATISTICS or alarm.ExtendedStatistic
        if not (comparable and alarm.Namespace and alarm.MetricName):
            checks.append(
                Check(alarm.AlarmName, Status.NOT_COMPARED, note="statistic not supported")
            )
            continue
        try:
            nrql = nrql_for(monitor)
        except (CannotEmit, ValueError):
            checks.append(
                Check(alarm.AlarmName, Status.NOT_COMPARED, note="alarm was not converted")
            )
            continue
        try:
            cw = cloudwatch_value(reader, alarm, start, end)
            nr = new_relic_value(client, account_id, nrql, start, end)
        except (AwsError, NewRelicAPIError, ValidationError, KeyError) as exc:
            checks.append(
                Check(alarm.AlarmName, Status.NOT_COMPARED, note=printable(str(exc), 100))
            )
            continue
        status = compare(cw, nr)
        note = ""
        if status in (Status.MISMATCH, Status.NO_DATA_IN_NEW_RELIC) and cw is not None:
            note = explain(client, account_id, nrql, start, end, cw)
        checks.append(Check(alarm.AlarmName, status, cw, nr, note))
    return checks


def render(checks: list[Check], start: datetime, end: datetime) -> str:
    from importer.core.report import md_cell

    def num(v: float | None) -> str:
        return "-" if v is None else f"{v:g}"

    bad = {Status.MISMATCH, Status.NO_DATA_IN_NEW_RELIC}
    lines = [
        *header_lines("Side-by-side validation", "cloudwatch"),
        "",
        f"Window: {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC. Tolerance {TOLERANCE:.0%}.",
        f"Compared {sum(c.status != Status.NOT_COMPARED for c in checks)} of {len(checks)} alarms; "
        f"**{sum(c.status in bad for c in checks)} need attention**.",
        "",
        "> New Relic only has data from when ingestion started. Use a window that starts after it.",
        "",
        "| Alarm | Result | CloudWatch | New Relic | Note |",
        "|---|---|---|---|---|",
    ]
    lines.extend(
        f"| {md_cell(printable(c.name, 60))} | {c.status.value} | {num(c.cloudwatch)} | "
        f"{num(c.new_relic)} | {md_cell(printable(c.note, 300), 300)} |"
        for c in sorted(checks, key=lambda x: (x.status not in bad, x.name))
    )
    return "\n".join(lines) + "\n"


def has_problems(checks: list[Any]) -> bool:
    return any(c.status in (Status.MISMATCH, Status.NO_DATA_IN_NEW_RELIC) for c in checks)
