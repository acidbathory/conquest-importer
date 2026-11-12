"""A per-series equality test becomes a faceted condition on the labels the rule's own message
names; a rule that names none stays refused."""

import importlib

import pytest

from grafana_rules2_eval import evaluate
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from test_grafana_rules2_round2 import file_rule

t = importlib.import_module("importer.sources.grafana.translate")


def ann(text: str) -> dict[str, object]:
    return {"annotations": {"summary": text}}


def test_an_equality_test_facets_by_the_labels_the_message_names() -> None:
    m = file_rule("svc_up == 0", **ann("down {{ $labels.instance }} in {{ $labels.region }}"))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.metric and m.signal.metric.group_by == ["instance", "region"]
    assert nrql_for(m).endswith("FACET instance, region")
    assert t.N_FACET_IDENTITY.format(labels="instance, region") in m.reasons
    assert t.N_ANY_SERIES not in m.reasons


def test_a_not_equals_test_facets_too_and_each_series_is_tested_on_its_own() -> None:
    m = file_rule("svc_status != 1", **ann("{{ $labels.instance }}"))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.metric and m.signal.metric.group_by == ["instance"]
    rows = [
        {"svc_status": 1.0, "instance": "a"},
        {"svc_status": 0.0, "instance": "b"},
    ]
    assert evaluate(nrql_for(m), rows) == {("a",): 1.0, ("b",): 0.0}


def test_a_rule_that_names_no_label_stays_refused() -> None:
    m = file_rule("svc_up == 0", **ann("service is down"))
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_PER_SERIES_TEST]


@pytest.mark.parametrize("text", ["{{ $labels }}", "{{ $labels.a-b }}", "{{ $labels.__name__ }}"])
def test_only_plain_label_identifiers_are_used(text: str) -> None:
    m = file_rule("svc_up == 0", **ann(text))
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_PER_SERIES_TEST]


def test_the_description_is_read_too_and_names_are_deduplicated() -> None:
    m = file_rule(
        "svc_up == 0",
        annotations={
            "summary": "{{ $labels.instance }}",
            "description": "{{ $labels.name }} {{ $labels.instance }}",
        },
    )
    assert m.signal.metric and m.signal.metric.group_by == ["instance", "name"]


def test_the_above_test_keeps_the_maximum_without_facets() -> None:
    m = file_rule("queue_depth > 100", **ann("{{ $labels.instance }}"))
    assert m.signal.metric and m.signal.metric.group_by == []


def test_a_counter_rate_without_sum_facets_by_the_labels_the_message_names() -> None:
    m = file_rule(
        "rate(jobs_failed_total[5m]) > 0.1", **ann("{{ $labels.instance }} {{ $labels.queue }}")
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.metric and m.signal.metric.group_by == ["instance", "queue"]
    assert nrql_for(m).endswith("FACET instance, queue")
    assert t.N_FACET_IDENTITY.format(labels="instance, queue") in m.reasons
    rows = [
        {"jobs_failed_total": 60.0, "instance": "a", "queue": "x"},
        {"jobs_failed_total": 3.0, "instance": "b", "queue": "x"},
    ]
    # 60 / 300s = 0.2 fires for a; 3 / 300s = 0.01 does not for b: each facet on its own
    assert evaluate(nrql_for(m), rows) == {("a", "x"): 0.2, ("b", "x"): 0.01}


def test_an_increase_without_sum_facets_too() -> None:
    m = file_rule("increase(jobs_failed_total[5m]) > 0", **ann("{{ $labels.instance }}"))
    assert m.confidence is Confidence.APPROXIMATE
    assert m.signal.metric and m.signal.metric.group_by == ["instance"]


def test_a_counter_rate_without_labels_stays_refused() -> None:
    m = file_rule("rate(jobs_failed_total[5m]) > 0.1", **ann("jobs are failing"))
    assert m.reasons == [t.R_COUNTER_UNAGG]


def test_a_rate_of_a_metric_not_known_as_a_counter_stays_refused() -> None:
    m = file_rule("rate(jobs_failed[5m]) > 0.1", **ann("{{ $labels.instance }}"))
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_COUNTER_NAME]
