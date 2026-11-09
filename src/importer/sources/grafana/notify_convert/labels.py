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
    receiver = settings.get("receiver") if isinstance(settings, dict) else None
    return RuleLabels(
        name=monitor.name,
        labels=labels,
        folder=rule.folder if titled else "",
        by_labels=by,
        receiver=receiver if isinstance(receiver, str) and receiver else None,
        uid=rule.uid,
        namespace_uid="" if titled else rule.folder,
    )
