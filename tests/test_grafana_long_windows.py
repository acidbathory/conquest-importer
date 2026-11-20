"""Aggregation windows from one to six hours (the limit New Relic accepted live on 2026-11-11)."""

import importlib

import pytest

from importer.core.emit.nerdgraph import nerdgraph_payload
from importer.core.ir import Confidence
from test_grafana import make, one, text

t = importlib.import_module("importer.sources.grafana.translate")


@pytest.mark.parametrize("seconds", [7200, 10800, 21600])
def test_a_window_up_to_six_hours_converts_with_a_note(seconds: int) -> None:
    m = one(make("cpu", reducer="max", time_range=seconds, for_=0))
    assert m.confidence is Confidence.APPROXIMATE, m.reasons
    assert m.signal.aggregation_window_seconds == seconds
    assert t.N_LONG_WINDOW.format(window=seconds) in m.reasons
    payload = nerdgraph_payload(m)
    assert payload["variables"]["condition"]["signal"]["aggregationWindow"] == seconds


def test_a_window_of_an_hour_or_less_has_no_long_window_note() -> None:
    m = one(make("cpu", reducer="max", time_range=3600, for_=0))
    assert not any("full" in r and "window" in r and "edit" in r for r in m.reasons)


def test_a_window_over_six_hours_stays_manual() -> None:
    m = one(make("cpu", reducer="max", time_range=43200, for_=0))
    assert m.reasons == [t.R_AGG_WINDOW.format(seconds=43200, limit=21600)]
    assert "grafana aggregation window" in text(m)
