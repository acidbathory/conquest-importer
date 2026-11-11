"""Azure alert rules to IR monitors: one monitor per rule (per alert rule in a Prometheus group).

`translate` never raises. Anything not understood is `Confidence.MANUAL` with a reason that
`remediation_rules.py` has steps for. Metric names are derived from the documented Azure Monitor
integration naming rule and are reported as UNVERIFIED (see `names.py`).
"""

from importer.core.ir import IRMonitor
from importer.sources.azure import reasons as R
from importer.sources.azure.activity import translate_activity_log_alert
from importer.sources.azure.common import base_fields, common_notes, key_of, manual, severity_level
from importer.sources.azure.links import add_links
from importer.sources.azure.logs import (
    translate_scheduled_query_rule,
    translate_smart_detector,
)
from importer.sources.azure.metric import translate_metric_alert
from importer.sources.azure.models import AzureAlert
from importer.sources.azure.promql import convert_expression, finish

MAX_RULES = 20000


def _prometheus_group(alert: AzureAlert) -> list[IRMonitor]:
    props = alert.properties
    rules = props.get("rules")
    items = [r for r in rules if isinstance(r, dict)] if isinstance(rules, list) else []
    alerts = [r for r in items if r.get("alert") and not r.get("record")]
    records = len(items) - len(alerts)
    if not alerts:
        base = base_fields(alert, key_of(alert), "prometheus rule group", alert.name)
        return [manual(base, [R.M_PROM_RECORD])]
    monitors: list[IRMonitor] = []
    for rule in alerts[:MAX_RULES]:
        rule_name = R.shown(rule.get("alert"), 100)
        key = f"{key_of(alert)}#{rule_name}"
        name = f"{alert.name} / {rule_name}"
        base = base_fields(alert, key, "prometheus rule", name)
        level = severity_level(rule.get("severity"))
        extra = {"description_note": f"Azure severity {level.label}", "azure_severity": level.label}
        notes = common_notes(
            {"enabled": props.get("enabled") is not False and rule.get("enabled") is not False},
            level,
        )
        if records:
            notes.append(R.N_RECORD_GROUP.format(count=records))
        found, found_notes = convert_expression(
            rule.get("expression"),
            key=key,
            name=name,
            for_text=rule.get("for"),
            interval_text=props.get("interval"),
            labels=rule.get("labels"),
            group=alert.name,
        )
        monitors.append(
            add_links(alert, finish(base, found, found_notes, level, notes, extra), rule)
        )
    return monitors


def translate_alert(alert: AzureAlert) -> list[IRMonitor]:
    if alert.family == "prometheusRuleGroups":
        return _prometheus_group(alert)
    return [add_links(alert, m) for m in _translate_other(alert)]


def _translate_other(alert: AzureAlert) -> list[IRMonitor]:
    family = alert.family
    if family == "metricAlerts":
        return [translate_metric_alert(alert)]
    if family == "scheduledQueryRules":
        return [translate_scheduled_query_rule(alert)]
    if family == "activityLogAlerts":
        return [translate_activity_log_alert(alert)]
    if family == "smartDetectorAlertRules":
        return [translate_smart_detector(alert)]
    base = base_fields(alert, key_of(alert), alert.type or "unknown", alert.name)
    return [manual(base, [R.M_UNKNOWN_FAMILY])]


def translate(alerts: list[AzureAlert]) -> list[IRMonitor]:
    """All monitors; a repeated source key gets an index so ids and file names stay distinct."""
    seen: dict[str, int] = {}
    monitors: list[IRMonitor] = []
    for alert in alerts:
        for monitor in translate_alert(alert):
            count = seen.get(monitor.source_key, 0) + 1
            seen[monitor.source_key] = count
            if count > 1:
                from importer.core.ids import source_id

                key = f"{monitor.source_key}#{count}"
                monitor = monitor.model_copy(
                    update={"source_key": key, "source_id": source_id(key)}
                )
            monitors.append(monitor)
    return monitors
