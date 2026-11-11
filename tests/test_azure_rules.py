"""Every Azure reason is categorised, every manual reason has steps, rules are anchored."""

from pathlib import Path

from azure_helpers import log_rule, make, metric_alert
from importer.core import remediation
from importer.core.ir import Confidence, IRMonitor
from importer.core.playbook import all_playbooks
from importer.core.remediation import remedies_for
from importer.core.taxonomy import Category, classify
from importer.sources import REGISTRY
from importer.sources.azure.translate import translate

FIX = Path(__file__).parent / "fixtures" / "azure"


def everything() -> list[IRMonitor]:
    monitors = REGISTRY["azure"].convert(FIX)
    extra = [
        metric_alert(windowSize="PT2H"),
        metric_alert(windowSize="PT30S"),
        metric_alert(
            scopes=["/subscriptions/s/resourceGroups/a", "/subscriptions/s/resourceGroups/b"]
        ),
        metric_alert(
            autoMitigate=False,
            enabled=False,
            severity=9,
            evaluationFrequency="PT1M",
            windowSize="PT15M",
        ),
        log_rule("T | join U on a"),
        log_rule('T | where A contains "x"', operator="LessThan"),
        log_rule(
            "T", failingPeriods={"numberOfEvaluationPeriods": 4, "minFailingPeriodsToAlert": 2}
        ),
        make("scheduledQueryRules", {}, kind="LogToMetric"),
        make("scheduledQueryRules", {}),
        make("smartDetectorAlertRules", {"detector": {"id": "x"}}),
        make("activityLogAlerts", {}),
        make(
            "prometheusRuleGroups", {"rules": [{"alert": "A"}, {"record": "r", "expression": "x"}]}
        ),
    ]
    return monitors + translate(extra)


def test_every_reason_has_a_category() -> None:
    for m in everything():
        for reason in m.reasons:
            assert classify(reason) is not Category.OTHER, reason


def test_every_manual_monitor_has_a_remediation_rule() -> None:
    for m in everything():
        if m.confidence is Confidence.MANUAL:
            assert remedies_for(m.reasons) or m.remediation, m.reasons


def test_azure_rules_are_anchored_and_have_playbooks() -> None:
    azure = [(p, t) for p, t, _ in remediation._COMPILED if t.startswith("Azure")]
    assert len(azure) >= 10
    books = all_playbooks()
    for pattern, title in azure:
        assert pattern.pattern.startswith("^"), title
        assert title in books, title


def test_the_dynamic_threshold_reason_is_a_feature_gap_and_the_name_note_is_unverified() -> None:
    assert (
        classify("dynamic threshold: Azure learns the normal range") is Category.UNSUPPORTED_FEATURE
    )
    note = "metric name azure.x.Y is derived from the documented naming rule and is UNVERIFIED"
    assert classify(note) is Category.UNVERIFIED_NAMES
