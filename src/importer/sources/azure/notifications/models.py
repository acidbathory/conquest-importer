"""Input models for Azure action groups and alert processing rules.

Small and lenient on purpose: the export is read-only data from a service that adds fields, so
unknown fields are ignored and a null is an empty value. A resource that cannot be read becomes a
problem line (never the text of the resource) and the rest still loads.
"""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError, ValidationInfo, field_validator

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable

MAX_FILES = 5000
MAX_RESOURCES = 20000
RECEIVER_KINDS = (
    "emailReceivers",
    "smsReceivers",
    "voiceReceivers",
    "webhookReceivers",
    "itsmReceivers",
    "azureAppPushReceivers",
    "automationRunbookReceivers",
    "logicAppReceivers",
    "azureFunctionReceivers",
    "armRoleReceivers",
    "eventHubReceivers",
)
_NULLS: dict[str, object] = {"useAadAuth": False, "useCommonAlertSchema": None}


class Receiver(BaseModel):
    """One receiver of any kind; the fields a kind does not have stay empty."""

    model_config = ConfigDict(extra="ignore", coerce_numbers_to_str=True, hide_input_in_errors=True)

    kind: str = ""
    name: str = ""
    status: str = ""
    emailAddress: str = ""
    countryCode: str = ""
    phoneNumber: str = ""
    serviceUri: str = ""
    callbackUrl: str = ""
    httpTriggerUrl: str = ""
    useCommonAlertSchema: bool | None = None
    useAadAuth: bool = False
    workspaceId: str = ""
    connectionId: str = ""
    region: str = ""
    resourceId: str = ""
    functionName: str = ""
    runbookName: str = ""
    roleId: str = ""
    eventHubNameSpace: str = ""
    eventHubName: str = ""

    @field_validator("*", mode="before")
    @classmethod
    def _null_is_empty(cls, value: object, info: ValidationInfo) -> object:
        if value is None:
            return _NULLS.get(info.field_name or "", "")
        return value


class ActionGroup(BaseModel):
    id: str = ""
    name: str = ""
    short_name: str = ""
    enabled: bool = True
    receivers: list[Receiver] = []
    unknown_kinds: list[str] = []  # receiver arrays this reader does not know


class RuleCondition(BaseModel):
    field: str
    operator: str
    values: list[str] = []


class Recurrence(BaseModel):
    type: str
    start_time: str = ""
    end_time: str = ""
    days_of_week: list[str] = []
    days_of_month: list[int] = []


class RuleSchedule(BaseModel):
    effective_from: str = ""
    effective_until: str = ""
    time_zone: str = ""
    recurrences: list[Recurrence] = []


class RuleAction(BaseModel):
    type: str
    action_group_ids: list[str] = []


class ProcessingRule(BaseModel):
    id: str = ""
    name: str = ""
    enabled: bool = True
    description: str = ""
    legacy: bool = False  # the 2019 preview "action rules" API; only its name and state are read
    scopes: list[str] = []
    conditions: list[RuleCondition] = []
    schedule: RuleSchedule | None = None
    actions: list[RuleAction] = []


class AzureExport(BaseModel):
    action_groups: list[ActionGroup] = []
    rules: list[ProcessingRule] = []
    problems: list[str] = []


