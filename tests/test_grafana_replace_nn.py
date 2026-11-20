"""replaceNN with 0 converts only where a 0 can never fire the rule (invented data)."""

import importlib
from typing import Any

import pytest

from importer.core.ir import Confidence
from test_grafana import make, one, text

t = importlib.import_module("importer.sources.grafana.translate")
ZERO = {"mode": "replaceNN", "replaceWithValue": 0}


@pytest.mark.parametrize("value", [0, 0.5, 80])
def test_a_zero_for_missing_values_under_an_above_threshold_converts_with_a_note(
    value: float,
) -> None:
    m = one(make("cpu", kind="gt", params=[value], reduce_settings=ZERO))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert t.N_REPLACE_ZERO in m.reasons


@pytest.mark.parametrize(
    ("kind", "value"),
    [("gt", -1), ("gte", 0), ("lt", 5), ("lte", 0), ("eq", 0), ("ne", 3)],
)
def test_a_zero_that_could_fire_or_hold_the_rule_stays_manual(kind: str, value: float) -> None:
    m = one(make("cpu", kind=kind, params=[value], reduce_settings=ZERO))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0] == t.R_REDUCE_MODE


@pytest.mark.parametrize(
    "settings",
    [
        {"mode": "replaceNN", "replaceWithValue": 1},
        {"mode": "replaceNN", "replaceWithValue": -1},
        {"mode": "replaceNN"},
        {"mode": "replaceNN", "replaceWithValue": "0"},
        {"mode": "replaceNN", "replaceWithValue": False},
    ],
)
def test_other_replacement_values_stay_manual(settings: dict[str, Any]) -> None:
    m = one(make("cpu", kind="gt", params=[5], reduce_settings=settings))
    assert m.confidence is Confidence.MANUAL and m.reasons[0] == t.R_REDUCE_MODE


def test_a_recovery_threshold_keeps_the_rule_manual() -> None:
    # a 0 would satisfy a recovery threshold below a positive value and resolve the alert early
    rule = make("cpu", kind="gt", params=[5], reduce_settings=ZERO)
    rule.data[-1].model["conditions"][0]["unloadEvaluator"] = {"type": "lt", "params": [1]}
    m = one(rule)
    assert m.confidence is Confidence.MANUAL and m.reasons[0] == t.R_REDUCE_MODE
    assert "grafana reduce mode" in text(m)
