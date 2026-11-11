"""Estate conditions the converter keeps manual: each reason names its cause and gets its own steps.

Nothing here converts a condition; a value that only a person knows (a `${NAME}` placeholder, a
system label whose New Relic attribute is unconfirmed) is never guessed. What these tests pin is
that the reason says what is missing and that the playbook groups it under the right title.
"""

from typing import Any

import pytest

from importer.core.ir import Confidence, IRMonitor
from importer.core.remediation import remedies_for
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy

METRIC = 'metric.type="compute.googleapis.com/instance/cpu/utilization"'


def convert(kind: str, body: dict[str, Any]) -> IRMonitor:
    policy = GcpAlertPolicy.model_validate(
        {"displayName": "p", "combiner": "OR", "conditions": [{"displayName": "c", kind: body}]}
    )
    (monitor,) = translate_policy(policy)
    return monitor


def threshold(filter_text: str) -> IRMonitor:
    return convert(
        "conditionThreshold",
        {
            "filter": filter_text,
            "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
            "comparison": "COMPARISON_GT",
            "thresholdValue": 0.8,
            "duration": "600s",
        },
    )


def logs(filter_text: str) -> IRMonitor:
    return convert("conditionMatchedLog", {"filter": filter_text})


def promql(query: str) -> IRMonitor:
    return convert(
        "conditionPrometheusQueryLanguage",
        {"query": query, "duration": "300s", "evaluationInterval": "60s"},
    )


def title_of(monitor: IRMonitor) -> str:
    assert monitor.confidence is Confidence.MANUAL
    return remedies_for(monitor.reasons)[0].title


def test_promql_placeholder_reason_names_every_placeholder() -> None:
    m = promql('sum(rate(http_requests_total{cluster=~"${CLUSTER_NAME}", pod="${POD}"}[5m])) > 1')
    assert m.confidence is Confidence.MANUAL
    assert "${CLUSTER_NAME}" in m.reasons[0] and "${POD}" in m.reasons[0]
    (remedy,) = remedies_for(m.reasons)
    assert remedy.title == "PromQL query with a template placeholder"
    assert any("variables" in step for step in remedy.steps)


def test_promql_placeholder_that_is_the_whole_query_is_manual() -> None:
    m = promql("${QUERY}")
    assert m.confidence is Confidence.MANUAL and "${QUERY}" in m.reasons[0]


CASES = [
    pytest.param(
        lambda: threshold(
            f'resource.type="gce_instance" AND {METRIC} AND '
            'metadata.system_labels.name = starts_with("prod-")'
        ),
        "System label filter",
        id="system-label-starts-with",
    ),
    pytest.param(
        lambda: threshold(f'{METRIC} AND metadata.system_labels.state = "unack"'),
        "System label filter",
        id="system-label-equals",
    ),
    pytest.param(
        lambda: threshold(f'{METRIC} AND select_slo_health("projects/p/services/s/slos/x")'),
        "SLO selector",
        id="slo-selector",
    ),
    pytest.param(
        lambda: threshold(
            f"{METRIC} AND metric.labels.service = "
            'monitoring.regex.full_match("${SERVICES_REGEX}")'
        ),
        "Filter value is a template placeholder",
        id="threshold-regex-placeholder",
    ),
    pytest.param(
        lambda: logs('protoPayload.resourceName =~ "/${EVALUATION_NAME}/"'),
        "Filter value is a template placeholder",
        id="log-regex-placeholder",
    ),
    pytest.param(
        lambda: logs('textPayload=~"timeout after [0-9]+ms"'),
        "Regular expression filter",
        id="log-regex-class",
    ),
    pytest.param(
        lambda: logs('jsonPayload.message =~ "a.*b" OR jsonPayload.message =~ "c.*d"'),
        "Filter with an OR",
        id="log-or-of-patterns",
    ),
    pytest.param(
        lambda: threshold('metric.type="logging.googleapis.com/user/my_errors"'),
        "Metric New Relic does not collect from Google Cloud",
        id="log-based-metric-is-not-a-filter",
    ),
]


@pytest.mark.parametrize(("make", "title"), CASES)
def test_manual_filter_reasons_get_their_own_title_and_steps(make: Any, title: str) -> None:
    monitor = make()
    assert title_of(monitor) == title
    assert remedies_for(monitor.reasons)[0].steps


def test_system_label_reason_says_the_attribute_is_unconfirmed() -> None:
    m = threshold(f'{METRIC} AND metadata.system_labels.name = starts_with("prod-")')
    assert "metadata.system_labels.name" in m.reasons[0]
    assert "no confirmed New Relic attribute" in m.reasons[0]
