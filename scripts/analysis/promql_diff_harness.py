"""Offline differential harness: does the NRQL we emit compute what the PromQL computes?

Usage:
    uv run python scripts/analysis/promql_diff_harness.py [--json OUT] [--markdown OUT]

For each case of `promql_rule_cases.json` (and `promql_diff_cases.json`) the converter runs
offline, the PromQL is evaluated by our reference (`importer.parity.diff_promql`) and the emitted
NRQL by our model of its semantics (`importer.parity.diff_nrql`), both on seeded synthetic series
(`importer.parity.diff_series`). Scenarios: clean, resets, gaps, volatile, misaligned.

Verdict ladder, from the worst divergence (relative, 5 percent tolerance) per scenario:
    IDENTICAL               every scenario within 1e-9
    EQUAL_WITHIN_TOLERANCE  every scenario within tolerance
    DIFFERS_AT_EDGES        clean data agrees, an edge scenario (resets, gaps, volatile data,
                            window alignment) is outside tolerance
    WRONG                   clean data is outside tolerance, or one side has a group the other lacks
Cases the readers cannot model are NOT_MODELLED and refused conversions are REFUSED; neither is a
verdict. The output is evidence about our reading of both languages, never a proof, and it sets
no converter state: it only prints a suggested level.
"""

import argparse
import importlib.util
import json
import re
import sys
from functools import cache
from pathlib import Path
from typing import Any

from importer.parity.diff_nrql import Query, facet_names, parse_nrql, run_nrql, where_attrs
from importer.parity.diff_promql import (
    Node,
    evaluate,
    first_range,
    label_names,
    metric_names,
    parse_promql,
    walk,
)
from importer.parity.diff_series import SCENARIOS, DataSpec, Unmodelled, Vector, generate

HERE = Path(__file__).resolve().parent
EXTRA_CASES = HERE / "promql_diff_cases.json"
DOC = HERE.parent.parent / "docs" / "research" / "promql-differential-results.md"
TOLERANCE = 0.05
EPSILON = 1e-9
SAME = 1e-9
SEEDS = (1, 2, 3)
TIMES = (6300, 6600, 6900)
MISALIGN = (37, 13, 53)
SUGGEST = {
    "IDENTICAL": "HIGH",
    "EQUAL_WITHIN_TOLERANCE": "HIGH",
    "DIFFERS_AT_EDGES": "MEDIUM",
    "WRONG": "MANUAL",
}

# Rules the harness found WRONG on clean data. Each has a failing test marked xfail; the rule is
# left as it is until a person decides (rule 6 of docs/dev/parallel-work.md and the brief).
KNOWN_WRONG: dict[str, str] = {
    "DIFF-GAUGE-D03": (
        "a Prometheus gauge whose name ends in _count (`pg_stat_activity_count`) is stored as a "
        "delta count by the name-suffix rule, so `average()` of it is the average of differences. "
        "Same as finding 2 of telemetry-rig-results.md. The real fix is on the data path, not the "
        "rule: relabel the metric to a gauge type (`newrelic_metric_type=gauge` write relabel), "
        "which the rig showed restores the gauge. The rule was not changed."
    ),
}


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, HERE / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def all_cases() -> list[dict[str, Any]]:
    runner = _load("promql_rule_runner")
    base: list[dict[str, Any]] = runner.load_cases()
    return base + json.loads(EXTRA_CASES.read_text())["cases"]


def classify(
    clean: float, edges: list[float], clean_missing: bool = False, edge_missing: bool = False
) -> str:
    """The rung of the ladder for the worst divergence per scenario.

    This is a statement about our two readings, not about Prometheus or New Relic: the series
    generator, the PromQL reference and the NRQL model share one author and one set of assumptions,
    and the model stores `_total/_count/_sum/_bucket` as deltas by the rig's name-suffix rule.
    WRONG therefore cannot exceed what the rig already found, and IDENTICAL or
    EQUAL_WITHIN_TOLERANCE only shows that summed deltas equal an extrapolated rate under that
    rule. A missing group (one side has a group the other lacks) is not a wrong value: on clean
    data it is WRONG, under an edge scenario it is DIFFERS_AT_EDGES. Rules that differ only at
    edges are suggested MEDIUM at most.
    """
    if clean > TOLERANCE or clean_missing:
        return "WRONG"
    worst = max([clean, *edges])
    if edge_missing or worst > TOLERANCE:
        return "DIFFERS_AT_EDGES"
    return "IDENTICAL" if worst <= SAME else "EQUAL_WITHIN_TOLERANCE"


