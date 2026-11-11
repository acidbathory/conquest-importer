"""Estate conditions the converter keeps manual: each reason names its cause and gets its own steps.

Nothing here converts a condition; a value that only a person knows (a `${NAME}` placeholder, a
system label whose New Relic attribute is unconfirmed) is never guessed. What these tests pin is
that the reason says what is missing and that the playbook groups it under the right title.
"""

import json
from pathlib import Path
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


def test_an_or_of_metric_types_is_not_filed_under_filter_with_an_or() -> None:
    m = threshold(
        'metric.type = "compute.googleapis.com/instance/cpu/utilization" OR '
        'metric.type = "compute.googleapis.com/instance/disk/read_bytes_count"'
    )
    assert "an OR of several metric types" in m.reasons[0]
    assert title_of(m) != "Filter with an OR"


def test_a_prefixed_filter_reason_keeps_its_title() -> None:
    """A ratio denominator reason is the filter reason with a prefix; it must still match."""
    m = convert(
        "conditionThreshold",
        {
            "filter": f'{METRIC} AND resource.type="gce_instance"',
            "denominatorFilter": f'{METRIC} AND metric.labels.code > "3"',
            "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}],
            "denominatorAggregations": [
                {"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_RATE"}
            ],
            "comparison": "COMPARISON_GT",
            "thresholdValue": 0.8,
            "duration": "600s",
        },
    )
    assert m.reasons[0].startswith("ratio denominator: filter term")
    assert title_of(m) == "Filter not supported"


ESTATE = Path(__file__).parent / "fixtures" / "gcp-estate" / "policies"


def test_no_filter_reason_of_the_estate_lacks_a_playbook_title() -> None:
    """Every manual estate condition refused for its filter (also with a prefix, such as a
    ratio denominator) gets a playbook title; none falls to the default."""
    seen = 0
    for path in sorted(ESTATE.glob("*/*.json")):
        policy = GcpAlertPolicy.model_validate(json.loads(path.read_text()))
        for monitor in translate_policy(policy):
            if monitor.confidence is Confidence.MANUAL and "filter" in monitor.reasons[0]:
                seen += 1
                assert remedies_for(monitor.reasons), monitor.reasons
    assert seen >= 15


def test_a_hostile_placeholder_name_never_reaches_the_reason() -> None:
    m = promql('sum(up{job="${é1}", c="${evil name}", d="${OK_1}"}) > 1')
    assert m.confidence is Confidence.MANUAL
    assert "${OK_1}" in m.reasons[0]
    assert all(ord(c) < 128 for c in m.reasons[0])
    assert "evil" not in m.reasons[0]
