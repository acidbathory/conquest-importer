"""The offline PromQL-to-NRQL differential harness: generators, both models and the verdict ladder.

The harness supplies evidence only. Nothing here may change a converter confidence.
"""

import importlib.util
import math
import sys
from itertools import pairwise
from pathlib import Path
from types import ModuleType

import pytest

from importer.parity.diff_nrql import parse_nrql, run_nrql
from importer.parity.diff_promql import evaluate, parse_promql
from importer.parity.diff_series import SCENARIOS, DataSpec, Series, Unmodelled, generate

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "scripts" / "analysis" / "promql_diff_harness.py"
DOC = ROOT / "docs" / "research" / "promql-differential-results.md"


def load_harness() -> ModuleType:
    spec = importlib.util.spec_from_file_location("promql_diff_harness", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["promql_diff_harness"] = module
    spec.loader.exec_module(module)
    return module


def line(name: str, step: int, values: list[float], **labels: str) -> Series:
    pairs = tuple(sorted(labels.items()))
    return Series(name, pairs, tuple((step * (i + 1), v) for i, v in enumerate(values)))


# ---------------------------------------------------------------- generators


SPEC = DataSpec(metrics={"req_total": "counter", "mem": "gauge"}, labels={"code": ["200", "500"]})


def test_the_generator_is_deterministic_for_a_seed() -> None:
    assert generate(SPEC, "clean", 7) == generate(SPEC, "clean", 7)
    assert generate(SPEC, "clean", 7) != generate(SPEC, "clean", 8)


@pytest.mark.parametrize("scenario", SCENARIOS)
def test_every_scenario_makes_series_for_every_label_combination(scenario: str) -> None:
    made = generate(SPEC, scenario, 1)
    assert {(s.name, dict(s.labels)["code"]) for s in made} == {
        (m, c) for m in ("req_total", "mem") for c in ("200", "500")
    }


def test_the_reset_scenario_has_counter_drops_and_the_clean_one_has_none() -> None:
    def drops(scenario: str) -> int:
        counter = next(s for s in generate(SPEC, scenario, 1) if s.name == "req_total")
        values = [v for _, v in counter.samples]
        return sum(b < a for a, b in pairwise(values))

    assert drops("clean") == 0
    assert drops("resets") >= 2


def test_the_gap_scenario_has_missing_samples() -> None:
    clean = next(s for s in generate(SPEC, "clean", 1) if s.name == "mem")
    gappy = next(s for s in generate(SPEC, "gaps", 1) if s.name == "mem")
    assert len(gappy.samples) < len(clean.samples)


def test_histogram_buckets_are_cumulative_in_le() -> None:
    spec = DataSpec(metrics={"lat_bucket": "histogram"}, labels={})
    made = generate(spec, "clean", 1)
    by_le = {dict(s.labels)["le"]: s for s in made}
    assert set(by_le) == {"0.1", "0.5", "1", "+Inf"}
    last = [by_le[k].samples[-1][1] for k in ("0.1", "0.5", "1", "+Inf")]
    assert last == sorted(last)


# ---------------------------------------------------------------- PromQL reference


def counter(step: int, per_step: float, n: int = 40, **labels: str) -> Series:
    return line("c_total", step, [per_step * (i + 1) for i in range(n)], **labels)


def test_rate_of_a_steady_counter_is_the_slope() -> None:
    got = evaluate(parse_promql("rate(c_total[5m])"), [counter(15, 3.0)], 600)
    assert got[()] == pytest.approx(0.2, rel=1e-9)


def test_increase_is_rate_times_the_range() -> None:
    got = evaluate(parse_promql("increase(c_total[5m])"), [counter(15, 3.0)], 600)
    assert got[()] == pytest.approx(60.0, rel=1e-9)


def test_rate_adds_the_value_before_a_reset() -> None:
    values = [3.0 * (i + 1) for i in range(20)] + [3.0 * (i + 1) for i in range(20)]
    got = evaluate(parse_promql("rate(c_total[5m])"), [line("c_total", 15, values)], 600)
    assert got[()] == pytest.approx(0.2, rel=1e-6)


def test_rate_needs_two_samples() -> None:
    assert evaluate(parse_promql("rate(c_total[10s])"), [counter(15, 3.0)], 600) == {}


def test_aggregation_by_and_without() -> None:
    data = [counter(15, 3.0, a="1", b="x"), counter(15, 6.0, a="2", b="x")]
    by = evaluate(parse_promql("sum by (a) (rate(c_total[5m]))"), data, 600)
    assert by == {(("a", "1"),): pytest.approx(0.2), (("a", "2"),): pytest.approx(0.4)}
    allsum = evaluate(parse_promql("sum without (a) (rate(c_total[5m]))"), data, 600)
    assert allsum == {(("b", "x"),): pytest.approx(0.6)}
    assert evaluate(parse_promql("count(rate(c_total[5m]))"), data, 600)[()] == 2
    assert evaluate(parse_promql("max(rate(c_total[5m]))"), data, 600)[()] == pytest.approx(0.4)


def test_instant_selector_uses_the_five_minute_lookback() -> None:
    g = line("g", 60, [1.0, 2.0, 3.0])
    assert evaluate(parse_promql("g"), [g], 200)[()] == 3.0
    assert evaluate(parse_promql("g"), [g], 600) == {}


def test_comparison_filters_and_bool_marks() -> None:
    data = [line("g", 60, [5.0] * 5, k="a"), line("g", 60, [1.0] * 5, k="b")]
    assert set(evaluate(parse_promql("g > 2"), data, 300)) == {(("k", "a"),)}
    flags = evaluate(parse_promql("g > bool 2"), data, 300)
    assert flags == {(("k", "a"),): 1.0, (("k", "b"),): 0.0}


def test_arithmetic_matches_series_on_labels() -> None:
    data = [line("a", 60, [6.0] * 5, k="x"), line("b", 60, [2.0] * 5, k="x")]
    assert evaluate(parse_promql("a / b * 100 - 1"), data, 300) == {(("k", "x"),): 299.0}
    assert evaluate(parse_promql("-a"), data, 300) == {(("k", "x"),): -6.0}


def test_over_time_functions() -> None:
    g = line("g", 60, [1.0, 2.0, 3.0, 4.0, 5.0])
    for fn, want in (("avg", 4.0), ("max", 5.0), ("min", 3.0), ("sum", 12.0), ("count", 3.0)):
        got = evaluate(parse_promql(f"{fn}_over_time(g[3m])"), [g], 300)
        assert got[()] == pytest.approx(want), fn


def test_histogram_quantile_interpolates_inside_the_bucket() -> None:
    def bucket(le: str, per: float) -> Series:
        return line("h_bucket", 15, [per * (i + 1) for i in range(30)], le=le)

    data = [bucket("1", 50.0), bucket("2", 100.0), bucket("+Inf", 100.0)]
    text = "histogram_quantile(0.75, sum by (le) (rate(h_bucket[5m])))"
    got = evaluate(parse_promql(text), data, 400)
    assert got[()] == pytest.approx(1.5, rel=1e-6)


@pytest.mark.parametrize(
    "text", ["predict_linear(x[5m], 3)", "a and b", "x offset 5m", "rate(x[$__rate_interval])"]
)
def test_what_the_reference_cannot_read_is_reported_not_guessed(text: str) -> None:
    with pytest.raises(Unmodelled):
        parse_promql(text)


# ---------------------------------------------------------------- NRQL model


def test_nrql_sum_of_a_total_metric_is_the_sum_of_deltas() -> None:
    data = [counter(15, 3.0)]
    got = run_nrql(parse_nrql("SELECT sum(c_total) / 300 FROM Metric"), data, 600, 300)
    assert got[()] == pytest.approx(0.2, rel=1e-9)


def test_nrql_a_reset_stores_the_new_value_as_the_delta() -> None:
    values = [3.0, 6.0, 9.0, 3.0, 6.0]
    got = run_nrql(
        parse_nrql("SELECT sum(c_total) FROM Metric"), [line("c_total", 60, values)], 300, 300
    )
    assert got[()] == 3.0 + 3.0 + 3.0 + 3.0  # first sample has no delta; 9 to 3 stores 3


def test_nrql_gauge_aggregates_and_latest() -> None:
    g = line("g", 60, [1.0, 2.0, 3.0, 4.0])
    wanted = {
        "SELECT average(g) FROM Metric": 2.5,
        "SELECT max(g) FROM Metric": 4.0,
        "SELECT min(g) FROM Metric": 1.0,
        "SELECT latest(g) FROM Metric": 4.0,
        "SELECT sum(g) FROM Metric": 10.0,
    }
    for query, want in wanted.items():
        got = run_nrql(parse_nrql(query), [g], 240, 300)
        assert got[()] == pytest.approx(want), query


def test_nrql_facet_where_filter_and_rate_function() -> None:
    data = [counter(15, 3.0, code="500", s="a"), counter(15, 6.0, code="200", s="a")]
    q = "SELECT filter(sum(c_total), WHERE code = '500') / 300 FROM Metric FACET s"
    assert run_nrql(parse_nrql(q), data, 600, 300) == {(("s", "a"),): pytest.approx(0.2)}
    r = "SELECT rate(sum(c_total), 1 second) FROM Metric WHERE code = '200' TIMESERIES AUTO"
    assert run_nrql(parse_nrql(r), data, 600, 300)[()] == pytest.approx(0.4)


def test_nrql_division_by_nothing_gives_no_row() -> None:
    q = "SELECT sum(c_total) / sum(zzz_total) FROM Metric"
    assert run_nrql(parse_nrql(q), [counter(15, 3.0)], 600, 300) == {}


@pytest.mark.parametrize(
    "text",
    ["SELECT * FROM (SELECT latest(x) FROM Metric) WHERE x > 0", "SELECT if(a>1,1,0) FROM Metric"],
)
def test_nrql_forms_the_model_does_not_read_are_reported(text: str) -> None:
    with pytest.raises(Unmodelled):
        parse_nrql(text)


# ---------------------------------------------------------------- verdict ladder


@pytest.mark.parametrize(
    ("clean", "edges", "want"),
    [
        (0.0, [0.0, 0.0], "IDENTICAL"),
        (1e-12, [0.0], "IDENTICAL"),
        (0.02, [0.0], "EQUAL_WITHIN_TOLERANCE"),
        (0.0, [0.02, 0.04], "EQUAL_WITHIN_TOLERANCE"),
        (0.0, [0.3], "DIFFERS_AT_EDGES"),
        (0.0, [math.inf], "DIFFERS_AT_EDGES"),
        (0.2, [0.0], "WRONG"),
        (math.inf, [0.0], "WRONG"),
    ],
)
def test_the_verdict_ladder(clean: float, edges: list[float], want: str) -> None:
    assert load_harness().classify(clean, edges) == want


# ---------------------------------------------------------------- the cases


def results() -> dict[str, dict[str, object]]:
    got: dict[str, dict[str, object]] = load_harness().run_all()
    return got


LADDER = {"IDENTICAL", "EQUAL_WITHIN_TOLERANCE", "DIFFERS_AT_EDGES", "WRONG"}


def test_every_case_has_a_verdict_or_a_stated_reason_for_none() -> None:
    harness = load_harness()
    got = harness.run_all()
    ids = [c["id"] for c in harness.all_cases()]
    assert list(got) == ids
    for case_id, res in got.items():
        assert res["verdict"] in LADDER | {"NOT_MODELLED", "REFUSED"}, case_id
        if res["verdict"] in ("NOT_MODELLED", "REFUSED"):
            assert res["why"], case_id
        else:
            assert res["scenarios"], case_id


def test_the_run_is_reproducible() -> None:
    assert results() == results()


def test_the_harness_covers_the_listed_rule_families() -> None:
    harness = load_harness()
    modelled = {
        c["rule"] for c in harness.all_cases() if harness.run_all()[c["id"]]["verdict"] in LADDER
    }
    assert {"ARITH", "OVER-TIME", "GAUGE-SUM", "WITHOUT", "CMP-FILTER"} <= modelled


def test_the_output_never_claims_exactness_and_never_touches_confidence() -> None:
    text = load_harness().render_markdown(results())
    assert "exact" not in text.lower().replace("exactly", "")
    assert "evidence" in text.lower()
    assert "Confidence" not in SCRIPT.read_text().split("def render_markdown")[0].replace(
        "converter confidence", ""
    ).replace("monitor.confidence", "")


def test_the_results_document_is_the_harness_output() -> None:
    assert DOC.read_text() == load_harness().render_markdown(results())


def test_the_wrong_verdicts_are_exactly_the_known_findings() -> None:
    harness = load_harness()
    wrong = {k for k, v in results().items() if v["verdict"] == "WRONG"}
    assert wrong == set(harness.KNOWN_WRONG), "a new WRONG verdict is a finding: record it"


def _wrong_params() -> list[object]:
    harness = load_harness()
    return [
        pytest.param(
            case_id,
            marks=pytest.mark.xfail(strict=True, reason=f"{case_id}: {why}"),
            id=case_id,
        )
        for case_id, why in harness.KNOWN_WRONG.items()
    ]


@pytest.mark.parametrize("case_id", _wrong_params())
def test_a_rule_the_harness_found_wrong_should_agree_with_the_reference(case_id: str) -> None:
    assert results()[case_id]["verdict"] != "WRONG"