def divergence(prom: Vector, nrql: Vector) -> tuple[float, bool]:
    """(worst relative difference over the groups both sides have, a group is missing on a side).

    Each group is judged on its own size; only a difference below EPSILON counts as none, so a
    small group cannot hide behind a large one.
    """
    worst = 0.0
    for key in prom.keys() & nrql.keys():
        a, b = prom[key], nrql[key]
        gap = abs(a - b)
        if gap > EPSILON:
            worst = max(worst, gap / max(abs(a), abs(b)))
    return worst, prom.keys() != nrql.keys()


def project(vector: Vector, labels: list[str]) -> Vector | None:
    """Keep only the labels the NRQL facets on; None when that merges two series."""
    out: Vector = {}
    for key, value in vector.items():
        short = tuple((k, v) for k, v in key if k in labels)
        if short in out:
            return None
        out[short] = value
    return out


def _literals(pattern: str) -> list[str]:
    """The plain values a matcher names: `a`, or the alternatives of `a|b`; else none."""
    parts = pattern.split("|")
    return parts if all(p.replace("-", "").replace("_", "").isalnum() for p in parts) else []


def _spec(prom: Node, query: Query, types: dict[str, str]) -> DataSpec:
    """`types` is the real Prometheus type of a metric when its name says otherwise."""
    kinds: dict[str, str] = {}
    quantile = any(n.kind == "call" and n.name == "histogram_quantile" for n in walk(prom))
    for n in walk(prom):
        if n.kind == "call" and n.name in ("rate", "irate", "increase") and n.args[0].kind == "sel":
            kinds[n.args[0].name] = "counter"
    for name in metric_names(prom):
        if quantile and name.endswith("_bucket"):
            kinds[name] = "histogram"
        else:
            kinds.setdefault(
                name, "counter" if name.endswith(("_total", "_count", "_sum")) else "gauge"
            )
    kinds.update({k: v for k, v in types.items() if k in kinds})
    values: dict[str, list[str]] = {}
    for n in walk(prom):
        for label, op, want in n.matchers:
            vals = values.setdefault(label, [])
            if op in ("=", "!=", "=~"):
                vals.extend(v for v in _literals(want) if v not in vals)
    for label in [*label_names(prom), *facet_names(query)]:
        values.setdefault(label, [])
    out: dict[str, list[str]] = {}
    for label, vals in sorted(values.items()):
        if label == "le":
            continue
        out[label] = [*vals, "other"][:2] if vals else ["a", "b"]
    return DataSpec(metrics=kinds, labels=out)


def _alert_side(prom: Node, query: Query, surface: str) -> tuple[Node, str]:
    """The part of the alert PromQL whose value the NRQL computes (the threshold is separate)."""
    node, mode = prom, "full"
    compares = node.kind == "bin" and node.name in ("==", "!=", ">", "<", ">=", "<=")
    if surface != "dashboard" and compares and not where_attrs(query) & set(metric_names(prom)):
        node, mode = node.args[0], "value, threshold applied by the alert condition"
    if node.kind == "agg" and node.name in ("topk", "bottomk"):
        node, mode = node.args[1], "value, topk is a threshold filter"
    return node, mode


def _window(converted: dict[str, Any], query: Query, prom: Node) -> int:
    """An alert evaluates its aggregation window; a chart evaluates one TIMESERIES bucket."""
    return int(converted.get("window_seconds") or query.bucket or first_range(prom) or 300)


def _scenario(
    prom: Node, query: Query, data_spec: DataSpec, window: int, name: str
) -> tuple[float, bool]:
    """Worst value divergence and whether any group was missing, over seeds, times and shifts."""
    worst, missing = 0.0, False
    shifts = MISALIGN if name == "misaligned" else (0,)
    for seed in SEEDS:
        data = generate(data_spec, name, seed)
        for base in TIMES:
            for shift in shifts:
                left = evaluate(prom, data, base + shift)
                right = run_nrql(query, data, base, window)
                merged = project(left, facet_names(query))
                if merged is None:
                    missing = True
                    continue
                if not merged and not right:
                    continue
                value, gone = divergence(merged, right)
                worst, missing = max(worst, value), missing or gone
    return worst, missing


