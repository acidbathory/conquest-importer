"""Prometheus, Mimir and Loki rule groups, as models the Grafana translator can be fed.

PyYAML is not a dependency of the importer, so a `rules.yml` file is not read: the loader
accepts JSON (`yq -o=json rules.yml`, the ruler API, `mimirtool rules print -o json`) and says
so. Group labels are merged into each rule (the rule's own label wins), as Prometheus does.
One bad rule is counted and skipped; it does not stop the group.
"""

import json
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable
from importer.sources.grafana.models import parse_duration
from importer.sources.grafana.notifications.models import (
    MAX_NAME,
    MAX_RULE_GROUPS,
    MAX_RULES,
    RULE_FILE_FORMAT_HELP,
    PromRule,
    PromRuleGroup,
    as_dict,
    as_list,
)

MAX_EXPR = 100_000
MAX_TEXT = 4_000
MAX_LABELS = 200
R_GROUP_UNREADABLE = "grafana rule file group could not be read"


def _text_map(value: Any) -> dict[str, str]:
    pairs = list(as_dict(value).items())[:MAX_LABELS]
    return {printable(k, MAX_NAME): printable(v, MAX_TEXT) for k, v in pairs if isinstance(v, str)}


def _expr(value: Any) -> str | None:
    if isinstance(value, int | float) and not isinstance(value, bool):
        return str(value)
    return value[:MAX_EXPR] if isinstance(value, str) and value.strip() else None


def _rule(raw: Any, group_labels: dict[str, str]) -> PromRule | None:
    entry = as_dict(raw)
    alert, record = entry.get("alert"), entry.get("record")
    name = alert if isinstance(alert, str) and alert else record
    expr = _expr(entry.get("expr"))
    if not isinstance(name, str) or not name or expr is None:
        return None
    try:
        for_seconds = parse_duration(entry.get("for"))
        keep = parse_duration(entry.get("keep_firing_for"))
    except ValueError:
        return None
    return PromRule(
        kind="alert" if isinstance(alert, str) and alert else "record",
        name=printable(name, MAX_NAME),
        expr=expr,
        for_seconds=for_seconds,
        keep_firing_for_seconds=keep,
        labels={**group_labels, **_text_map(entry.get("labels"))},
        annotations=_text_map(entry.get("annotations")),
    )


def _group(raw: Any, namespace: str, kind: str, budget: list[int]) -> PromRuleGroup | None:
    entry = as_dict(raw)
    name = entry.get("name")
    if not isinstance(name, str) or not name:
        return None
    try:
        interval = parse_duration(entry.get("interval")) or None
    except ValueError:
        interval = None
    labels = _text_map(entry.get("labels"))
    group = PromRuleGroup(
        name=printable(name, MAX_NAME),
        namespace=printable(namespace, MAX_NAME),
        interval_seconds=interval,
        kind=kind,
    )
    for item in as_list(entry.get("rules")):
        if budget[0] <= 0:
            group.skipped += 1
            continue
        budget[0] -= 1
        rule = _rule(item, labels)
        if rule is None:
            group.skipped += 1
        else:
            group.rules.append(rule)
    return group


def _group_items(data: Any) -> list[tuple[str, Any]]:
    """(namespace, raw group) pairs from any accepted shape."""
    body = as_dict(data)
    if isinstance(body.get("data"), dict):  # the Prometheus rules API: {status, data: {groups}}
        body = body["data"]
    if isinstance(data, list):
        return [("", g) for g in data]
    if isinstance(body.get("groups"), list):
        return [
            (printable(str(g.get("file", "")), MAX_NAME) if isinstance(g, dict) else "", g)
            for g in body["groups"]
        ]
    # the ruler listing: {namespace: [groups]}
    return [(ns, g) for ns, groups in body.items() if isinstance(groups, list) for g in groups]


def load_rule_groups(data: Any, kind: str = "prometheus") -> list[PromRuleGroup]:
    """Rule groups from parsed JSON. Raises ValueError when nothing here looks like a group."""
    items = _group_items(data)[:MAX_RULE_GROUPS]
    budget = [MAX_RULES]
    groups = [g for ns, raw in items if (g := _group(raw, ns, kind, budget)) is not None]
    if items and not groups:
        raise ValueError(R_GROUP_UNREADABLE)
    return groups


def load_rule_file(path: Path, kind: str = "prometheus") -> list[PromRuleGroup]:
    """Read a JSON rule file. YAML is refused with the fix in the message."""
    if path.suffix.lower() in (".yml", ".yaml"):
        raise ValueError(RULE_FILE_FORMAT_HELP)
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError(f"{path.name} is larger than the importer accepts")
    try:
        return load_rule_groups(json.loads(path.read_text()), kind)
    except RecursionError:
        raise ValueError("a rule file is nested too deeply to read") from None
    except json.JSONDecodeError:
        raise ValueError(f"{path.name} is not JSON. {RULE_FILE_FORMAT_HELP}") from None


def as_ruler_group(group: PromRuleGroup) -> dict[str, Any]:
    """The group in the ruler shape the Grafana loader reads (`folder` is the namespace)."""
    rules = []
    for r in group.rules:
        body: dict[str, Any] = {"expr": r.expr, "labels": r.labels, "annotations": r.annotations}
        body["alert" if r.kind == "alert" else "record"] = r.name
        if r.for_seconds:
            body["for"] = f"{r.for_seconds}s"
        if r.keep_firing_for_seconds:
            body["keep_firing_for"] = f"{r.keep_firing_for_seconds}s"
        rules.append(body)
    out: dict[str, Any] = {"name": group.name, "folder": group.namespace, "rules": rules}
    if group.interval_seconds:
        out["interval"] = f"{group.interval_seconds}s"
    return out
