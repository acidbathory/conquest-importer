"""Work out what the metric stream must carry so converted alerts and dashboards get data.

Namespaces come from both alarms and dashboard widgets. Percentile alarms also need the stream to
send that percentile (`additional_statistics`), or the converted query would return nothing.
"""

import re

from importer.sources.cloudwatch.dashboard_models import CloudWatchDashboard
from importer.sources.cloudwatch.models import CloudWatchAlarm

_PERCENTILE = re.compile(r"^p\d{1,2}$")


def alarm_metrics(alarms: list[CloudWatchAlarm]) -> list[tuple[str, str, str]]:
    """(namespace, metric, statistic) for every metric an alarm reads."""
    found: list[tuple[str, str, str]] = []
    for a in alarms:
        if a.Namespace and a.MetricName:
            found.append((a.Namespace, a.MetricName, a.ExtendedStatistic or a.Statistic or ""))
        for q in a.Metrics or []:
            if q.MetricStat:
                m = q.MetricStat.Metric
                found.append((m.Namespace, m.MetricName, q.MetricStat.Stat))
    return found


def dashboard_metrics(dashboards: list[CloudWatchDashboard]) -> list[tuple[str, str, str]]:
    """(namespace, metric, statistic) for every plain metric row of every metric widget."""
    found: list[tuple[str, str, str]] = []
    for d in dashboards:
        for widget in d.widgets:
            props = widget.get("properties")
            rows = props.get("metrics") if isinstance(props, dict) else None
            if widget.get("type") != "metric" or not isinstance(rows, list):
                continue
            widget_stat = str(props.get("stat", "")) if isinstance(props, dict) else ""
            previous: list[object] = []
            for row in rows:
                if not isinstance(row, list) or not row or isinstance(row[0], dict):
                    continue
                tokens = [t for t in row if not isinstance(t, dict)]
                tokens = [
                    previous[i] if t == "." and i < len(previous) else t
                    for i, t in enumerate(tokens)
                ]
                if tokens[0] == "...":
                    tokens = previous[:2]
                previous = tokens
                options = row[-1] if isinstance(row[-1], dict) else {}
                if len(tokens) >= 2 and all(isinstance(t, str) for t in tokens[:2]):
                    stat = str(options.get("stat", widget_stat))
                    found.append((str(tokens[0]), str(tokens[1]), stat))
    return found


def streaming_inputs(
    alarms: list[CloudWatchAlarm], dashboards: list[CloudWatchDashboard]
) -> tuple[list[str], list[dict[str, object]]]:
    """Namespaces to stream, and percentile specs as IngestConfig expects them."""
    metrics = alarm_metrics(alarms) + dashboard_metrics(dashboards)
    namespaces = sorted({ns for ns, _, _ in metrics})
    wanted: dict[tuple[str, str], set[str]] = {}
    for ns, metric, stat in metrics:
        if _PERCENTILE.match(stat):
            wanted.setdefault((ns, metric), set()).add(stat)
    specs: list[dict[str, object]] = [
        {"namespace": ns, "metric_name": metric, "statistics": sorted(stats)}
        for (ns, metric), stats in sorted(wanted.items())
    ]
    return namespaces, specs