def run_case(case: dict[str, Any], runner: Any) -> dict[str, Any]:
    converted = runner.run_case(case)
    if not converted["converted"]:
        return {"verdict": "REFUSED", "why": converted.get("reason", "refused"), "scenarios": {}}
    nrql_text: str = converted["nrql"]
    base: dict[str, Any] = {"nrql": nrql_text, "scenarios": {}}
    try:
        prom = parse_promql(_fill(case["promql"], case.get("args", {})))
        query = parse_nrql(nrql_text)
        side, mode = _alert_side(prom, query, case["surface"])
        spec = _spec(side, query, case.get("types", {}))
        window = _window(converted, query, side)
        scenarios = {s: _scenario(side, query, spec, window, s) for s in SCENARIOS}
    except Unmodelled as stop:
        return {**base, "verdict": "NOT_MODELLED", "why": str(stop)}
    if not any(
        evaluate(side, generate(spec, "clean", 1), t)
        or run_nrql(query, generate(spec, "clean", 1), t, window)
        for t in TIMES
    ):
        return {**base, "verdict": "NOT_MODELLED", "why": "both sides returned no data"}
    clean, clean_missing = scenarios["clean"]
    others = [v for k, v in scenarios.items() if k != "clean"]
    verdict = classify(clean, [v for v, _ in others], clean_missing, any(m for _, m in others))
    return {
        **base,
        "verdict": verdict,
        "why": "",
        "mode": mode,
        "window": window,
        "scenarios": {
            k: {"value": _round(v), "missing_group": m} for k, (v, m) in scenarios.items()
        },
        "suggested": SUGGEST[verdict],
    }


def _fill(text: str, args: dict[str, Any]) -> str:
    """Put the case's variable defaults into `${NAME}` placeholders, as the converter was told to."""
    values = args.get("hints", {}).get("variables", {})
    return re.sub(r"\$\{(\w+)\}", lambda m: str(values.get(m.group(1), m.group(0))), text)


def _round(value: float) -> float:
    return round(value, 6)


@cache
def run_all() -> dict[str, dict[str, Any]]:
    runner = _load("promql_rule_runner")
    return {c["id"]: run_case(c, runner) for c in all_cases()}


LADDER = (
    "IDENTICAL",
    "EQUAL_WITHIN_TOLERANCE",
    "DIFFERS_AT_EDGES",
    "WRONG",
    "NOT_MODELLED",
    "REFUSED",
)