def _list(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _dicts(value: object) -> list[dict[str, Any]]:
    return [v for v in _list(value) if isinstance(v, dict)]


def _strings(value: object) -> list[str]:
    return [v for v in _list(value) if isinstance(v, str)]


def _props(raw: dict[str, Any]) -> dict[str, Any]:
    props = raw.get("properties")
    return props if isinstance(props, dict) else {}


def _last(resource_id: str) -> str:
    return resource_id.rstrip("/").rsplit("/", 1)[-1] if resource_id else ""


def _group(raw: dict[str, Any], hint: str) -> ActionGroup:
    props = _props(raw)
    receivers: list[Receiver] = []
    for kind in RECEIVER_KINDS:
        if props.get(kind) and not isinstance(props[kind], list):
            raise ValueError("a receiver list is not a list")
        receivers += [
            Receiver.model_validate({**item, "kind": kind}) for item in _dicts(props.get(kind))
        ]
    unknown = sorted(
        k for k, v in props.items() if k.endswith("Receivers") and k not in RECEIVER_KINDS and v
    )
    rid = str(raw.get("id") or "")
    enabled = props.get("enabled")
    return ActionGroup(
        id=rid,
        name=str(raw.get("name") or hint or _last(rid)),
        short_name=str(props.get("groupShortName") or ""),
        enabled=enabled if isinstance(enabled, bool) else True,
        receivers=receivers,
        unknown_kinds=unknown,
    )


def _recurrence(raw: dict[str, Any]) -> Recurrence:
    return Recurrence(
        type=str(raw.get("recurrenceType") or ""),
        start_time=str(raw.get("startTime") or ""),
        end_time=str(raw.get("endTime") or ""),
        days_of_week=_strings(raw.get("daysOfWeek")),
        days_of_month=[d for d in _list(raw.get("daysOfMonth")) if isinstance(d, int)],
    )


def _schedule(raw: object) -> RuleSchedule | None:
    if not isinstance(raw, dict):
        return None
    return RuleSchedule(
        effective_from=str(raw.get("effectiveFrom") or ""),
        effective_until=str(raw.get("effectiveUntil") or ""),
        time_zone=str(raw.get("timeZone") or ""),
        recurrences=[_recurrence(r) for r in _dicts(raw.get("recurrences"))],
    )


def _old_api(raw: dict[str, Any]) -> bool:
    return "scopes" not in _props(raw)


def _rule(raw: dict[str, Any], hint: str) -> ProcessingRule:
    props = _props(raw)
    rid = str(raw.get("id") or "")
    name = str(raw.get("name") or hint or _last(rid))
    if _old_api(raw):  # the 2019 preview API: scope, suppressionConfig, status
        return ProcessingRule(
            id=rid,
            name=name,
            legacy=True,
            enabled=props.get("status") != "Disabled",
            description=str(props.get("description") or ""),
        )
    enabled = props.get("enabled")
    return ProcessingRule(
        id=rid,
        name=name,
        enabled=enabled if isinstance(enabled, bool) else True,
        description=str(props.get("description") or ""),
        scopes=_strings(props.get("scopes")),
        conditions=[
            RuleCondition(
                field=str(c.get("field") or ""),
                operator=str(c.get("operator") or ""),
                values=_strings(c.get("values")),
            )
            for c in _dicts(props.get("conditions"))
        ],
        schedule=_schedule(props.get("schedule")),
        actions=[
            RuleAction(
                type=str(a.get("actionType") or ""),
                action_group_ids=_strings(a.get("actionGroupIds")),
            )
            for a in _dicts(props.get("actions"))
        ],
    )


_HINTS = {
    "actionGroup": "actionGroupName",
    "alertProcessingRule": "alertProcessingRuleName",
    "actionRule": "actionRuleName",
}


def _kind(raw: dict[str, Any]) -> str:
    """'group', 'rule' or '' for anything else (alert rules in the same folder are ignored).

    A resource that names its type is decided by the type alone: metric and activity log alert
    rules also have `scopes` and `actions`. An unnamed request body is decided by its shape.
    """
    props = _props(raw)
    kind = str(raw.get("type") or "").lower()
    if kind:
        if "actiongroups" in kind:
            return "group"
        return "rule" if "actionrules" in kind else ""
    if "groupShortName" in props or any(k.endswith("Receivers") for k in props):
        return "group"
    actions = props.get("actions")
    typed = isinstance(actions, list) and any(
        isinstance(a, dict) and "actionType" in a for a in actions
    )
    return "rule" if typed or "suppressionConfig" in props else ""


def _walk(doc: Any, hint: str = "") -> Iterator[tuple[str, dict[str, Any], str]]:
    """(kind, resource, name hint) for every action group or rule inside an example or response."""
    if isinstance(doc, list):
        for item in doc:
            yield from _walk(item, hint)
        return
    if not isinstance(doc, dict):
        return
    params = doc["parameters"] if isinstance(doc.get("parameters"), dict) else {}
    if isinstance(doc.get("responses"), dict):
        found = False
        for response in doc["responses"].values():
            body = response.get("body") if isinstance(response, dict) else None
            for item in _walk(body, hint):
                found = True
                yield item
        if found:
            return
    if params and "properties" not in doc:
        for key, name_key in _HINTS.items():
            if isinstance(params.get(key), dict):
                yield from _walk(params[key], str(params.get(name_key) or hint))
        return
    if isinstance(doc.get("value"), list):
        yield from _walk(doc["value"], hint)
        return
    kind = _kind(doc)
    if kind:
        yield kind, doc, hint


def _files(path: Path) -> list[Path]:
    if path.is_file():
        return [path]
    if path.is_dir():
        return sorted(p for p in path.rglob("*.json") if p.is_file())[:MAX_FILES]
    return []


def _name_of(raw: dict[str, Any], hint: str) -> str:
    return str(raw.get("name") or hint or _last(str(raw.get("id") or ""))).lower()


def load_export(path: Path) -> AzureExport:
    """Action groups and alert processing rules from a JSON file or a folder of them.

    The same resource found twice (several api versions of one example, or a list and a get) is
    kept once: by resource id, or by name when the copy has no id.
    """
    files = _files(path)
    if not files:
        raise ValueError(f"no JSON files found at {printable(str(path), 200)}")
    export = AzureExport()
    with_id: dict[tuple[str, str], tuple[str, dict[str, Any], str]] = {}
    without_id: list[tuple[str, dict[str, Any], str]] = []
    for file in files:
        label = printable(file.name, 80)
        if file.stat().st_size > MAX_INPUT_BYTES:
            export.problems.append(f"{label}: larger than the importer accepts")
            continue
        try:
            doc = json.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            export.problems.append(f"{label}: not valid JSON")
            continue
        for kind, raw, hint in _walk(doc):
            rid = str(raw.get("id") or "").lower()
            if rid:
                known = with_id.get((kind, rid))
                # the same rule from the retired 2019 API and the current one: keep the current
                if known is None or (kind == "rule" and _old_api(known[1]) and not _old_api(raw)):
                    with_id[(kind, rid)] = (kind, raw, hint)
            else:
                without_id.append((kind, raw, hint))
    items = list(with_id.values())
    seen = {(k, _name_of(r, h)) for k, r, h in items}
    for kind, raw, hint in without_id:
        key = (kind, _name_of(raw, hint))
        if key not in seen:
            seen.add(key)
            items.append((kind, raw, hint))
    for kind, raw, hint in items[:MAX_RESOURCES]:
        try:
            if kind == "group":
                export.action_groups.append(_group(raw, hint))
            else:
                export.rules.append(_rule(raw, hint))
        except (ValidationError, ValueError, TypeError):
            what = "action group" if kind == "group" else "alert processing rule"
            export.problems.append(f"an {what} could not be read and was left out")
    return export
