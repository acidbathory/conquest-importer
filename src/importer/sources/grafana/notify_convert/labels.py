"""What the policy walk needs to know about each converted rule: its labels and label set.

The saved IR of a converted rule keeps its title, key and the labels of its query's grouping, but
not the rule's own labels or its folder. With the rules themselves (the file or folder that
`convert` read) the labels are known and a matcher on `severity` can be decided. Without them,
every label that the policy matches on and that is not `alertname` or a grouping label is marked
unknown, so the walk reports the rule as undecidable instead of reading the label as empty.
"""

from collections.abc import Sequence

from importer.core.ir import IRMonitor
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.notifications.models import PolicyNode, PolicyTree
from importer.sources.grafana.notifications.policies import parse_node
from importer.sources.grafana.notifications.policy import RuleLabels

UNKNOWN = "{{unknown}}"  # a value the policy walk treats as a template: not decidable


def policy_label_names(tree: PolicyTree) -> set[str]:
    """Every label name that a matcher of the tree names. The tree is already bounded."""
    names: set[str] = set()
    stack: list[PolicyNode] = [tree.root]
    while stack:
        node = stack.pop()
        names.update(m.name for m in node.matchers)
        stack.extend(node.routes)
    return names


def rule_keys(rules: Sequence[GrafanaRule]) -> list[str]:
    """The key `translate` gives each rule (uid, or folder/group/title, with `#n` for repeats)."""
    seen: dict[str, int] = {}
    keys: list[str] = []
    for rule in rules:
        base = rule.uid or f"{rule.folder}/{rule.group}/{rule.title}"
        seen[base] = seen.get(base, 0) + 1
        keys.append(base if seen[base] == 1 else f"{base}#{seen[base]}")
    return keys


def query_labels(monitor: IRMonitor) -> frozenset[str] | None:
    """The labels the rule's query groups by; None when it does not group (the set is unknown:
    a bare selector carries every label of the series)."""
    signal = monitor.signal
    grouped = (
        signal.metric.group_by
        if signal.metric
        else signal.count.group_by
        if signal.count
        else signal.heartbeat.group_by
        if signal.heartbeat
        else []
    )
    return frozenset(grouped) if grouped else None


def _own_route(settings: object) -> PolicyNode | None:
    """The rule's simplified routing as a policy node, read like a policy route (so a receiver
    that looks like an address gets the same stable reference as in the policy). Grafana spells
    the timing lists `mute_time_intervals` and `active_time_intervals`; the provisioning API of
    some versions says `mute_timings` and `active_timings`."""
    if not isinstance(settings, dict):
        return None
    raw = dict(settings)
    for old, new in (
        ("mute_timings", "mute_time_intervals"),
        ("active_timings", "active_time_intervals"),
    ):
        if old in raw and new not in raw:
            raw[new] = raw[old]
    if not isinstance(raw.get("receiver"), str) or not raw["receiver"]:
        return None
    return parse_node(raw)


def rule_labels(monitor: IRMonitor, rule: GrafanaRule | None, wanted: set[str]) -> RuleLabels:
    by = query_labels(monitor)
    if rule is None:
        unknown = {n: UNKNOWN for n in wanted if n != "alertname" and n not in (by or ())}
        return RuleLabels(name=monitor.name, labels=unknown, by_labels=by)
    titled = rule.shape == "export"  # the folder is its title only in the export shape
    labels = dict(rule.labels)
    if not titled:
        labels["grafana_folder"] = UNKNOWN
    settings = rule.notification_settings
    own = _own_route(settings)
    receiver = own.receiver if own is not None else None
    return RuleLabels(
        name=monitor.name,
        labels=labels,
        folder=rule.folder if titled else "",
        by_labels=by,
        receiver=receiver or None,
        settings=own,
        uid=rule.uid,
        namespace_uid="" if titled else rule.folder,
    )
