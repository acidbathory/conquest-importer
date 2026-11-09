"""Resample modes: only a mean (or a last with reducer last) is reproduced by a New Relic window."""

import importlib
from typing import Any

import pytest

from importer.core.ir import Confidence
from importer.sources.grafana.models import GrafanaRule
from test_grafana import make, one
from test_grafana_rules2_pipeline import converted, expr_node, with_resample

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def resampled(
    downsampler: str = "mean", upsampler: str = "fillna", window: str = "1m", reducer: str = "mean"
) -> GrafanaRule:
    rule = with_resample(make("avg(cpu)", reducer=reducer))
    rule.data[1].model.update(downsampler=downsampler, upsampler=upsampler, window=window)
    return rule


@pytest.mark.parametrize(
    ("downsampler", "reducer"), [("mean", "mean"), ("mean", "last"), ("last", "last")]
)
def test_the_resample_modes_a_window_reproduces_convert(downsampler: str, reducer: str) -> None:
    converted(resampled(downsampler=downsampler, reducer=reducer))


@pytest.mark.parametrize(
    ("downsampler", "reducer"),
    [
        ("sum", "mean"),
        ("max", "mean"),
        ("min", "mean"),
        ("max", "max"),
        ("last", "mean"),
        ("mean", "max"),
        ("mean", "sum"),
        ("", "mean"),
        ("garbage", "mean"),
    ],
)
def test_other_downsamplers_are_manual_with_a_resample_reason(
    downsampler: str, reducer: str
) -> None:
    assert one(resampled(downsampler=downsampler, reducer=reducer)).reasons == [t.R_RESAMPLE_MODE]


@pytest.mark.parametrize("upsampler", ["pad", "backfilling", "garbage"])
def test_an_upsampler_that_fills_values_is_manual(upsampler: str) -> None:
    assert one(resampled(upsampler=upsampler)).reasons == [t.R_RESAMPLE_MODE]


@pytest.mark.parametrize("window", ["", "garbage", "0s", "-1m", "1x", "5"])
def test_a_resample_window_that_is_not_a_duration_is_manual(window: str) -> None:
    assert one(resampled(window=window)).reasons == [t.R_RESAMPLE_MODE]


def test_a_resample_after_the_reduce_is_manual() -> None:
    """query A -> reduce B -> resample R -> threshold C: the resample acts on a number."""
    rule = make("avg(cpu)", reducer="mean")
    model: dict[str, Any] = {
        "type": "resample",
        "expression": "B",
        "window": "1m",
        "downsampler": "mean",
        "upsampler": "fillna",
    }
    rule.data.insert(2, expr_node("R", model))
    rule.data[3].model["expression"] = "R"
    m = one(rule)
    assert m.confidence is Confidence.MANUAL and m.reasons == [t.R_SHAPE]
