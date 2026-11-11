# PromQL engine: rewrite registry

Status: written 2026-11-11 on branch `improve/promql-engine`. Offline code and tests, plus read-only NerdGraph checks that the NRQL is accepted. **No value was compared with Prometheus**: every rule is `VERIFIED-VALID` (New Relic accepted the query, see `promql-engine-validity.json`) and carries a pending differential test for the live harness.

## 1. What this is

The shared PromQL translator has two readers: the alert reader (`rules2/promql.py` and the plain handlers in `translate.py`, used by Grafana alert rules, Google PromQL conditions and Azure Prometheus rule groups) and the dashboard reader (`dashboard_convert/promql.py`, used by Grafana dashboards and Google dashboard widgets). Both read text with regular expressions. This change adds a second pass that reads the expression as a tree (`promql_ast.py`) and writes only the rewrites below:

- alerts: `rules2/promql_engine.py`, tried only after the older forms refused the expression (`PROM_FALLBACKS` in `translate.py`), so nothing that converted before can change;
- dashboards and Google widgets: `dashboard_convert/promql_engine.py`, tried only after `convert_classic` raised `Unconvertible`;
- the planner (`dashboards/query_tier.py`) asks the engine for a second opinion on a refused comparison or `_over_time` query, so a panel is no longer turned into a placeholder before the converter is asked.

A rule that does not apply returns nothing and the original refusal, with its original reason, stands.

Hints: several rules need facts the query does not have. They come from the name map (`IMPORTER_GRAFANA_NAME_MAP`, written by `grafana-prom-hints` or by hand): `series` (the labels that name one series of a gauge), `labels` (every label of a metric, from `keyset()`), `types`, and the new `variables` (defaults for `${NAME}` placeholders).

Test cases: `scripts/analysis/promql_rule_cases.json` (35 cases: promql, args, expected NRQL written by hand from the rule, expected shape). `scripts/analysis/promql_rule_runner.py` runs them offline; `tests/test_promql_engine_rules.py` asserts them; `scripts/analysis/promql_rule_validate.py` asks New Relic whether the converted NRQL is valid.

## 2. The rules

Columns: *surface* A is an alert condition, D a dashboard widget. *Status* is the cookbook scale. *Differential test pending* is what the live harness must show (values, not syntax) before the status can become VERIFIED-VALUE.

| id | surface | PromQL shape | NRQL form | proof obligation | status | differential test pending |
|---|---|---|---|---|---|---|
| ARITH | A | `+ - * /`, numbers and unary minus between aggregates that share one grouping and one window: `sum(rate(a[5m])) / sum(rate(b[5m]))`, `1 - x`, `x * 100`, `-x`; operands may carry different label filters | one query, math on the aggregates, same FACET; a rate is `sum(x) / seconds`; an operand with its own filter gets `filter(agg, WHERE ...)`; the shared filters stay in WHERE (cookbook "Multi-query math in alerts", method 1) | (1) all operands are the same kind (counters, gauges or one `_over_time` function) with the same `by` labels and the same range; (2) the counters are stored as delta counts (hinted type, else the `_total/_count/_bucket` suffix); (3) the gauge aggregation is exact for the reducer (`avg` with last or mean) | VERIFIED-VALID | ratio, difference, `1 - ratio` and `ratio * 100` from the telemetry rig counters against Prometheus at the same instants, per facet; filter() operand against two separate queries |
| ARITH-BARE | A | `a / b`, `a - b` of plain selectors (per-series test) | `SELECT average(a) / average(b) FROM Metric FACET <series labels>` | the hints give the same series labels for every selector, so each facet is one series and the test is per series; without hints the rule refuses (a maximum of a ratio is not a ratio of maxima) | VERIFIED-VALID | per-series ratio of newest values against Prometheus for a metric with more than one series |
| GAUGE-SUM | A | `sum by (L) (gauge)` | `average(gauge) FACET L` | the hints say the labels of `L` and the matchers name exactly one series, so the sum has one term and equals the value | VERIFIED-VALID | sum of a gauge with one series per group against the average of its samples |
| GAUGE-SUM | D | `sum by (L) (gauge)` on a widget that shows one value, when the hints list more series labels than `L` | `SELECT sum(v) FROM (SELECT latest(g) AS v FROM Metric FACET <series labels> LIMIT MAX) FACET L LIMIT MAX` (cookbook "sum() of a gauge", method 1, VERIFIED-VALUE) | the hint is complete (all labels that name one series) | VERIFIED-VALID | nested sum against `sum by (L)` in Prometheus on an instant query, with series churn |
| OVER-TIME | A | `avg|max|min|sum|count_over_time(x[w])`, alone or in the same aggregate (`avg by (h) (avg_over_time(..))`, `max(max_over_time(..))`, `sum(sum_over_time(..))`, `sum(count_over_time(..))`) | the aggregation over a window of `w`; the condition slides it by the evaluation interval (`slide_by_seconds`) when that divides the window and is at least 30 s (cookbook "_over_time", method 3) | the outer aggregate is the same operation as the inner one, so the two layers are one aggregation; a bare `max_over_time` is only converted for an above threshold (and `min_over_time` for below), where the extreme of all samples is the extreme of the per-series extremes; other bare forms need the series labels (hints) | VERIFIED-VALID | rolling windows against Prometheus at several evaluation times; sample counts when scrape intervals differ |
| OVER-TIME | D | same functions | chart: `TIMESERIES w SLIDE BY 1 minute` (the step is the fixed bucket when it divides `w`); a variable window (`$__rate_interval`, `$interval`) is the bucket; a single value uses the dashboard range (a widget query cannot hold SINCE) | the function maps to the NRQL aggregate of the cookbook table | VERIFIED-VALID | sliding window against `*_over_time` at the same points; the single-value widget against a range equal to `w` |
| CMP-FILTER | D | `x OP n` on a plain selector | chart or value: `WHERE x OP n` on the sample value; `==` is `>= n AND <= n` (raw equality matched nothing in the live test) (cookbook "Comparison filters", method 1) | none for the form; the note says it tests every sample in the range, not only the newest | VERIFIED-VALID | the series set against Prometheus `x > n` on a state that flips inside the range |
| CMP-FILTER | D | `x OP n` on a plain selector, widget shows one value, hints list the series labels | `SELECT * FROM (SELECT latest(x) AS v FROM Metric FACET <series labels> LIMIT MAX) WHERE v OP n` (method 2, VERIFIED-VALUE) | the hint is complete | VERIFIED-VALID | the rows against Prometheus `x > n` instant query |
| CMP-FILTER | D | `agg(...) OP n` on a widget that shows one value | `SELECT * FROM (SELECT <aggregate> AS v FROM Metric FACET k LIMIT MAX) WHERE v OP n` | the aggregate is a form the plain reader writes | VERIFIED-VALID | groups against Prometheus on an instant query; a chart of a filtered aggregate stays refused |
| CMP-BOOL | D | `agg OP bool n` | `if(<aggregate> OP n, 1, 0)` (accepted by New Relic on Metric, including with FACET and TIMESERIES) | none | VERIFIED-VALID | 0/1 values per group and bucket against Prometheus `bool` |
| CMP-COUNT | D | `count [by (L)] (x OP n)` on a widget that shows one value, with series hints | `SELECT count(*) FROM (SELECT latest(x) AS v FROM Metric FACET <series labels> LIMIT MAX) WHERE v OP n [FACET L]` | the hint is complete | VERIFIED-VALID | counts against Prometheus on an instant query |
| TOPK | A | `topk(k, E) > n`, `bottomk(k, E) < n` (the bound keeps the ranked end) | the query of `E`; the condition tests every series | "some series passes the bound" is true exactly when "some series among the top k passes it"; only the number of incidents differs (a note says so). The other direction is refused | VERIFIED-VALID | fires-or-not against Prometheus over a replay with more than k series above the bound |
| WITHOUT | A | `agg without (X) (rate(m[w]))` | `FACET <labels of m> minus X` (cookbook "without() grouping", method 1, VERIFIED-VALUE) | the `labels` hint lists every label of `m` at the time of the run and at most 5 remain | VERIFIED-VALID | grouped values against Prometheus when a label appears later |
| PLACEHOLDER | A (Google, Azure) | `${NAME}` in a query | the default from the name map `variables`, written in with a note per use | the default is the person's choice; a name without a default is refused with the old reason (never dropped or guessed) | VERIFIED-VALID | not a value rule; the harness only needs the default it is given |

