"""Grafana alert rules: one internal model, filled by one normaliser per read path.

Grafana spells the same rule four ways (provisioning list, export, ruler, /apis). Each normaliser
below turns one spelling into `GrafanaRule`; everything after this module sees only that model.
Unknown fields are kept (extra="allow"). Rules in the export and ruler shapes inherit folder,
group and interval from the group they sit in.

Shapes follow docs/research/grafana-native-coverage.md. The provisioning and export shapes were
checked against a live stack (Grafana 13.4); the ruler and /apis shapes were not.
"""

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from importer.core.ids import MAX_INPUT_BYTES

MAX_FILES = 10_000
MAX_RULES = 50_000
MAX_DEPTH = 6
DURATION_PART = re.compile(r"(\d+)(ms|s|m|h|d|w|y)")
UNIT_SECONDS = {"ms": 0, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800, "y": 31536000}
STATES = {  # lower case spelling -> the name Grafana documents
    "nodata": "NoData",
    "ok": "OK",
    "normal": "OK",
    "alerting": "Alerting",
    "keeplast": "KeepLast",
    "error": "Error",
}
SHAPE_ERROR = (
    "expected Grafana alert rules: a list of rules, an export with groups, a ruler response, "
    "or an /apis rule list"
)


class GfNode(BaseModel):
    """One query or expression of a rule (an item of `data`)."""

    model_config = ConfigDict(extra="allow")

    refId: str = ""
    datasourceUid: str = ""
    queryType: str = ""
    relativeTimeRange: dict[str, Any] = {}
    model: dict[str, Any] = {}


class GrafanaRule(BaseModel):
    model_config = ConfigDict(extra="allow")

    title: str
    uid: str = ""
    folder: str = ""  # a folder UID (provisioning, ruler) or title (export); whichever was read
    group: str = ""
    interval_seconds: int | None = None  # evaluation interval; not in the provisioning list
    for_seconds: int = 0
    keep_firing_for_seconds: int = 0
    labels: dict[str, Any] = {}
    annotations: dict[str, Any] = {}
    no_data_state: str = ""
    exec_err_state: str = ""
    is_paused: bool = False
    condition: str = ""
    data: list[GfNode] = []
    record: dict[str, Any] | None = None
    notification_settings: dict[str, Any] | None = None
    datasource_managed: bool = False
    shape: str = ""


def parse_duration(value: Any) -> int:
    """Seconds in a Prometheus-style duration ("5m", "1h30m"), a number of seconds, or "" / None."""
    if value is None or value == "":
        return 0
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    if isinstance(value, str):
        parts = list(DURATION_PART.finditer(value))
        if parts and "".join(p[0] for p in parts) == value:
            return sum(int(p[1]) * UNIT_SECONDS[p[2]] for p in parts)
    raise ValueError("a duration in a rule is not like 5m, 1h30m or a number of seconds")


def _pick(raw: dict[str, Any], *names: str) -> Any:
    """The first of several spellings of a field that is present."""
    return next((raw[n] for n in names if n in raw), None)


def _state(value: Any) -> str:
    return STATES.get(str(value or "").replace("_", "").lower(), "")


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _nodes(raw: Any) -> list[GfNode]:
    if isinstance(raw, dict):  # /apis: a map keyed by refId
        items = [{**_mapping(v), "refId": k} for k, v in raw.items()]
    else:
        items = [i for i in raw if isinstance(i, dict)] if isinstance(raw, list) else []
    for item in items:  # /apis spells it datasourceUID
        if "datasourceUid" not in item and "datasourceUID" in item:
            item["datasourceUid"] = item.pop("datasourceUID")
    return [GfNode.model_validate(i) for i in items]


class _Context(BaseModel):
    """What a rule inherits from its group."""

    folder: str = ""
    group: str = ""
    interval: Any = None


def _common(raw: dict[str, Any], ctx: _Context, shape: str) -> dict[str, Any]:
    """Fields every spelling carries once the field names are unified."""
    return {
        "title": str(_pick(raw, "title", "alert", "record") or ""),
        "uid": str(raw.get("uid") or ""),
        "folder": ctx.folder,
        "group": ctx.group,
        "interval_seconds": parse_duration(ctx.interval) or None,
        "for_seconds": parse_duration(raw.get("for")),
        "keep_firing_for_seconds": parse_duration(_pick(raw, "keep_firing_for", "keepFiringFor")),
        "labels": _mapping(raw.get("labels")),
        "annotations": _mapping(raw.get("annotations")),
        "no_data_state": _state(_pick(raw, "noDataState", "no_data_state")),
        "exec_err_state": _state(_pick(raw, "execErrState", "exec_err_state")),
        "is_paused": bool(_pick(raw, "isPaused", "is_paused", "paused")),
        "condition": str(raw.get("condition") or ""),
        "data": _nodes(raw.get("data")),
        "notification_settings": _pick(raw, "notification_settings", "notificationSettings"),
        "record": _pick(raw, "record") if isinstance(raw.get("record"), dict) else None,
        "shape": shape,
    }


def _provisioning(raw: dict[str, Any], ctx: _Context) -> GrafanaRule:
    here = _Context(
        folder=str(raw.get("folderUID") or ctx.folder),
        group=str(raw.get("ruleGroup") or ctx.group),
        interval=ctx.interval,
    )
    return GrafanaRule.model_validate(_common(raw, here, "provisioning"))


