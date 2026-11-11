"""Alert rules read the same PromQL spellings as dashboards: comments, quote styles, case."""

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from test_grafana import make, one

PLAIN = 'avg by (a) (cpu{job="x"})'


@pytest.mark.parametrize(
    "variant",
    [
        "avg by (a) (cpu{job='x'})",
        "avg by (a) (cpu{job=`x`})",
        'AVG BY (a) (cpu{job="x"})',
        'avg(cpu{job="x"}) BY (a)',
        'avg by (a) (cpu{job="x"}) # why this one',
        'avg by (a) (cpu{job="x"}) # why\n',
    ],
)
def test_every_spelling_converts_like_the_plain_one(variant: str) -> None:
    plain, other = one(make(PLAIN)), one(make(variant))
    assert other.confidence is Confidence.APPROXIMATE, other.reasons
    assert nrql_for(other) == nrql_for(plain)


def test_a_hash_inside_a_string_is_a_value_not_a_comment() -> None:
    assert "a#b" in nrql_for(one(make("cpu{job='a#b'}")))


def test_a_function_in_the_wrong_case_stays_manual() -> None:
    assert one(make("AVG(RATE(x_total[5m]))")).confidence is Confidence.MANUAL
