"""Turn the optional inventory (EventBridge rules, canaries, SLOs and so on) into report assets.

None of these convert automatically. Each becomes a "manual" asset with a reason that the
remediation catalogue turns into steps, so a customer sees every piece of AWS monitoring
configuration the importer found and what to do about it. Scheduled and disabled EventBridge
rules are not monitoring and are listed as ignored instead of counted.
"""

import json
from pathlib import Path
from typing import Any

from importer.core.ir import Confidence
from importer.core.migration_summary import Asset
from importer.core.text import printable
from importer.sources.cloudwatch.logfilter import load_metric_filters
from importer.sources.cloudwatch.models import CloudWatchAlarm

NAME_LIMIT = 80


def _items(export: Path, kind: str) -> list[dict[str, Any]]:
    path = export / "inventory" / f"{kind}.json"
    if not path.is_file():
        return []
    data = json.loads(path.read_text())
    items = data.get("items", []) if isinstance(data, dict) else []
    return [i for i in items if isinstance(i, dict)]


def _name(item: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value:
            return printable(value, NAME_LIMIT)
    return "(unnamed)"


def _target_services(rule: dict[str, Any]) -> str:
    services: list[str] = []
    for target in rule.get("Targets", []) or []:
        arn = target.get("Arn", "") if isinstance(target, dict) else ""
        parts = str(arn).split(":")
        service = parts[2] if len(parts) > 2 else ""
        if service and service not in services:
            services.append(printable(service, 20))
    return ", ".join(services)


def _routes_alarms(rule: dict[str, Any]) -> bool:
    try:
        pattern = json.loads(rule.get("EventPattern") or "{}")
    except (TypeError, ValueError):
        return False
    if not isinstance(pattern, dict):
        return False
    source = pattern.get("source", [])
    detail = pattern.get("detail-type", [])
    return "aws.cloudwatch" in (source if isinstance(source, list) else [source]) or any(
        "Alarm State Change" in str(d) for d in (detail if isinstance(detail, list) else [detail])
    )


def _rules(export: Path) -> tuple[list[Asset], list[str]]:
    assets: list[Asset] = []
    scheduled = disabled = 0
    for rule in _items(export, "eventbridge-rules"):
        if rule.get("ScheduleExpression"):
            scheduled += 1
            continue
        if rule.get("State") == "DISABLED":
            disabled += 1
            continue
        reason = (
            "EventBridge rule routes CloudWatch alarm state changes; New Relic has no rule "
            "engine, alerts reach people through workflows"
            if _routes_alarms(rule)
            else "EventBridge rule reacts to AWS service events; New Relic documents sending "
            "notifications to EventBridge, not receiving events from it"
        )
        targets = _target_services(rule)
        assets.append(
            Asset(
                "other",
                _name(rule, "Name"),
                "EventBridge rule",
                Confidence.MANUAL,
                [reason],
                detail=f"targets: {targets}" if targets else "",
            )
        )
    ignored = []
    if scheduled:
        ignored.append(f"{scheduled} scheduled EventBridge rule(s): they run jobs, not monitoring")
    if disabled:
        ignored.append(f"{disabled} disabled EventBridge rule(s)")
    return assets, ignored


def _health_checks(export: Path) -> list[Asset]:
    assets = []
    for item in _items(export, "route53-health-checks"):
        config = (
            item.get("HealthCheckConfig", {})
            if isinstance(item.get("HealthCheckConfig"), dict)
            else {}
        )
        kind = str(config.get("Type", ""))
        label = (
            _name(config, "FullyQualifiedDomainName", "IPAddress")
            if kind in {"HTTP", "HTTPS", "TCP", "HTTP_STR_MATCH", "HTTPS_STR_MATCH"}
            else _name(item, "Id")
        )
        reason = (
            "Route 53 health check on an endpoint; recreate as a New Relic Synthetics ping monitor"
            if kind in {"HTTP", "HTTPS", "TCP", "HTTP_STR_MATCH", "HTTPS_STR_MATCH"}
            else "Route 53 health check that combines other checks or follows a CloudWatch alarm"
        )
        assets.append(Asset("other", label, "Route 53 health check", Confidence.MANUAL, [reason]))
    return assets


_SIMPLE = (
    (
        "synthetics-canaries",
        "Synthetics canary",
        ("Name",),
        (
            "Synthetics canary: a scheduled script check; recreate it as a New Relic Synthetics "
            "monitor"
        ),
    ),
    (
        "contributor-insights-rules",
        "Contributor Insights rule",
        ("Name",),
        "Contributor Insights rule: top-N analysis of log data; rebuild it as a log query",
    ),
    (
        "rum-app-monitors",
        "RUM app monitor",
        ("Name",),
        (
            "CloudWatch RUM app monitor: browser real-user monitoring; New Relic Browser "
            "records its own data"
        ),
    ),
    (
        "slos",
        "Application Signals SLO",
        ("Name",),
        "Application Signals SLO: recreate it as a New Relic service level",
    ),
)


def inventory_assets(export: Path, alarms: list[CloudWatchAlarm]) -> tuple[list[Asset], list[str]]:
    """Report assets for the inventory, plus a note for anything left out on purpose."""
    used = {(r.filter_name, r.log_group) for a in alarms for r in a.log_metric_filters}
    assets: list[Asset] = []
    for f in load_metric_filters(export / "logs"):
        key = (str(f.get("filterName", "")), str(f.get("logGroupName", "")))
        if key in used:
            continue  # its alarm carries it: converted or reported with its own reason
        assets.append(
            Asset(
                "other",
                _name(f, "filterName"),
                "log metric filter",
                Confidence.MANUAL,
                [
                    "log metric filter feeds no converted alarm; New Relic has no metric "
                    "filters, query the logs directly"
                ],
                detail=f"log group {printable(str(f.get('logGroupName', '')), NAME_LIMIT)}",
            )
        )
    rule_assets, ignored = _rules(export)
    assets += rule_assets
    assets += _health_checks(export)
    for kind, source_type, name_keys, reason in _SIMPLE:
        assets += [
            Asset("other", _name(i, *name_keys), source_type, Confidence.MANUAL, [reason])
            for i in _items(export, kind)
        ]
    return assets, ignored
