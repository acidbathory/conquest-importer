# Proposal: a tree-reading PromQL engine behind the existing readers, and what the owner decides

## What changed, in plain English

The PromQL translator that Grafana alerts, Grafana dashboards, Google PromQL conditions and widgets, and Azure Prometheus rule groups share used to read each expression with text patterns. It now has a second pass that reads the expression as a tree. The second pass only runs on an expression the old reader refused, and it writes a query only for the shapes listed in `docs/research/promql-engine-rules.md` (each with a hand-computed expected query, a read-only New Relic validity check and a semantic note). Everything else keeps its old refusal and its old reason.

Shapes that now convert: arithmetic between aggregates with the same grouping (differences, `1 - ratio`, `x * 100`, unary minus, one metric under two filters), `*_over_time` functions (sliding window), `topk` or `bottomk` in an alert whose threshold keeps the ranked end, comparisons against a number in dashboards (`x > n`, `x == 1`, `bool`, `count(x == 0)`), `without()` when the name map lists the metric's labels, per-series ratios and gauge sums when the name map names the series labels, and `${NAME}` placeholders when the name map gives a default.

## Numbers (offline, exact)

| measure | before | after |
|---|---:|---:|
| Google, 84 PromQL alert samples converted (placeholders never count) | 2 | 5 |
| Google, 89 PromQL conditions of the estate corpus converted | 4 | 7 |
| Google estate, PromQL expressions in widgets the shared translator writes | 277 of 558 | 282 of 558 |
| Grafana estate (50 dashboards, 2,990 PromQL expressions) written by the converter | 1,546 | 1,579 |
| Grafana alert-rule fixtures, distinct rules converted | 25 of 54 | 27 of 54 |

If the owner supplies defaults for the 23 placeholders in the 84 samples, 2 more convert (7 of 84). That figure is not counted anywhere.

Why the gain is small without hints: the blockers that remain are mostly facts only the data has. Sum of a gauge (591 expressions, 354 panels in the estate), per-series ratios of plain selectors (12 of the Google samples) and a bare `avg_over_time` need the labels that name one series. The engine writes all of them correctly the moment the name map has a `series` entry (the file `grafana-prom-hints` writes), and refuses otherwise. No hints file exists in the repository, so these are not in the numbers.

## Outputs that changed (for the owner to review)

No pinned hash file changed and no golden file was touched. Tests that spell out the old refusals were updated, each for a stated reason:

- `r2-error-ratio-one-metric` (Grafana rules2 fixture): was manual "ratio needs filter()"; now `SELECT filter(sum(requests_total), WHERE code = '500') / 300 / (sum(requests_total) / 300) FROM Metric`. The old note said the IR cannot express filter(); it can (a term's own scope).
- `topk(3, ...) > n` alerts convert by dropping the topk (the condition tests every series). The number of incidents can be larger than k; a note says so.
- Five arithmetic shapes in `test_grafana_rules2_ratio.py` that were pinned as "keeps the binary operator reason" now convert.
- The GCP sample count is 5, the Azure baseline table has 7 converted metric alerts (a quoted label in `by ("microsoft.resourceid")` is now read), and the notification-routing test counts 6 routed rules.
- Dashboard planner: `up == 1`, `up > 0` and `max_over_time(up[1h])` are now tier 3 instead of a reason; tests that used them as "stays manual" examples use `up or up` and `up or on(x) up`.

## Decisions for the owner

1. **Placeholders.** A placeholder is refused unless the name map has a default for it (`variables`). No `--var` command-line option was added: the name map already travels with the other hints, and a default written there is noted on every use. Say if a flag is wanted.
2. **Filter on an aggregate in a chart.** `sum(rate(x[5m])) > 0` on a graph stays a placeholder (no NRQL form). On a single-value widget the converter writes the nested query, but the planner does not offer that to Grafana dashboards because it does not know the widget type; a one-line change in the planner would do it if the owner wants those panels.
3. **`topk` in dashboards.** The existing `FACET x LIMIT n` is unchanged. The telemetry rig showed the stat matches and the chart's series set can differ; the cookbook's `ORDER BY` form was not applied because it changes existing dashboard output. Proposal: write `FACET x ORDER BY <aggregate> LIMIT n` after one more live comparison. `bottomk` has no verified form and stays refused.
4. **`sum(a + b)` of two rates.** Not rewritten as `sum(a) + sum(b)`: the two differ when a series exists on one side only (the sum of a sum is not the sum over matched pairs). Several Google samples (the VPN throughput and the nginx connection alerts) have this shape.
5. **Aggregations that differ inside one expression** (`sum(...) / min(...)`): the condition IR has one aggregation per signal, so these stay manual. Widening the IR would let a few Google samples convert (the Chronicle quota alerts).

## Evidence still pending

Every rule is `VERIFIED-VALID` only. `scripts/analysis/promql_rule_cases.json` carries each case (promql, args, expected shape) so the live harness can compare values with Prometheus on the telemetry rig; the column "differential test pending" of the registry says what each rule must show.