## 3. What stays refused, and why

| shape | reason |
|---|---|
| `a / on(x) b`, `group_left`, `ignoring` | a join needs the NRQL JOIN of cookbook "Joins" (method 1) with the info metric as sub-select; alerts cannot hold a JOIN, and the `group_left` label-adding intent has no form. Not provable here |
| `a and b`, `a or b`, `a unless b` | the cookbook method for sets is `IN (SELECT uniques(...))` and is VERIFIED-VALID only (no value test), and an alert condition cannot hold a sub-select; `or` has no form |
| a filter on an aggregate in a chart (`sum(rate(x[5m])) > 0` on a graph) | NRQL has no post-aggregation filter per bucket (cookbook "Comparison filters": what New Relic cannot do) |
| `sum(g)` and `count(g)` of a gauge with no `series` hint | the cookbook asks for the labels that name one series; the converter never guesses the series count |
| per-series ratio, bare `avg_over_time`, bare `rate` with no series labels | one query would combine every series first; `max(a) / max(b)` is not the maximum of `a / b` |
| `topk(k, E) < n`, `bottomk(k, E) > n` | the condition would test the wrong end of the ranking |
| `avg_over_time(..)` under a different outer aggregate (`sum(max_over_time(..))`) | two layers; the nested form needs the series labels and an alert cannot hold it |
| `histogram_quantile`, `predict_linear`, `label_replace`, `offset`, subqueries, `changes`, `resets` | unchanged: see the census and the cookbook |
| `x / 0` | division by a literal zero is not an alert |

## 4. Caveats measured elsewhere that apply

- Counters arrive as delta counts, so `sum(x)` over the window is the increase and a rate is that sum divided by the window (`live-grafana-counter-roundtrip.md`). The engine writes the division, as the older counter form does.
- Cumulative gauges (a running total sent as a gauge) are wrong under `rate`; the `types` hint decides, otherwise the suffix does (`telemetry-rig-results.md`, finding 1).
- `topk` in a chart ranks over the whole range, Prometheus at each step: the older converter note stays; no ORDER BY change is made here (see `docs/improvement/proposals/2026-11-11-promql-engine.md`).
- A comparison against a float with `=` matched nothing in the live test; the engine writes a closed range.

## 5. How to extend

Add the case to `promql_rule_cases.json` with the NRQL computed by hand first, run `promql_rule_runner.py`, add the row above, run `promql_rule_validate.py` and commit the validity file with the rule.
