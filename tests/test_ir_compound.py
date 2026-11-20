"""The optional compound field of IRMonitor: validated, and absent from every other monitor."""

import pytest
from pydantic import ValidationError

from importer.core.ir import CompoundSpec, Confidence, IRMonitor, Signal, SignalKind

KEYS = ["k/and-A", "k/and-B", "k/and-C", "k/and-D", "k/and-E", "k/and-F"]


@pytest.mark.parametrize(
    ("trigger", "count"),
    [
        ("A AND B", 2),
        ("(A AND B)", 2),
        ("A OR B", 2),
        ("A AND NOT B", 2),
        ("((A AND B) OR C)", 3),
        ("(A AND B AND C AND D AND E)", 5),
        ("NOT (A OR B)", 2),
    ],
)
def test_valid_triggers_are_accepted(trigger: str, count: int) -> None:
    spec = CompoundSpec(trigger=trigger, components=KEYS[:count])
    assert spec.components == KEYS[:count]


@pytest.mark.parametrize(
    ("trigger", "count", "message"),
    [
        ("A", 1, "2 to 5 components"),
        ("A AND B AND C AND D AND E AND F", 6, "2 to 5 components"),
        ("a AND b", 2, "only A to E"),
        ("A XOR B", 2, "only A to E"),
        ("A && B", 2, "only A to E"),
        ("A AND B; DROP", 2, "only A to E"),
        ("A AND", 2, "well-formed"),
        ("A B", 2, "well-formed"),
        ("(A AND B", 2, "well-formed"),
        ("A AND B)", 2, "well-formed"),
        ("A AND B", 3, "one alias per component"),
        ("A AND F", 2, "one alias per component"),
        ("B AND C", 2, "one alias per component"),
    ],
)
def test_invalid_specs_are_refused(trigger: str, count: int, message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        CompoundSpec(trigger=trigger, components=KEYS[:count])


def test_a_component_named_twice_is_refused() -> None:
    with pytest.raises(ValidationError, match="each component once"):
        CompoundSpec(trigger="A AND B", components=["k", "k"])


@pytest.mark.parametrize("field", ["facets", "threshold_duration_seconds"])
def test_the_spec_has_no_facet_or_duration_setting(field: str) -> None:
    assert field not in CompoundSpec.model_fields


def test_a_trigger_that_is_too_long_is_refused() -> None:
    with pytest.raises(ValidationError, match="longer than"):
        CompoundSpec(trigger="(" * 150 + "A AND B" + ")" * 150, components=KEYS[:2])


def monitor(compound: CompoundSpec | None) -> IRMonitor:
    return IRMonitor(
        source_system="invented",
        source_id=1,
        source_type="rule",
        name="n",
        signal=Signal(kind=SignalKind.COMPOSITE, raw_query="q"),
        confidence=Confidence.APPROXIMATE,
        compound=compound,
    )


def test_a_monitor_without_compound_keeps_its_saved_shape() -> None:
    assert "compound" not in monitor(None).model_dump()
    assert "compound" not in monitor(None).model_dump_json()


def test_a_compound_monitor_round_trips() -> None:
    spec = CompoundSpec(trigger="(A OR B)", components=KEYS[:2])
    saved = monitor(spec).model_dump_json()
    assert IRMonitor.model_validate_json(saved).compound == spec