def render_markdown(results: dict[str, dict[str, Any]]) -> str:
    cases = {c["id"]: c for c in all_cases()}
    lines = [
        "# PromQL to NRQL: offline differential results",
        "",
        "Generated by `scripts/analysis/promql_diff_harness.py`; do not edit by hand (a test checks it).",
        "",
        "This is evidence, not proof, and it is circular by construction. The series generator, the",
        "PromQL reference and the NRQL model share one author and one set of assumptions. The NRQL",
        "model's storage rule is the rig's name-suffix rule (`_total`, `_count`, `_sum`, `_bucket` are",
        "stored as deltas), so a WRONG verdict cannot exceed what the rig already found, and",
        "IDENTICAL or EQUAL_WITHIN_TOLERANCE only shows that summed deltas equal an extrapolated rate",
        "under that rule. Both sides are our own readings: a PromQL reference evaluator",
        "(`src/importer/parity/diff_promql.py`) and a model of the NRQL semantics",
        "(`diff_nrql.py`, stored types from `telemetry-rig-results.md`), run on seeded synthetic series",
        "(`diff_series.py`). A verdict says the two readings agree or not; it does not replace the live",
        "comparison against Prometheus, and it never changes a converter confidence. The suggested level",
        "is advice to the person who decides.",
        "",
        f"Tolerance {TOLERANCE:.0%} relative. Scenarios: {', '.join(SCENARIOS)}. Seeds {list(SEEDS)},",
        f"evaluation times {list(TIMES)} s, misalignment shifts {list(MISALIGN)} s. WRONG means the",
        "two disagree on clean data (a wrong value, or a group that only one side has). Each group is",
        "judged on its own size (no floor; only differences below 1e-9 count as none), so a small group",
        "cannot hide behind a large one. A difference of two nearly equal rates can therefore show a",
        "larger divergence than its operands do. Clean counters carry 1 percent noise (volatile: 50).",
        "",
        "Cells give the worst relative value divergence over the groups both sides have; `+M` means a",
        "group was missing on one side in some run (a missing group is not a wrong value).",
        "MEDIUM is the cap for a rule that differs only under gaps, resets, volatile data or window",
        "shifts; HIGH is suggested only when every scenario is within tolerance.",
        "",
        "## Counts",
        "",
        "| Verdict | Cases |",
        "|---|---|",
    ]
    for name in LADDER:
        lines.append(f"| {name} | {sum(r['verdict'] == name for r in results.values())} |")
    lines += [
        "",
        "## Cases",
        "",
        "Columns after the verdict are the worst relative divergence per scenario.",
        "",
        "| Case | Rule | Verdict | clean | resets | gaps | volatile | misaligned | Suggested | Note |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for cid, res in results.items():
        sc = res["scenarios"]
        cells = [_cell(sc[s]) if sc else "" for s in SCENARIOS]
        note = (res["why"] or "").replace("|", "/") or EDGE_NOTES.get(cid, "")
        lines.append(
            f"| {cid} | {cases[cid]['rule']} | {res['verdict']} | {' | '.join(cells)} | "
            f"{res.get('suggested', '')} | {note} |"
        )
    lines += ["", "## Findings", ""]
    lines += FINDINGS
    if KNOWN_WRONG:
        lines += ["", "### Rules found WRONG (xfail tests, rules untouched)", ""]
        lines += [f"- `{k}`: {v}" for k, v in KNOWN_WRONG.items()]
    return "\n".join(lines) + "\n"


def _cell(entry: dict[str, Any]) -> str:
    """The value divergence, then `+M` when a group was missing on one side."""
    return f"{entry['value']:.4f}" + (" +M" if entry["missing_group"] else "")


EDGE_NOTES: dict[str, str] = {
    "DIFF-RATE-D03": "counter without _total is stored as a gauge (rig finding 1)",
    "DIFF-GAUGE-D03": "gauge named *_count is stored as a delta (rig finding 2)",
    "CMP-FILTER-01": "flapping state: PromQL tests the latest sample, NRQL filters every sample",
    "CMP-FILTER-02": "flapping value: PromQL tests the latest sample, NRQL averages the samples above 0",
}
FINDINGS: list[str] = [
    "1. Rules found WRONG on clean data are listed below with a failing test (xfail). A WRONG",
    "   verdict is evidence against our reading, to be confirmed live before anyone edits a rule.",
    "2. Counter resets agree between the two sides (Prometheus adds the value before the reset;",
    "   New Relic stores the new value as the delta). No rule drifts on the reset scenario beyond",
    "   the tolerance except through the window effects of 4.",
    "3. Gaps are the most common edge. A counter's delta spans the silent period and lands in the",
    "   first window after it, where Prometheus counts only the increase between samples inside",
    "   the window (the rig saw the same catch-up step). A gauge that is silent for longer than the",
    "   alert window but less than five minutes stays visible to Prometheus (staleness lookback)",
    "   and is absent in New Relic, so a group is missing (`+M`). For an alert the cost is a",
    "   group that cannot fire during the silence.",
    "4. Volatile gauges: PromQL tests the latest sample, NRQL tests the window mean (or maximum).",
    "   On smooth data they agree; on spiky data they differ by design of the rule. Rules that use",
    "   `max_over_time`/`min_over_time`/`avg_over_time` do not have this edge (same reducer).",
    "5. Window alignment: Prometheus evaluates at the instant, New Relic at the end of its bucket.",
    "   On noisy ratios this moves the value by several percent (ARITH-02).",
    "6. Chart queries compare a `[5m]` Prometheus range with one 60 s New Relic bucket. The",
    "   difference is smoothing, visible in the volatile column of the DIFF-RATE-D rows.",
    "7. NOT_MODELLED means the reference or the NRQL model cannot read the form: sub-queries",
    "   (`FROM (SELECT ...)`), `if()`, and variables left unfilled. REFUSED means the converter",
    "   refused the expression, which is its safe answer. Neither is evidence for a rule.",
    "",
    "Assumptions the verdicts rest on (each is a place the model could be wrong): New Relic",
    "stores `_total/_count/_sum/_bucket` as deltas computed from the samples that arrived (the",
    "first sample of a series has no delta); a window is (end - width, end]; `bucketPercentile`",
    "interpolates linearly like `histogram_quantile` (the rig agreed within 0.01 percent);",
    "`latest` is the newest sample in the window; a missing attribute fails every WHERE test;",
    "series are merged when the NRQL facets on fewer labels than the PromQL keeps (reported as a",
    "missing group). Relative error uses a floor of 10 percent of the largest group so that a",
    "difference of two nearly equal rates is not judged on its small result.",
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    args = parser.parse_args(argv)
    results = run_all()
    for cid, res in results.items():
        print(f"{res['verdict']:<24} {cid}  {res['why']}")
    counts = {n: sum(r["verdict"] == n for r in results.values()) for n in LADDER}
    print(json.dumps(counts))
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    if args.markdown:
        args.markdown.write_text(render_markdown(results))
    return 0


if __name__ == "__main__":
    sys.exit(main())
