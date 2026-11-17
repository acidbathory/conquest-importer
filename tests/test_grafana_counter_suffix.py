"""A metric name ending _counter is assumed to be a counter (delta count), with a note."""

import importlib
import json
from pathlib import Path

import pytest

from importer.core.ir import Confidence, IRMonitor
from test_grafana import make, one

t = importlib.import_module("importer.sources.grafana.translate")


def convert(expr: str, instant: bool = False) -> IRMonitor:
    return one(make(expr, params=[2], for_=600, instant=instant))


@pytest.mark.parametrize(
    "expr",
    [
        "sum(rate(orders_counter[5m]))",
        "sum(increase(orders_counter[5m]))",
        "sum(rate(a_counter[5m])) / sum(rate(b_counter[5m]))",
        "sum(rate(a_counter[5m])) / sum(rate(b_total[5m]))",
    ],
)
@pytest.mark.parametrize("instant", [False, True])
def test_counter_suffix_converts_with_the_note(expr: str, instant: bool) -> None:
    m = convert(expr, instant)
    assert m.confidence is Confidence.APPROXIMATE
    assert any("name suffix _counter" in r for r in m.reasons)


def test_a_total_only_rate_carries_no_suffix_note() -> None:
    m = convert("sum(rate(orders_total[5m]))")
    assert not any("name suffix _counter" in r for r in m.reasons)


@pytest.mark.parametrize("fn", ["rate", "increase"])
def test_a_gauge_hint_wins_over_the_suffix(
    fn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "map.json"
    path.write_text(json.dumps({"types": {"orders_counter": "gauge"}}))
    monkeypatch.setenv("IMPORTER_GRAFANA_NAME_MAP", str(path))
    assert convert(f"sum({fn}(orders_counter[5m]))").confidence is Confidence.MANUAL


@pytest.mark.parametrize("name", ["orders_requests", "orders_bytes"])
def test_other_names_stay_manual(name: str) -> None:
    assert convert(f"sum(rate({name}[5m]))").confidence is Confidence.MANUAL


def test_a_plain_selector_is_unchanged() -> None:
    assert convert("avg(orders_counter)").confidence is not Confidence.MANUAL
