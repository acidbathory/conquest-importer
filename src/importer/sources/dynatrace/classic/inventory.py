"""Inventory rows for the Dynatrace objects this source does not convert.

Alerting profiles, problem notifications, maintenance windows, management zones and the built-in
anomaly detection settings are listed in the report as manual items, each with its fixed manual
steps (see remediation_rules.py), so nothing in the tenant is silently ignored. Their conversion
(workflows, destinations, muting rules) belongs to a later step. Only counts and values from a
fixed list reach a reason; names stay in the item name, which the report escapes.
"""

from typing import Any

from importer.core.ir import Confidence, IRMonitor
from importer.core.text import printable
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.common import monitor, shown
from importer.sources.dynatrace.classic.models import SettingsObject

NAME_LIMIT = 80
NOTIFICATION_TYPES = frozenset(
    {
        "EMAIL",
        "SLACK",
        "JIRA",
        "ANSIBLETOWER",
        "OPS_GENIE",
        "PAGER_DUTY",
        "VICTOROPS",
        "WEBHOOK",
        "XMATTERS",
        "TRELLO",
        "SERVICE_NOW",
    }
)
SCHEDULES = frozenset({"ONCE", "DAILY", "WEEKLY", "MONTHLY"})
SUPPRESSIONS = frozenset(
    {"DETECT_PROBLEMS_AND_ALERT", "DETECT_PROBLEMS_DONT_ALERT", "DONT_DETECT_PROBLEMS"}
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _count(value: Any) -> int:
    return len(value) if isinstance(value, list) else 0


def _one_of(value: Any, allowed: frozenset[str]) -> str:
    return value if isinstance(value, str) and value in allowed else "other"


def _name(obj: SettingsObject, label: str) -> str:
    value = obj.value
    general = _dict(value.get("generalProperties"))
    for candidate in (value.get("name"), value.get("displayName"), general.get("name")):
        if isinstance(candidate, str) and candidate.strip():
            return printable(candidate.strip(), NAME_LIMIT)
    tail = obj.schemaId.removeprefix("builtin:")
    return printable(f"{label} {tail}".strip(), NAME_LIMIT)


def _profile(value: dict[str, Any]) -> str:
    zone = ", with a management zone" if value.get("managementZone") else ""
    return (
        f"alerting profile is not converted ({_count(value.get('severityRules'))} severity "
        f"rules, {_count(value.get('eventFilters'))} event filters{zone}): it decides which "
        "problems notify whom, and its conversion belongs with the notification workflows"
    )


def _notification(value: dict[str, Any]) -> str:
    kind = _one_of(value.get("type"), NOTIFICATION_TYPES)
    state = "enabled" if value.get("enabled") is not False else "disabled"
    return (
        f"problem notification is not converted (type {kind}, {state}): recreate it as a New "
        "Relic destination and workflow; secrets, URLs and recipients are not exported"
    )


def _maintenance(value: dict[str, Any]) -> str:
    general = _dict(value.get("generalProperties"))
    schedule = _one_of(_dict(value.get("schedule")).get("scheduleType"), SCHEDULES)
    suppression = _one_of(general.get("suppression"), SUPPRESSIONS)
    return (
        f"maintenance window is not converted (schedule {schedule}, suppression {suppression}): "
        "recreate it as a New Relic muting rule; New Relic still records alert events while "
        "muted"
    )


def _zone(value: dict[str, Any]) -> str:
    return (
        f"management zone is not converted ({_count(value.get('rules'))} rules): a zone is a "
        "set of entities built by rules, which New Relic has no equivalent for"
    )


def _detection(_value: dict[str, Any]) -> str:
    return (
        "built-in detection setting is not converted: Davis detects these problems "
        "automatically, so there is no user condition to carry over; compare non-default "
        "values by hand"
    )


KINDS = {
    models.PROFILE: ("alerting profile", _profile),
    models.NOTIFICATIONS: ("problem notification", _notification),
    models.MAINTENANCE: ("maintenance window", _maintenance),
    models.MAINTENANCE_NEW: ("maintenance window", _maintenance),
    models.ZONES: ("management zone", _zone),
}


def is_inventory(schema: str) -> bool:
    return schema in KINDS or schema.startswith(models.DETECTION_PREFIX)


def translate_inventory(obj: SettingsObject, key: str) -> IRMonitor:
    """A manual row for an object this source lists but does not convert."""
    if obj.schemaId in KINDS:
        label, describe = KINDS[obj.schemaId]
        reason = describe(obj.value)
    elif obj.schemaId.startswith(models.DETECTION_PREFIX):
        label, reason = "built-in anomaly detection setting", _detection(obj.value)
    else:
        label = "settings object"
        reason = (
            "settings schema is not converted: "
            f"{shown(obj.schemaId)} is not one of the schemas this source reads"
        )
    return monitor(
        key=key,
        name=_name(obj, label),
        source_type=label,
        confidence=Confidence.MANUAL,
        reasons=[reason],
    )