def _export(raw: dict[str, Any], ctx: _Context) -> GrafanaRule:
    return GrafanaRule.model_validate(_common(raw, ctx, "export"))


def _ruler(raw: dict[str, Any], ctx: _Context) -> GrafanaRule:
    inner = _mapping(raw.get("grafana_alert"))
    merged = {**inner, **{k: v for k, v in raw.items() if k != "grafana_alert"}}
    merged["title"] = inner.get("title", "")
    merged["condition"] = inner.get("condition", "")
    here = _Context(
        folder=str(inner.get("namespace_uid") or ctx.folder),
        group=str(inner.get("rule_group") or ctx.group),
        interval=inner.get("intervalSeconds") or ctx.interval,
    )
    return GrafanaRule.model_validate(_common(merged, here, "ruler"))


def _ruler_datasource_rule(raw: dict[str, Any], ctx: _Context) -> GrafanaRule:
    """A rule the Mimir or Loki ruler evaluates; it has an `expr`, not a Grafana pipeline."""
    fields = _common(raw, ctx, "ruler")
    return GrafanaRule.model_validate({**fields, "datasource_managed": True})


def _apis(raw: dict[str, Any], ctx: _Context) -> GrafanaRule:
    spec = _mapping(raw.get("spec"))
    meta = _mapping(raw.get("metadata"))
    expressions = _mapping(spec.get("expressions"))
    source = next((k for k, v in expressions.items() if _mapping(v).get("source")), "")
    here = _Context(
        folder=str(_mapping(meta.get("annotations")).get("grafana.app/folder") or ctx.folder),
        group=ctx.group,
        interval=_mapping(spec.get("trigger")).get("interval") or ctx.interval,
    )
    body = {
        **spec,
        "uid": meta.get("name", ""),
        "data": spec.get("expressions"),
        "condition": source,
    }
    return GrafanaRule.model_validate(_common(body, here, "apis"))


# a rule item: first predicate that holds names its normaliser
RULE_SHAPES: list[tuple[Callable[[dict[str, Any]], bool], Callable[..., GrafanaRule]]] = [
    (lambda r: "grafana_alert" in r, _ruler),
    (lambda r: "spec" in r and "metadata" in r, _apis),
    (lambda r: "expr" in r and ("alert" in r or "record" in r), _ruler_datasource_rule),
    (lambda r: "ruleGroup" in r or "folderUID" in r, _provisioning),
    (lambda r: "title" in r, _export),
]


def _rule(raw: Any, ctx: _Context) -> GrafanaRule:
    if isinstance(raw, dict):
        for matches, normalise in RULE_SHAPES:
            if matches(raw):
                return normalise(raw, ctx)
    raise ValueError(SHAPE_ERROR)


def _group_rules(group: dict[str, Any], folder: str) -> list[GrafanaRule]:
    ctx = _Context(
        folder=str(group.get("folder") or folder),
        group=str(group.get("name") or ""),
        interval=group.get("interval"),
    )
    return [_rule(r, ctx) for r in _list(group.get("rules"))]


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _collect(data: Any, folder: str = "", depth: int = 0) -> list[GrafanaRule]:
    """Rules in any of the accepted payloads; `depth` bounds the recursion."""
    if depth > MAX_DEPTH:
        raise ValueError(SHAPE_ERROR)
    if isinstance(data, list):
        return [r for item in data for r in _collect_item(item, folder, depth)]
    if isinstance(data, dict):
        return _collect_item(data, folder, depth)
    raise ValueError(SHAPE_ERROR)


def _collect_item(item: Any, folder: str, depth: int) -> list[GrafanaRule]:
    if not isinstance(item, dict):
        raise ValueError(SHAPE_ERROR)
    if isinstance(item.get("groups"), list):  # export envelope
        return [r for g in item["groups"] for r in _group_rules(_mapping(g), folder)]
    if isinstance(item.get("rules"), list) and "name" in item:  # one group
        return _group_rules(item, folder)
    if isinstance(item.get("items"), list):  # /apis list
        return [_rule(i, _Context()) for i in item["items"]]
    if any(k in item for k in ("data", "grafana_alert", "spec", "expr")):
        return [_rule(item, _Context(folder=folder))]
    values = list(item.values())
    if values and all(isinstance(v, list) for v in values):  # ruler: namespace -> groups
        return [r for ns, groups in item.items() for r in _collect(groups, str(ns), depth + 1)]
    raise ValueError(SHAPE_ERROR)


def _files(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path]
    # the files here and in the folder `extract-grafana` writes; not alerts/, which holds IR
    found = sorted(f for folder in (path, path / "alert-rules") for f in folder.glob("*.json"))
    if len(found) > MAX_FILES:
        raise ValueError(f"{path} has more than {MAX_FILES} JSON files; pass a smaller folder")
    return found


def load_rules(path: Path) -> list[GrafanaRule]:
    """Read alert rules from a file, an API response or a folder; the same uid is kept once."""
    rules: list[GrafanaRule] = []
    seen: set[str] = set()
    for file in _files(path):
        if file.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{file} is larger than {MAX_INPUT_BYTES // (1024 * 1024)} MB")
        for rule in _collect(json.loads(file.read_text())):
            if rule.uid:
                if rule.uid in seen:
                    continue
                seen.add(rule.uid)
            rules.append(rule)
        if len(rules) > MAX_RULES:
            raise ValueError(f"more than {MAX_RULES} alert rules; pass a smaller input")
    return rules
