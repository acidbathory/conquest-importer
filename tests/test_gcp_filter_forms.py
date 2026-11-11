"""Filter forms seen in Google's sample policies: grouping, one_of, log_id, quoted label keys."""

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from importer.sources.gcp.filter import FilterError, parse_filter
from importer.sources.gcp.translate import translate_policy
from test_gcp import policy, threshold

CPU = "compute.googleapis.com/instance/cpu/utilization"


def test_a_group_of_and_terms_is_flattened() -> None:
    terms = parse_filter('metric.type = "a" AND (x.y = "1" AND z = "2")')
    assert [t.field for t in terms] == ["metric.type", "x.y", "z"]


@pytest.mark.parametrize(
    "text",
    [
        '(a = "1" AND b = "2"',
        'a = "1") AND b = "2"',
        'a = "1" AND (b = "2" OR c = "3")',
        'NOT (a = "1")',
    ],
)
def test_unbalanced_or_negated_groups_are_refused(text: str) -> None:
    with pytest.raises(FilterError):
        parse_filter(text)


def test_one_of_gives_a_list_of_values() -> None:
    (term,) = parse_filter('resource.labels.log_type != one_of("A", "B")')
    assert term.values == ("A", "B") and term.op == "!="


def test_a_quoted_label_key_is_joined_to_its_parent() -> None:
    (term,) = parse_filter('labels."compute.googleapis.com/resource_name"="vm-1"')
    assert term.field == "labels.compute.googleapis.com/resource_name" and term.value == "vm-1"


def test_log_id_is_its_own_term() -> None:
    (term,) = parse_filter('log_id("cloudaudit.googleapis.com/system_event")')
    assert (term.field, term.op, term.value) == (
        "log_id",
        "()",
        "cloudaudit.googleapis.com/system_event",
    )


def test_metric_labels_with_one_of_become_in_lists() -> None:
    (m,) = translate_policy(
        policy(
            threshold(
                filter=f'metric.type="{CPU}" AND resource.labels.zone != one_of("a", "b") '
                'AND (metric.labels.x = "1" AND metric.labels.y = "2")'
            )
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert "gcp.zone NOT IN ('a', 'b')" in nrql_for(m)
    assert "metric.x = '1' AND metric.y = '2'" in nrql_for(m)


def test_log_id_and_a_slash_key_become_log_filters() -> None:
    (m,) = translate_policy(
        policy(
            {
                "conditionMatchedLog": {
                    "filter": 'log_id("cloudaudit.googleapis.com/system_event") AND '
                    'labels."compute.googleapis.com/resource_name"="vm-1"'
                }
            }
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert "logName LIKE '%/logs/cloudaudit.googleapis.com%system_event'" in nrql_for(m)
    assert "`labels.compute.googleapis.com/resource_name` = 'vm-1'" in nrql_for(m)


def test_an_unusable_log_field_is_manual_not_a_late_crash() -> None:
    (m,) = translate_policy(policy({"conditionMatchedLog": {"filter": 'jsonPayload.a b="1"'}}))
    assert m.confidence is Confidence.MANUAL
