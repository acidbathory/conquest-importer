"""Google PromQL alert conditions convert through the Grafana PromQL translator."""

import json
from pathlib import Path

from importer.core.ir import Confidence, IRMonitor, Operator, Severity, SignalKind
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy

FIXTURES = Path(__file__).parent / "fixtures" / "gcp-promql"


def promql(query: str, **body: object) -> IRMonitor:
    policy = GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "severity": body.pop("severity", ""),
            "documentation": {"content": "doc text"},
            "conditions": [
                {
                    "displayName": "c",
                    "conditionPrometheusQueryLanguage": {
                        "query": query,
                        "duration": "300s",
                        "evaluationInterval": "60s",
                        **body,
                    },
                }
            ],
        }
    )
    return translate_policy(policy)[0]


def test_a_gauge_selector_with_a_threshold_converts() -> None:
    m = promql("avg(node_load1) > 4")
    assert m.confidence is Confidence.APPROXIMATE
    assert m.source_system == "gcp"
    assert m.signal.kind is SignalKind.METRIC
    assert m.signal.metric is not None
    assert m.thresholds[0].operator is Operator.ABOVE
    assert m.thresholds[0].value == 4
    assert m.signal.window_seconds == 300
    assert m.signal.aggregation_window_seconds == 60
    assert m.notification.message_template == "doc text"


def test_the_prometheus_name_note_and_remote_write_are_stated() -> None:
    m = promql("avg(node_load1) > 4")
    assert any("remote write" in r for r in m.reasons)
    assert not any("grafana" in r.lower() for r in m.reasons)


def test_quoted_utf8_selector_and_monitored_resource_are_rewritten() -> None:
    m = promql('avg({"node_load1", monitored_resource="gce_instance", job="a"}) > 4')
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.metric is not None
    scope = [(f.key, f.value) for f in m.signal.metric.scope]
    assert scope == [("job", "a")]
    assert any("monitored_resource" in r for r in m.reasons)


def test_dotted_metric_names_get_the_prometheus_spelling() -> None:
    m = promql('avg({"workload.googleapis.com/jvm.memory.heap.used"}) > 4')
    assert m.signal.metric is not None
    term = m.signal.metric.terms["a"]
    assert term.metric == "workload_googleapis_com_jvm_memory_heap_used"
    assert any("workload.googleapis.com/jvm.memory.heap.used" in r for r in m.reasons)


def test_comments_are_removed() -> None:
    m = promql('avg(node_load1{\n  # only prod\n  env="prod"\n}) > 4')
    assert m.confidence is Confidence.APPROXIMATE


def test_template_placeholders_stay_manual_with_the_reason() -> None:
    m = promql('avg(node_load1{cluster=~"${CLUSTER}"}) > 4')
    assert m.confidence is Confidence.MANUAL
    assert "placeholder" in m.reasons[0]


def test_unsafe_shapes_are_refused_with_the_grafana_reason() -> None:
    m = promql("max(node_load1 offset 1h) > 4")
    assert m.confidence is Confidence.MANUAL
    assert m.signal.kind is SignalKind.METRIC
    assert m.signal.metric is None


def test_policy_severity_labels_group_and_validation_flag() -> None:
    m = promql(
        "avg(node_load1) > 4",
        severity="WARNING",
        labels={"team": "db"},
        ruleGroup="grp",
        alertRule="rule1",
        disableMetricValidation=True,
    )
    assert m.thresholds[0].severity is Severity.WARNING
    assert any("grp" in r and "team" in r for r in m.reasons)
    assert "gcp:rule-group:grp" in m.tags
    assert any("disableMetricValidation" in r for r in m.reasons)


def test_a_missing_interval_is_stated() -> None:
    m = promql("avg(node_load1) > 4", evaluationInterval="")
    assert any("evaluation interval" in r for r in m.reasons)


def load_fixtures() -> list[IRMonitor]:
    out: list[IRMonitor] = []
    for path in sorted(FIXTURES.glob("*.json")):
        out += translate_policy(GcpAlertPolicy.model_validate(json.loads(path.read_text())))
    return out


def real(m: IRMonitor) -> bool:
    """A converted monitor with a real query behind it; a placeholder does not count."""
    s = m.signal
    if m.confidence is Confidence.MANUAL:
        return False
    return bool(s.metric and s.metric.terms) or s.heartbeat is not None or s.count is not None


def test_sample_policies_before_and_after() -> None:
    """Before this adapter 0 of the 84 samples converted; the rest stay manual with a reason."""
    monitors = load_fixtures()
    assert len(monitors) == 84
    converted = [m for m in monitors if real(m)]
    assert len(converted) == 2
    for m in monitors:
        if not real(m):
            assert m.confidence is Confidence.MANUAL
            assert "not converted" not in m.reasons[0]
    placeholders = [m for m in monitors if "placeholder" in m.reasons[0]]
    assert len(placeholders) == 23
    assert all(not real(m) for m in placeholders)


def test_every_refusal_has_cookbook_steps() -> None:
    from importer.core.remediation import steps_for

    for m in load_fixtures():
        if not real(m):
            assert steps_for(m.reasons), m.reasons[0]
