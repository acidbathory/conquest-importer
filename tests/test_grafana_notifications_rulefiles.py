"""Rule files: annotations are dropped, labels and expressions are checked for secrets."""

from importer.sources.grafana.notifications.models import PromRule, PromRuleGroup
from importer.sources.grafana.notifications.promrules import to_grafana_rule
from importer.sources.grafana.notifications.rulefiles import (
    N_EXPR_REDACTED,
    N_LABEL_REDACTED,
    PLACEHOLDER,
    as_ruler_group,
    load_rule_groups,
)

SECRET = "SECRETVALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests


def group_of(*rules: dict[str, object], labels: dict[str, str] | None = None) -> PromRuleGroup:
    raw: dict[str, object] = {"name": "g", "rules": list(rules)}
    if labels:
        raw["labels"] = labels
    (group,) = load_rule_groups({"ns": [raw]})
    return group


def test_annotations_are_not_read_at_all() -> None:
    group = group_of(
        {"alert": "A", "expr": "up == 0", "annotations": {"summary": SECRET, "runbook": "x"}}
    )
    assert "annotations" not in PromRule.model_fields
    assert SECRET not in group.model_dump_json()
    assert "annotations" not in as_ruler_group(group)["rules"][0]
    assert to_grafana_rule(group.rules[0], group, "u").annotations == {}


def test_a_label_value_that_looks_like_a_secret_is_replaced_with_a_note() -> None:
    group = group_of(
        {
            "alert": "A",
            "expr": "up == 0",
            "labels": {"team": "pay", "contact": "ops@example.com", "key": "token=abcdef123456"},
        },
        labels={"owner": "https://u:pw@example.com/x"},
    )
    (rule,) = group.rules
    assert rule.labels["team"] == "pay"
    assert rule.labels["contact"] == PLACEHOLDER and rule.labels["key"] == PLACEHOLDER
    assert rule.labels["owner"] == PLACEHOLDER
    assert N_LABEL_REDACTED in rule.notes
    assert "ops@example.com" not in group.model_dump_json() and "pw@" not in group.model_dump_json()


def test_an_expression_with_a_url_or_a_token_is_replaced_and_the_rule_stays_manual() -> None:
    group = group_of(
        {"alert": "A", "expr": 'probe{target="http://u:pw@internal.example.com"} == 0'},
        {"alert": "B", "expr": 'up{token="0123456789abcdef0123456789abcdef"} == 0'},
    )
    for rule in group.rules:
        assert rule.expr == PLACEHOLDER and N_EXPR_REDACTED in rule.notes
        assert to_grafana_rule(rule, group, "u").datasource_managed
    assert (
        "pw@" not in group.model_dump_json() and "0123456789abcdef" not in group.model_dump_json()
    )


def test_ordinary_expressions_and_labels_are_untouched() -> None:
    expr = 'sum by (job) (rate(http_requests_total{job="api",code=~"5.."}[5m])) > 5'
    group = group_of({"alert": "A", "expr": expr, "labels": {"severity": "critical"}})
    (rule,) = group.rules
    assert rule.expr == expr and rule.notes == [] and rule.labels == {"severity": "critical"}
