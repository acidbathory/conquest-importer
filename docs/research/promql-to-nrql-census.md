# PromQL to NRQL census: which constructs the dashboards use, which ones block conversion, and what a better translator would take

Status: research note, written 2026-11-10 on branch `research/promql-census`. Offline: no call to Grafana, New Relic or any cloud was made, and no web page was fetched (WebFetch and WebSearch were not available in this session). Every number below is reproducible with `scripts/analysis/promql_census.py` (command in section 1).

## 0. How to read this file

Evidence tags used in the verdict tables:

- MEASURED: shown by a live round trip already recorded in this repository (`docs/research/live-grafana-counter-roundtrip.md`, called LIVE-C).
- DOCS: the NRQL feature is documented, and the equivalence is argued from the documentation. The page keys N1 to N10 are the ones indexed in `docs/research/grafana-complex-dashboards-repos.md` section 0 (N1 NRQL reference, N2 subqueries, N3 subquery joins, N4 nested aggregation, N9 template variables, N10 nested variables); R-A rows F01 to F64 are in `docs/research/newrelic-grafana-data-and-query-analogues.md` section 3; R-C is the complex-dashboards note. **I did not re-read any of these pages in this task**; DOCS here means "the earlier note quotes the page", not "checked today".
- UNVERIFIED: a candidate NRQL pattern whose function or behaviour on `Metric` data nobody read or ran.
- n/a: nothing to verify (no NRQL form, or a parser limitation of the current converter).

Verdict codes (the user's four classes): 1 expressible exactly; 2 expressible with named caveats (scrape interval, bucket, counter or gauge, label sets); 3 only through a subquery (`FROM (SELECT ...)`, `WHERE x IN (FROM ...)`) or FACET math; 4 not expressible. I was strict: an aggregate over a bucket is not "exact" for a gauge, so `max`, `min` and `matcher.ne` are code 2.

## 1. Corpus, method and headline numbers

Corpus (read-only): the 19 dashboards extracted from the Grafana Cloud sandbox in `live-grafana-dash/out/live-run/extract-all/dashboards`: 6 community dashboards (prefix `ckg-`: NGINX Ingress, kube-state-metrics v2, JVM Micrometer, cAdvisor, PostgreSQL, Node Exporter Full) and 13 built-in Grafana Cloud usage, billing, cardinality and incident dashboards. Alert rule fixtures (`tests/fixtures/grafana*`) were counted separately (section 3.4). Caution on representativeness: 169 of the 412 PromQL panels belong to the 13 built-in dashboards, which are Grafana Cloud internals (billing arithmetic, `group_left` joins to `*_info` metrics, `without(monetary)`); they inflate `without`, `group_left`, `set.or` and `subquery`. The community half is the better guide to what a customer's own dashboards look like, and both halves are shown.

Method:

1. `importer.sources.grafana.dashboard_convert.translate.load_items` reads the files; `resolve_target` decides the data source family of each visible query (a Loki query is counted and skipped).
2. Every PromQL `expr` is parsed with the new `src/importer/sources/grafana/promql_ast.py` (a small tokenizer and precedence parser, no new dependency; 857 of 857 expressions parsed; the only parse failures in all inputs were LogQL, the hostile fixtures, and one invalid `sum(...) offset 1w`). Grafana variables stay in the tree as text (`$__rate_interval` in a range, `$x` in a matcher) or as a `Placeholder` (an operand).
3. `scripts/analysis/promql_constructs.py` maps the tree to construct ids (`fn.rate`, `agg.without`, `match.group_left`, ...) and a few converter-shape ids (`shape.counter_suffix_bare`, `style.single_quoted_string`) for failures that are not PromQL features but that the current converter has.
4. Every expression is also run through the real `convert_query` with every dashboard variable treated as an ordinary variable, so that the answer is about the query text and not about the variable plan. The set `SUPPORTED_TODAY` (what the converter writes) is then checked against that answer: **0 of 857 expressions disagree** (every expression that converts uses only supported constructs; every one that does not has at least one unsupported construct). `tests/test_promql_census.py` repeats the check on minimal samples and on the repository fixtures.
5. Panel-level blockers other than the query come from the planner's own reasons (`DashboardPlan.panels[i].reasons`): a manual variable, transformations, widget type, data source.

Reproduce:

```
uv run python scripts/analysis/promql_census.py <extract-all/dashboards> \
  --alerts tests/fixtures/grafana tests/fixtures/grafana-rules2 tests/fixtures/grafana-notify-convert \
  --markdown census.md --verdict-table verdicts.md
```

Headline numbers (MEASURED on the corpus):

| | count |
|---|---:|
| dashboards | 19 |
| panels | 534 |
| panels with at least one PromQL query (the "census panels") | 412 (243 community, 169 built-in) |
| PromQL expressions (727 distinct texts) | 857 |
| LogQL expressions (skipped) | 65 |
| expressions the converter writes today | 492 (57%) |
| census panels whose every expression the converter writes today ("query layer") | 230 (56%) |
| census panels that become a widget today (end to end) | **8 (1.9%)** |

The gap between 230 and 8 is the main finding of this note: **the PromQL translator is not what blocks the dashboards today; the variable gate is.**

## 2. What blocks the 412 census panels

Planner reasons that are not about the query text (a panel can have several):

| other blocker (planner reason) | panels |
|---|---:|
| dashboard panel uses a variable that stays manual, so its filter cannot be written | 389 |
| dashboard panel has transformations, which run in the browser and cannot be reproduced in a query | 21 |
| dashboard panel type gauge maps to a New Relic widget the importer cannot create yet | 6 |
| dashboard panel type bargauge maps to a New Relic widget the importer cannot create yet | 5 |
| dashboard panel type barchart maps to a New Relic widget the importer cannot create yet | 1 |
| dashboard panel overrides the time range (timeFrom or timeShift) | 1 |
| dashboard panel has options that change what it shows (value mappings, overrides, a unit conversion or a calculation other than the latest value), and its query is already at the highest tier | 1 |

389 of 412 panels (94%) carry "uses a variable that stays manual". In this corpus the manual variables are chained query variables ("depends on another variable", 23 of the 48 manual ones: `$namespace` defined from `label_values(metric{cluster="$cluster"}, namespace)`), `query_result()` and other non-`label_values` queries, variable regexes the converter cannot verify, non-Prometheus data sources and ad hoc filters. Every query that uses `$cluster`, `$namespace` or `$pod` then fails the whole panel. If that gate alone were lifted, **221 of 412 panels (54%) would convert end to end today** with no change to the PromQL side. New Relic documents nested variables (`WHERE env IN ({{env}})`, N10) but only for some plans, and N9 still says they are not supported (R-C 4.0 notes the disagreement); whether the owner's account has them is UNVERIFIED. This is the first thing to settle, before any PromQL work: it moves the end-to-end number by two orders of magnitude more than every rule below.

## 3. Census

### 3.1 Constructs, ranked

Columns: *expressions* and *panels* where the construct appears; *panels it blocks* = census panels with a failing expression that contains it (query layer); *sole blocker* = panels whose complete set of unsupported constructs is that one construct (so supporting it alone would fix the query layer of those panels); *sole, nothing else blocks* = the same panels without any other planner blocker (so they would convert end to end today); *today* = the converter writes it; *verdict* and *evidence* from section 4. Unsupported constructs first, by panels blocked; then the supported ones.

| construct | expressions | panels | panels it blocks | sole blocker | sole, nothing else blocks | today | verdict | evidence |
|---|---:|---:|---:|---:|---:|---|---:|---|
| `cmp.filter.vector_scalar` | 263 | 108 | 108 | 18 | 3 | no | 3 | DOCS |
| `agg.without` | 183 | 63 | 63 | 1 | 0 | no | 4 | n/a |
| `match.explicit` | 68 | 44 | 44 | 0 | 0 | no | 3 | DOCS |
| `match.on` | 68 | 44 | 44 | 0 | 0 | no | 3 | DOCS |
| `match.group_left` | 67 | 43 | 43 | 0 | 0 | no | 4 | n/a |
| `shape.counter_suffix_bare` | 35 | 27 | 27 | 2 | 0 | no | 2 | MEASURED |
| `set.or` | 44 | 24 | 24 | 1 | 0 | no | 4 | n/a |
| `subquery` | 27 | 22 | 22 | 0 | 0 | no | 3 | UNVERIFIED |
| `agg.nested` | 24 | 15 | 15 | 0 | 0 | no | 3 | DOCS |
| `name.variable` | 14 | 14 | 14 | 0 | 0 | no | 4 | n/a |
| `mod.offset` | 13 | 13 | 13 | 0 | 0 | no | 2 | DOCS |
| `match.ignoring` | 22 | 12 | 12 | 0 | 0 | no | 3 | UNVERIFIED |
| `fn.sum_over_time` | 15 | 10 | 10 | 0 | 0 | no | 2 | DOCS |
| `fn.avg_over_time` | 9 | 9 | 9 | 2 | 0 | no | 2 | DOCS |
| `fn.sort_desc` | 9 | 9 | 9 | 0 | 0 | no | 2 | DOCS |
| `fn.label_replace` | 8 | 8 | 8 | 2 | 0 | no | 3 | UNVERIFIED |
| `fn.round` | 8 | 8 | 8 | 2 | 0 | no | 2 | DOCS |
| `fn.delta` | 7 | 7 | 7 | 1 | 0 | no | 2 | UNVERIFIED |
| `style.single_quoted_string` | 8 | 7 | 7 | 6 | 0 | no | 1 | n/a |
| `fn.vector` | 6 | 6 | 6 | 0 | 0 | no | 4 | n/a |
| `idiom.or_vector` | 6 | 6 | 6 | 0 | 0 | no | 2 | UNVERIFIED |
| `fn.time` | 4 | 4 | 4 | 3 | 1 | no | 2 | DOCS |
| `matcher.re.complex_syntax` | 4 | 4 | 4 | 0 | 0 | no | 2 | DOCS |
| `name.recording_rule` | 5 | 4 | 4 | 0 | 0 | no | 2 | DOCS |
| `shape.avg_min_max_of_rate` | 10 | 4 | 4 | 3 | 0 | no | 3 | DOCS |
| `fn.scalar` | 10 | 3 | 3 | 0 | 0 | no | 3 | UNVERIFIED |
| `matcher.name_label` | 8 | 3 | 3 | 0 | 0 | no | 4 | n/a |
| `matcher.re.matches_empty` | 3 | 3 | 3 | 1 | 1 | no | 2 | UNVERIFIED |
| `selector.no_name` | 8 | 3 | 3 | 0 | 0 | no | 4 | n/a |
| `style.uppercase_aggregate` | 3 | 3 | 3 | 3 | 0 | no | 1 | n/a |
| `cmp.filter.vector_vector` | 2 | 2 | 2 | 0 | 0 | no | 3 | UNVERIFIED |
| `fn.changes` | 2 | 2 | 2 | 1 | 0 | no | 4 | n/a |
| `set.and` | 5 | 2 | 2 | 0 | 0 | no | 3 | DOCS |
| `cmp.bool.vector_scalar` | 2 | 1 | 1 | 1 | 0 | no | 2 | UNVERIFIED |
| `fn.clamp_min` | 1 | 1 | 1 | 1 | 0 | no | 1 | DOCS |
| `fn.day_of_month` | 4 | 1 | 1 | 0 | 0 | no | 2 | UNVERIFIED |
| `fn.hour` | 4 | 1 | 1 | 0 | 0 | no | 2 | UNVERIFIED |
| `fn.max_over_time` | 1 | 1 | 1 | 0 | 0 | no | 2 | DOCS |
| `matcher.value_variable` | 836 | 397 | 0 | 0 | 0 | yes | 2 | DOCS |
| `selector.instant` | 684 | 328 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `matcher.eq` | 734 | 312 | 0 | 0 | 0 | yes | 1 | DOCS |
| `scalar.literal` | 330 | 159 | 0 | 0 | 0 | yes | 1 | DOCS |
| `agg.sum` | 188 | 127 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `matcher.re` | 163 | 125 | 0 | 0 | 0 | yes | 2 | DOCS |
| `binop.*` | 288 | 121 | 0 | 0 | 0 | yes | 1 | DOCS |
| `arith.vector_scalar` | 272 | 110 | 0 | 0 | 0 | yes | 1 | DOCS |
| `arith.vector_vector` | 146 | 93 | 0 | 0 | 0 | yes | 2 | DOCS |
| `selector.range` | 185 | 93 | 0 | 0 | 0 | yes | 2 | n/a |
| `binop.-` | 239 | 91 | 0 | 0 | 0 | yes | 1 | DOCS |
| `agg.ungrouped` | 136 | 84 | 0 | 0 | 0 | yes | 1 | DOCS |
| `agg.by` | 113 | 76 | 0 | 0 | 0 | yes | 2 | DOCS |
| `agg.avg` | 197 | 71 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `arith.scalar_scalar` | 219 | 69 | 0 | 0 | 0 | yes | 1 | DOCS |
| `fn.rate` | 150 | 69 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `var.as_operand` | 218 | 68 | 0 | 0 | 0 | yes | 2 | DOCS |
| `match.implicit` | 102 | 67 | 0 | 0 | 0 | yes | 2 | DOCS |
| `window.rate_interval` | 135 | 57 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `binop./` | 89 | 53 | 0 | 0 | 0 | yes | 2 | DOCS |
| `window.fixed` | 64 | 45 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `agg.topk` | 51 | 35 | 0 | 0 | 0 | yes | 2 | DOCS |
| `agg.max` | 48 | 25 | 0 | 0 | 0 | yes | 2 | DOCS |
| `matcher.ne` | 30 | 13 | 0 | 0 | 0 | yes | 2 | UNVERIFIED |
| `matcher.nre` | 15 | 13 | 0 | 0 | 0 | yes | 2 | UNVERIFIED |
| `window.range` | 18 | 13 | 0 | 0 | 0 | yes | 1 | DOCS |
| `fn.increase` | 10 | 9 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `fn.irate` | 18 | 9 | 0 | 0 | 0 | yes | 2 | MEASURED |
| `binop.+` | 17 | 7 | 0 | 0 | 0 | yes | 1 | DOCS |
| `agg.count` | 12 | 5 | 0 | 0 | 0 | yes | 2 | DOCS |
| `window.interval` | 2 | 2 | 0 | 0 | 0 | yes | 2 | DOCS |
| `fn.histogram_quantile` | 3 | 1 | 0 | 0 | 0 | yes | 2 | DOCS |
| `matcher.ne_empty` | 5 | 1 | 0 | 0 | 0 | yes | 2 | UNVERIFIED |
| `unary.-` | 1 | 1 | 0 | 0 | 0 | yes | 1 | DOCS |

### 3.2 Reading the table

- Co-occurrence is heavy. The biggest construct, a comparison filter (`x > 0`, `x == 1`, `... >= 0`), appears in 108 panels but is the only blocker of 18. Supporting one construct at a time unlocks few panels; the greedy table shows how the query-layer count rises when constructs are added in the best order (verdict 1 to 3, previous rows kept): 230, then 248 with the comparison filter, 254 with single quotes, 258 with bare `_count` names, and so on to 279 after 12 steps.
- The comparison filter is used for two different things. In the community dashboards it is an alert-style condition (`kube_node_status_condition{...} == 1`, `count(x == 0)`, `changes(...) > 1`); in the built-in dashboards it is the Grafana Cloud idiom "hide this series for some customers": `(expr  - (9999999999 * $is_customer_of_partner)) >= 0`. Of the 108 panels, 55 are time series or graph panels, where the nested NRQL form (a `WHERE` over an inner query) returns rows, not a filtered series (section 4).
- `without` is the biggest construct that has no static answer: 63 panels (61 built-in), all of them `avg without(monetary) (...)`. It needs the label set of the metric, which only the data has.
- The cheap fixes are not PromQL features. Single-quoted label values (`fstype!~'rootfs'`, all of Node Exporter Full's filesystem panels), upper-case `SUM(`, the 50-operand cap, and the `_count`/`_sum`/`_total` name heuristic account for 6 + 3 sole-blocked panels at the query layer and 27 panels where the name heuristic fires (for example `system_cpu_count`, `pg_stat_activity_count`, which are gauges). They come from the converter's regular-expression front end, not from NRQL.

### 3.3 Split by dashboard group

| group | scenario | PromQL panels | query layer | end to end | if variable gate lifted |
|---|---|---:|---:|---:|---:|
| community | today | 243 | 186 | 3 | 178 |
| community | verdict 1, 2 | 243 | 207 | 4 | 196 |
| community | verdict 1, 2, 3 | 243 | 236 | 9 | 220 |
| built-in | today | 169 | 44 | 5 | 43 |
| built-in | verdict 1, 2 | 169 | 45 | 6 | 44 |
| built-in | verdict 1, 2, 3 | 169 | 52 | 6 | 49 |

### 3.4 Alert rule fixtures (for the alert translator, not the dashboard converter)

69 `expr` strings in `tests/fixtures/grafana`, `grafana-rules2` and `grafana-notify-convert` (the folders overlap, so some are duplicates): 59 PromQL, all parsed, and 10 LogQL (not PromQL). The PromQL ones are dominated by `rate` over fixed windows, `sum by`, comparisons with a threshold (`cmp.filter.vector_scalar`, 6; in an alert this is the threshold operator and is not a gap), `absent` (4, an alert-only construct), `predict_linear` (2), `offset` (2) and `on/group_left` (2). The full list is produced by the script's `--alerts` option; it is a sanity check that the construct vocabulary is the same, not a second census.

## 4. Verdict per construct

The table lists every construct the census knows (including some that are not in the corpus, from the PromQL function list in the task, for completeness). The NRQL pattern column uses `g` for a gauge, `c` for a delta `count` counter, `<w>` for the PromQL window and `<bucket>` for the NRQL bucket. Patterns marked UNVERIFIED are candidates for the differential harness (section 6), not claims.

| construct | in corpus | verdict | NRQL pattern | caveats | evidence | source |
|---|---:|---:|---|---|---|---|
| `agg.ungrouped` | 136 | 1 | no FACET | - | DOCS | - |
| `arith.scalar_scalar` | 219 | 1 | constant arithmetic | - | DOCS | - |
| `arith.vector_scalar` | 272 | 1 | (aggregate) op number | - | DOCS | R-A F57 |
| `binop.*` | 288 | 1 | x * y inside one SELECT | - | DOCS | R-A F57 |
| `binop.+` | 17 | 1 | x + y inside one SELECT | division by zero gives null, not Inf (N1) | DOCS | R-A F57 |
| `binop.-` | 239 | 1 | x - y inside one SELECT | - | DOCS | R-A F57 |
| `cmp.scalar_scalar` | 0 | 1 | constant | - | DOCS | - |
| `fn.abs` | 0 | 1 | abs(x) | - | DOCS | R-A F47 |
| `fn.ceil` | 0 | 1 | ceil(x) | - | DOCS | R-A F47 |
| `fn.clamp` | 0 | 1 | clamp_max(clamp_min(x, lo), hi) | - | DOCS | R-A F46 |
| `fn.clamp_max` | 0 | 1 | clamp_max(x, n)  (or if(x > n, n, x)) | - | DOCS | R-A F46 |
| `fn.clamp_min` | 1 | 1 | clamp_min(x, n)  (or if(x < n, n, x)) | - | DOCS | R-A F46; R-C 4.11 |
| `fn.exp` | 0 | 1 | exp(x) | - | DOCS | R-A F47 |
| `fn.floor` | 0 | 1 | floor(x) | - | DOCS | R-A F47 |
| `fn.ln` | 0 | 1 | ln(x) | - | DOCS | R-A F47 |
| `fn.log10` | 0 | 1 | log10(x) | - | DOCS | R-A F48 |
| `fn.log2` | 0 | 1 | log(x, 2) | - | DOCS | R-A F48 |
| `fn.sqrt` | 0 | 1 | sqrt(x) | - | DOCS | R-A F47 |
| `matcher.eq` | 734 | 1 | WHERE attr = 'v' | label name must be a valid attribute (backtick quoted by identifier()) | DOCS | N1 |
| `scalar.literal` | 330 | 1 | number | - | DOCS | - |
| `shape.huge_expression` | 0 | 1 | same expression | a safety cap of the current converter (50 operands), not a PromQL feature | n/a | - |
| `style.single_quoted_string` | 8 | 1 | same as double quotes | parser limitation of the current converter, not a PromQL feature | n/a | - |
| `style.uppercase_aggregate` | 3 | 1 | same as lower case | parser limitation of the current converter (PromQL aggregators are case-insensitive) | n/a | - |
| `unary.+` | 0 | 1 | x | - | DOCS | - |
| `unary.-` | 1 | 1 | -x | - | DOCS | R-A F64 |
| `window.range` | 18 | 1 | the dashboard time window | - | DOCS | R-C 4.3 |
| `agg.avg` | 197 | 2 | SELECT average(x) ... FACET ... | equal weight per sample, not per series; equal to PromQL only if series have the same sample count in the bucket | MEASURED | LIVE-C part 2 |
| `agg.by` | 113 | 2 | FACET a, b LIMIT MAX | at most 5 attributes; at most 5000 facets | DOCS | N1 sel-facet |
| `agg.count` | 12 | 2 | SELECT uniqueCount(<identifying label>) / cardinality(x) | count(m) counts series, count() in NRQL counts samples; uniqueCount is exact to 256 values | DOCS | R-A F31; R-C 4.2 |
| `agg.max` | 48 | 2 | SELECT max(x) | as agg.min | DOCS | R-A F30 |
| `agg.min` | 0 | 2 | SELECT min(x) | min over every sample in the bucket, PromQL takes one sample per series per step: equal only with one sample per series per bucket | DOCS | R-A F30 |
| `agg.quantile` | 0 | 2 | SELECT percentile(x, q*100) | mixes samples and series | DOCS | R-A F33 |
| `agg.sum` | 188 | 2 | SELECT sum(x) ... FACET ... | counter (delta count): exact. Gauge: adds every sample in the bucket, so it is high by the samples per series (measured 4x per 60 s bucket, 41x for 600 s); exact form is a nested latest()-per-series | MEASURED | LIVE-C part 2 |
| `agg.topk` | 51 | 2 | FACET l ORDER BY <aggregate> LIMIT n | ranks over the whole window, not at each step; without ORDER BY the facets are picked by frequency for sum()/rate() (N1 sel-facet), which the current converter does not write | DOCS | N1 sel-facet-order; R-C 4.6 |
| `arith.vector_vector` | 146 | 2 | SELECT f(a) op f(b) FROM Metric FACET <shared labels> | exact only when both sides are aggregated to the same label set and the same bucket; different filters per side need filter() (N1 func-filter); different label sets have no form | DOCS | R-C 4.4 |
| `binop.%` | 0 | 2 | mod(x, y) | function name unconfirmed | UNVERIFIED | R-A F57 |
| `binop./` | 89 | 2 | x / y inside one SELECT | division by zero gives null, not +Inf/NaN; both sides need the same FACET and bucket | DOCS | R-A F57 |
| `binop.^` | 0 | 2 | pow(x, y) | function name unconfirmed | UNVERIFIED | R-A F57 |
| `cmp.bool.vector_scalar` | 2 | 2 | if(<aggregate> > n, 1, 0) | if() over aggregates inside SELECT is documented (N1 func-if); UNVERIFIED on Metric | UNVERIFIED | R-C 4.10 |
| `cmp.bool.vector_vector` | 0 | 2 | if(<aggregate a> > <aggregate b>, 1, 0) | as cmp.bool.vector_scalar | UNVERIFIED | R-C 4.10 |
| `fn.avg_over_time` | 9 | 2 | SELECT average(g) ... TIMESERIES <w> SLIDE BY <step> | average over samples; SLIDE BY gives the overlapping window; a stat uses the dashboard window instead of [w] | DOCS | N1 slide-by; R-A F18 |
| `fn.count_over_time` | 0 | 2 | SELECT count(g) ... TIMESERIES <w> | counts samples | DOCS | R-A F21 |
| `fn.day_of_month` | 4 | 2 | dayOfMonthOf(...) (name unconfirmed) | see fn.hour | UNVERIFIED | R-A F52 |
| `fn.day_of_week` | 0 | 2 | dayOfWeekOf(...) (name unconfirmed) | see fn.hour | UNVERIFIED | R-A F52 |
| `fn.delta` | 7 | 2 | SELECT latest(g) - earliest(g) ... TIMESERIES <w> | `earliest` on Metric gauges is not in the function list read (R-A F11); candidate: derivative(g, <w>) * <w> is a regression slope, not the end-point difference | UNVERIFIED | R-A F11 |
| `fn.deriv` | 0 | 2 | SELECT derivative(g, 1 second) ... TIMESERIES <w> | both are least squares (N1); derivative needs two points in the bucket | UNVERIFIED | R-A F13 |
| `fn.histogram_quantile` | 3 | 2 | SELECT bucketPercentile(x_bucket, q*100) FROM Metric FACET ... | only for `sum by (le, ...) (rate(x_bucket[w]))`; interpolation inside a bucket equal to Prometheus is UNVERIFIED; not usable in alert conditions | DOCS | N1 func-bucket-percentile; R-C 4.5 |
| `fn.hour` | 4 | 2 | hourOf(aggregationendtime()) | time zone: PromQL is UTC; NRQL needs WITH TIMEZONE 'UTC'; function names beyond hourOf/minuteOf/weekOf unconfirmed | UNVERIFIED | R-A F52 |
| `fn.idelta` | 0 | 2 | latestrate(g, 1 minute) * interval | only with regular scrapes | UNVERIFIED | R-A F12 |
| `fn.increase` | 10 | 2 | SELECT sum(c) ... TIMESERIES <w> (or rate(sum(c), <w>)) | sum of delta counts is the exact increase; a stat over data shorter than w extrapolates (measured 4385.8 vs 720) | MEASURED | LIVE-C part 1 |
| `fn.irate` | 18 | 2 | same as rate, or latestrate(c, 1 second) | measured equal on steady data; differs on spikes (bucket average, not last two samples) | MEASURED | LIVE-C part 1 |
| `fn.last_over_time` | 0 | 2 | SELECT latest(g) ... TIMESERIES <w> | - | DOCS | R-A F22 |
| `fn.max_over_time` | 1 | 2 | SELECT max(g) ... TIMESERIES <w> SLIDE BY <step> | as avg_over_time | DOCS | R-A F19 |
| `fn.min_over_time` | 0 | 2 | SELECT min(g) ... TIMESERIES <w> SLIDE BY <step> | as avg_over_time | DOCS | R-A F19 |
| `fn.month` | 0 | 2 | monthOf(...) (name unconfirmed) | see fn.hour | UNVERIFIED | R-A F52 |
| `fn.predict_linear` | 0 | 2 | latest(g) + derivative(g, 1 second) * <t seconds> | window is the query window, not [w]; NRQL has predictLinear(g, t) and a PREDICT clause (N1) which the documentation advises against with TIMESERIES | UNVERIFIED | R-C 4.11 |
| `fn.quantile_over_time` | 0 | 2 | SELECT percentile(g, q*100) ... TIMESERIES <w> | NRQL percentile is sketch based, not exact | DOCS | R-A F24 |
| `fn.rate` | 150 | 2 | SELECT rate(sum(c), 1 second) FROM Metric [FACET ...] TIMESERIES <bucket> | exact only when the bucket is a multiple of the scrape interval (measured: 5 s buckets on 10 s data give 0 and 2.4); counter must be a delta `count`; the [w] window is not honoured; stat divides by the query window | MEASURED | LIVE-C part 1 |
| `fn.round` | 8 | 2 | round(x, k) when to_nearest is 10^-k; otherwise round(x / n) * n | Prometheus rounds to the nearest multiple of to_nearest; NRQL's second argument is read as decimal places; tie rounding not stated | DOCS | R-A F48 |
| `fn.sort` | 0 | 2 | ORDER BY on a table widget | ordering only; no effect on a chart | DOCS | R-A F55 |
| `fn.sort_desc` | 9 | 2 | ORDER BY <col> DESC on a table widget | ordering only; no effect on a chart | DOCS | R-A F55 |
| `fn.stddev_over_time` | 0 | 2 | SELECT stddev(g) ... TIMESERIES <w> | population vs sample deviation not stated; not in the Metric gauge list read | UNVERIFIED | R-A F25 |
| `fn.sum_over_time` | 15 | 2 | SELECT sum(g) ... TIMESERIES <w> SLIDE BY <step> | adds samples, so the value depends on the scrape count in w | DOCS | R-A F20 |
| `fn.time` | 4 | 2 | aggregationendtime() | end of the bucket, not the evaluation instant | DOCS | R-A F50 |
| `idiom.or_vector` | 6 | 2 | widget null-to-zero option, or filter(count(*), WHERE ...) | exact for counts, approximate for sums; the null option is a widget setting, not NRQL | UNVERIFIED | R-C 4.10 |
| `match.implicit` | 102 | 2 | shared FACET labels | PromQL matches on all labels; NRQL has the labels you FACET by, so both sides must be grouped by identical labels | DOCS | R-C 4.4 |
| `matcher.eq_empty` | 0 | 2 | WHERE attr IS NULL | `label=""` means absent; same empty-string caveat | UNVERIFIED | - |
| `matcher.ne` | 30 | 2 | WHERE attr != 'v' | PromQL also matches series that lack the label; NRQL `!=` on a missing attribute is not documented to match (UNVERIFIED), so exact only when every series has the label | UNVERIFIED | N1 |
| `matcher.ne_empty` | 5 | 2 | WHERE attr IS NOT NULL | `label!=""` means present; exact only if empty strings are not stored as values (UNVERIFIED) | UNVERIFIED | regex_matchers.py |
| `matcher.nre` | 15 | 2 | WHERE attr NOT RLIKE 'p' | as matcher.re; behaviour of NOT RLIKE on a missing attribute is UNVERIFIED | UNVERIFIED | N1 |
| `matcher.re` | 163 | 2 | WHERE attr RLIKE 'p' (plain alternatives: attr IN ('a','b')) | RE2 and full-string match on both sides (N1); the converter only carries a checked subset; patterns that match the empty string differ (missing label) | DOCS | N1; regex_matchers.py |
| `matcher.re.complex_syntax` | 4 | 2 | WHERE attr RLIKE 'p' | RE2 on both sides, but the converter only writes a checked subset (no backslash, ^, $, (?) so that no escaping question reaches the NRQL string; backslashes need quote_string escaping and a test per pattern | DOCS | N1; regex_matchers.py |
| `matcher.re.matches_empty` | 3 | 2 | drop the matcher for `.*`; otherwise (attr RLIKE 'p' OR attr IS NULL) | PromQL treats a missing label as empty; the NRQL pair is exact only if NRQL stores no empty-string attributes (UNVERIFIED) | UNVERIFIED | regex_matchers.py |
| `matcher.value_variable` | 836 | 2 | attr IN ({{v}}) (multi-value variable) | a New Relic variable is substituted whole: fixed text mixed with a variable cannot be written (C_VAR_MATCHER); 'All' needs the include-all setting | DOCS | N9 |
| `mod.offset` | 13 | 2 | SINCE ... COMPARE WITH <offset> ago (outermost query only) | gives the earlier period as a second series, not a shifted series: `x / x offset 1d` has no form; N6 supports offset only if every vector has the same offset | DOCS | N1 sel-compare; R-C 4.11 |
| `name.recording_rule` | 5 | 2 | SELECT latest(`level:metric:ops`) FROM Metric | exact only if the recording rule output is itself remote-written to New Relic; otherwise the rule expression must be inlined (needs the rule file) | DOCS | R-C 4.1 |
| `selector.instant` | 684 | 2 | SELECT latest(g) FROM Metric (stat) / average(g) (chart) | series are merged unless FACET names the identifying labels; 'latest' of several series is undefined (measured: one arbitrary mount); gauge vs counter is read from the metric name | MEASURED | LIVE-C part 2 |
| `selector.range` | 185 | 2 | argument of a range function (see fn.*) | a range vector alone is not a result | n/a | - |
| `shape.counter_suffix_bare` | 35 | 2 | latest(g) for a gauge that merely ends in _count/_sum; sum(c) per bucket for a counter read raw | the type is not in the query; it comes from metric metadata (data check). A raw cumulative counter graph has no NRQL equal (measured) | MEASURED | LIVE-C what remains |
| `var.as_operand` | 218 | 2 | {{v}} (number-format variable) | a New Relic variable cannot appear in AS, LIMIT, SINCE, TIMESERIES (N9) | DOCS | N9 |
| `window.fixed` | 64 | 2 | TIMESERIES <bucket> | the PromQL window is not the bucket; rate smoothing differs (measured: Prometheus smooths over 5 m, a 60 s bucket does not) | MEASURED | LIVE-C part 2 |
| `window.interval` | 2 | 2 | TIMESERIES AUTO | as window.rate_interval | DOCS | N9 |
| `window.rate_interval` | 135 | 2 | TIMESERIES AUTO | a variable cannot go in TIMESERIES (N9) | MEASURED | LIVE-C part 2 |
| `window.variable` | 0 | 2 | TIMESERIES AUTO | a custom interval variable cannot be placed in TIMESERIES | DOCS | N9 |
| `agg.nested` | 24 | 3 | SELECT agg(x) FROM (SELECT agg2(y) AS x FROM Metric FACET ... TIMESERIES ...) [FACET ...] | TIMESERIES and FACET at any level, SINCE/UNTIL/COMPARE WITH only outermost (N4); used by 189 quickstart queries; per-step semantics need the inner and outer bucket to agree | DOCS | N4; R-C 4.0 |
| `cmp.filter.vector_scalar` | 263 | 3 | SELECT k, v FROM (SELECT <expr> AS v FROM Metric FACET k) WHERE v > n | returns rows (an instant table or stat), not a filtered time series; for a chart only a threshold line is possible | DOCS | N4; R-C 4.11 |
| `cmp.filter.vector_vector` | 2 | 3 | SELECT k FROM (SELECT latest(a) AS a, latest(b) AS b FROM Metric FACET k) WHERE a > b | comparison of two columns in WHERE is UNVERIFIED; rows, not series | UNVERIFIED | N4 |
| `fn.label_join` | 0 | 3 | FACET concat(a, '-', b) | FACET math | UNVERIFIED | R-C 4.8 |
| `fn.label_replace` | 8 | 3 | FACET capture(attr, r'(?P<c>...)') / concat(substring(...)) / cases(WHERE ... AS '...') | FACET math; usable only when the new label is used for grouping; behaviour of capture() on Metric attributes is UNVERIFIED | UNVERIFIED | R-C 4.8 |
| `fn.scalar` | 10 | 3 | SELECT x / (SELECT latest(y) FROM Metric)  (subquery as a number) | documented as a delta query on events (N2); on Metric data UNVERIFIED; scalar() of a multi-series vector is NaN in PromQL | UNVERIFIED | N2; R-C 4.11 |
| `match.explicit` | 68 | 3 | see match.on / match.ignoring | - | DOCS | R-C 4.9 |
| `match.ignoring` | 22 | 3 | as match.on with the complement of the listed labels | needs the label sets of both metrics (data) | UNVERIFIED | R-C 4.9 |
| `match.on` | 68 | 3 | A: one SELECT with a shared FACET; B: WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE ...); D: replace by the scalar | intent A and D are code 2; B needs a subquery (single key; two keys need a packed key, UNVERIFIED); C (adds a label) is group_left | DOCS | R-C 4.9; N2 |
| `set.and` | 5 | 3 | WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE metricName = 'b') | exact for a single join key; the value test inside b needs a nested aggregation; at most 3 subqueries, 5000 values (N2) | DOCS | N2; R-C 4.10 |
| `set.unless` | 0 | 3 | WHERE key NOT IN (FROM Metric SELECT uniques(key, 5000) WHERE ...) | as set.and | DOCS | N2; R-C 4.10 |
| `shape.avg_min_max_of_rate` | 10 | 3 | SELECT average(r) FROM (SELECT rate(sum(c), 1 second) AS r FROM Metric FACET dimensions() LIMIT MAX TIMESERIES <b>) TIMESERIES <b> | per-series rates first, then the aggregate; FACET dimensions() gives one series per label set (N1 func-dimensions) | DOCS | R-C 4.3 |
| `subquery` | 27 | 3 | SELECT agg(r) FROM (SELECT <inner> AS r FROM Metric FACET ... TIMESERIES <step>) | the range becomes the dashboard window (SINCE only on the outer query), so exact only when equal; per-step sliding of the outer needs SLIDE BY (UNVERIFIED on nested) | UNVERIFIED | N4; R-C 4.11 |
| `agg.bottomk` | 0 | 4 | - | FACET ... ORDER BY takes no ASC (N1) | n/a | R-C 4.6 |
| `agg.count_values` | 0 | 4 | - | no NRQL form | n/a | R-A F36 |
| `agg.group` | 0 | 4 | - | no NRQL form | n/a | R-A F36 |
| `agg.limitk` | 0 | 4 | - | no NRQL form | n/a | R-A F36 |
| `agg.stddev` | 0 | 4 | - | NRQL stddev runs over samples, not over series | n/a | R-A F32 |
| `agg.stdvar` | 0 | 4 | - | NRQL stdvar runs over samples, not over series | n/a | R-A F32 |
| `agg.without` | 183 | 4 | - | needs the label set of the metric, which only the data has (candidate with live data: FACET over keyset() minus the listed labels, at most 5) | n/a | R-A F38 |
| `binop.atan2` | 0 | 4 | - | no NRQL function | n/a | R-A F49 |
| `fn.absent` | 0 | 4 | - | an empty result is NULL in NRQL, not 1; an alert condition (loss of signal) is the equivalent, not a query | n/a | R-A F45 |
| `fn.changes` | 2 | 4 | - | counters are stored as deltas and resets are absorbed at ingest; the number of value changes has no NRQL function | n/a | R-A F16 |
| `fn.resets` | 0 | 4 | - | resets are not stored | n/a | R-A F15 |
| `fn.timestamp` | 0 | 4 | - | no documented NRQL function | n/a | R-A F51 |
| `fn.vector` | 6 | 4 | - | a constant has no series; the idiom `or vector(0)` is idiom.or_vector | n/a | R-A F53 |
| `match.group_left` | 67 | 4 | - | the label-adding intent (info metric joined for display) has no PromQL-style join; the filter-only intent is match.on B (code 3). Candidate: JOIN, UNVERIFIED on Metric ('subquery cannot be a metric row-wise query', N3) | n/a | R-C 4.9 |
| `match.group_right` | 0 | 4 | - | as match.group_left | n/a | R-C 4.9 |
| `matcher.name_label` | 8 | 4 | - | same as selector.no_name | n/a | R-C 4.1 |
| `mod.at` | 0 | 4 | - | pins evaluation to a timestamp; no NRQL form | n/a | - |
| `name.variable` | 14 | 4 | - | a variable inside a metric name: N9 lists an 'identifier' output format, but use in a function argument was not read or run; candidate only | n/a | N9 |
| `selector.no_name` | 8 | 4 | - | `{__name__=~...}` selects metrics by name pattern; NRQL names the metric in the function argument, so a pattern over names has no SELECT form (candidate: FACET metricName with count(*), which counts samples not values) | n/a | R-C 4.1 |
| `set.or` | 44 | 4 | - | a series union has no single NRQL query; two columns `latest(a) AS a, latest(b) AS b` only when the label sets agree and both exist | n/a | R-C 4.10 |
| `var.format` | 0 | 4 | - | `${x:csv}` and similar formats: a New Relic variable is substituted whole | n/a | N9 |
| `window.other` | 0 | 4 | - | an unrecognised window text | n/a | - |

Counts (this table): code 1: 26, code 2: 58, code 3: 13, code 4: 22. Several rows are shape tags for converter limitations, not PromQL features (`style.*`, `shape.*`).

Points where I was stricter than the earlier notes, so the reader can see the disagreement:

- `max`/`min` across series are code 2, not exact: R-A F30 calls them exact, but NRQL aggregates every sample in the bucket while PromQL takes one sample per series per step. They are equal only with one sample per series per bucket.
- `stddev` and `stdvar` across series are code 4 (R-A F32), although R-C 4.2 writes `stddev(g)` as exact: NRQL `stddev` runs over samples.
- A comparison filter is code 3 but UNVERIFIED for time series: N4's example filters rows of an inner query; no page read says a per-bucket filter of a time series inside a nested query works.
- `topk` is code 2 with a correctness warning. The current converter writes `... FACET pod LIMIT 5`. R-C 4.6 quotes N1 (`sel-facet`): for `sum()` and `rate()` as the first function, `FACET` picks facets by frequency, not by value; the documented form is `FACET pod ORDER BY sum(c) LIMIT 5`. If the quote is right, the 35 panels that use `topk` get the wrong series. **Check this first in the live sandbox** (one query, minutes).

## 5. Projection

Question: if every construct of verdict 1 and 2 were supported, how many census panels would convert?

Method. A construct is *supported* in a scenario if the converter writes it today or its verdict code is at most the cap. A panel *converts at the query layer* if each of its PromQL expressions either converts today or has only supported constructs; a failure that no construct explains is never counted as fixed (there are none left in this corpus). A panel *converts end to end* if, in addition, the planner gives it no reason other than the query text. The last column ignores only the "variable stays manual" gate. This is an upper bound: it assumes that constructs which are expressible alone are expressible together (a `rate` inside a nested query inside arithmetic is not proven by any of the single-construct rows), and it takes each verdict at face value, including the UNVERIFIED ones; the "MEASURED or DOCS only" row drops those.

| scenario | PromQL panels | query layer converts | end to end converts | end to end if the variable gate is lifted |
|---|---:|---:|---:|---:|
| today | 412 | 230 | 8 | 221 |
| cheap parser fixes only (quotes, upper case, huge) | 412 | 239 | 8 | 230 |
| verdict 1 and 2, any evidence | 412 | 252 | 10 | 240 |
| verdict 1 and 2, MEASURED or DOCS only | 412 | 240 | 9 | 228 |
| verdict 1, 2 and 3, any evidence | 412 | 288 | 15 | 269 |

Greedy order of construct support (verdict 1 to 3; each row adds one construct to the rows above):

| step | construct | query layer converts | with variable gate lifted |
|---:|---|---:|---:|
| 1 | `cmp.filter.vector_scalar` | 248 | 237 |
| 2 | `style.single_quoted_string` | 254 | 243 |
| 3 | `shape.counter_suffix_bare` | 258 | 247 |
| 4 | `fn.time` | 262 | 248 |
| 5 | `style.uppercase_aggregate` | 265 | 251 |
| 6 | `shape.avg_min_max_of_rate` | 268 | 253 |
| 7 | `agg.nested` | 270 | 255 |
| 8 | `fn.avg_over_time` | 272 | 257 |
| 9 | `fn.label_replace` | 275 | 258 |
| 10 | `fn.round` | 277 | 260 |
| 11 | `cmp.bool.vector_scalar` | 278 | 261 |
| 12 | `fn.delta` | 279 | 262 |

Reading it:

- Verdict 1 and 2 (any evidence): the query layer goes from 230 to **252 of 412 (61%)**; end to end from 8 to **10 (2.4%)**. With only MEASURED or DOCS patterns: 240 and 9. Adding verdict 3: 288 (70%) and 15. At the expression level the same scenarios convert 516, 502 and 580 of 857 expressions (today 492).
- So verdict 1 and 2 are worth +22 panels at the query layer. Most of the remaining 160 query-layer failures need code 3 or 4: vector matching (`on/group_left`), `without`, `or`, `subquery`, `offset`, and the comparison filters.
- The same scenarios with the variable gate lifted: 221 today, 240 with verdict 1 and 2, 269 with verdict 1 to 3 (65%).
- Community dashboards: query layer 186 of 243 today, 207 with verdict 1 and 2, 236 with verdict 1 to 3 (97%); built-in: 44, 45, 52 of 169. The community numbers are the ones to trust for a customer estate.

## 6. Design: an AST-based translator with differential testing

### 6.1 Why

The current converter (`dashboard_convert/promql.py`) is a regular-expression front end over a few shapes. The census shows its failures are a mix of real gaps and front-end limits (single quotes, upper case, `_count` names, 50 operands), and every new shape costs a new regular expression plus a mirror in the planner (`dashboards/query_tier.py`), which a test must keep in step. An AST gives one place that decides what a query means, and lets each rewrite state what it preserves. The parser exists (`promql_ast.py`, 21 tests); the rest is design.

### 6.2 Module layout (new package, next to the old path)

```
src/importer/sources/grafana/promql/            (promql_ast.py moves here as ast.py)
  ast.py          tokenizer, parser, walk, format_expr           (exists)
  normalize.py    strip parens, fold constants, resolve constant variables,
                  canonical operand order for + and *             (pure, AST -> AST)
  types.py        instant vector / range vector / scalar / string, label-set inference
                  (which labels does each subtree keep?)          (pure)
  context.py      Context: panel kind (stat/chart/table), bucket, metric type table
                  (counter, gauge, summary), name map, variables  (data, no I/O)
  plan.py         QueryPlan: select terms, where, facet, bucket, nested inner plan;
                  render() to NRQL through identifier()/quote_string()
  rules/          one module per rewrite rule: matches(node, ctx) -> bool,
                  apply(node, ctx) -> Plan fragment, PROOF (below)
  registry.py     ordered rule list; first match wins; no match -> Unconvertible(construct ids)
  cases/          differential cases: (promql, dataset, window, expected claim) as data
src/importer/sources/grafana/dashboard_convert/promql.py   unchanged; the new path is tried first
                  for shapes it covers and the old one stays the fallback until hashes are reviewed
scripts/live-test/grafana-sandbox/
  datasets.py     deterministic point generators (extends synth_counter.py): scrape interval,
                  series count, resets, gaps, gauge and count types
  ref_eval.py     the PromQL reference evaluator over the sent points (6.4)
  differential.py runs a case: send, query NR, evaluate, compare, write evidence JSON
tests/fixtures/promql-differential/                        recorded evidence (query, response, verdict)
```

Rules do not write strings: they build `QueryPlan` fragments, and only `plan.render()` produces NRQL, so the escaping functions (`identifier`, `quote_string`, `cond_sql`) stay the single exit and untrusted text never reaches an output another way. `Unconvertible` carries construct ids instead of one of the four coarse reasons, which is what the census table needs to stay current.

### 6.3 A rule carries a proof obligation

Every rule module defines:

```python
PROOF = Proof(
    claim=Claim.EXACT | Claim.BUCKETED | Claim.SHAPE_ONLY,   # what is preserved
    preconditions=[...],      # named facts about the data the rule assumes, each checkable:
                              #   "bucket is a multiple of the scrape interval",
                              #   "both operands carry the same label set",
                              #   "metric is a delta count",  "at most 5 facet labels"
    downgrade=...,            # what the translator does when a precondition cannot be shown:
                              #   add a named note (today's N_* notes) or refuse
    cases=["rate_counter_steady", "rate_counter_reset", ...],  # ids in cases/
    sources=["N1 func-rate", "LIVE-C part 1"],
)
```

and three kinds of test, all required by a registry test that fails if a rule lacks any of them:

1. A golden test: PromQL in, NRQL text out, per shape (offline, like `test_grafana_dashboard_convert_queries.py`).
2. A refusal test: for every precondition, an input that violates it must produce the downgrade (note or `Unconvertible`), never silent NRQL.
3. At least one differential case per claim. A rule that claims EXACT needs a MEASURED case with a recorded tolerance of floating-point error; BUCKETED needs the measured bound (for example "within window rounding, 1/N"); SHAPE_ONLY (the mean is right and the shape differs, as for `irate`) is allowed but forces a note on the widget. A rule with only UNVERIFIED sources can ship only behind a flag that adds the note "unverified pattern".

This turns the existing "may_be_exact" rule (only static content is exact) into something finer: exactness is a recorded measurement, not an opinion.

### 6.4 The reference evaluator (minimal)

`ref_eval.py` evaluates a PromQL AST over the exact points the harness sent, so that the expectation comes from PromQL semantics and not from the NRQL under test. Input: the point list as sent (`name`, `type`, `value`, `timestamp`, `interval.ms`, `attributes`), a start, an end and a step.

- Store: per metric and label set, a time-sorted list of samples. A `count` point (delta) is turned back into a cumulative counter by running sum (the harness already knows the starting value 0 and treats a drop as a reset), a `gauge` point is a sample.
- Instant selector at time t: the newest sample in (t - 5m, t], per series, filtered by the four matcher operators with full-string RE2 semantics (Python `re.fullmatch` on the checked subset).
- Range functions: `rate`, `increase` with Prometheus's extrapolation (`extrapolatedRate`: sampled interval, extrapolate to the window edges only up to 110% of the average sample interval, counter-reset handling); `irate` from the last two samples; `delta`, `deriv` (least squares), `*_over_time`, `changes`, `resets`. Where Prometheus's own algorithm is intricate, the evaluator carries a second mode, `nr_aligned`, that does what NRQL does on the same points (sum of deltas over the bucket divided by the bucket length), so a case can record both "equals the NRQL arithmetic exactly" and "differs from Prometheus by at most X" (the pair LIVE-C already reports by hand in `node_compare.py`).
- Aggregation `sum avg min max count topk bottomk quantile` with `by`/`without`; binary arithmetic, comparison with and without `bool`, `and or unless`, `on/ignoring/group_left/group_right`; unary minus; scalar functions `abs ceil floor round clamp*`, `time`, `vector`, `scalar`; `offset`; subqueries (evaluate the inner expression at each step). No `@`, no native histograms, no `label_replace` in the first cut (added with its rule).
- Size: about 400 lines. It is itself tested offline against hand-computed Prometheus documentation examples before any live run.

### 6.5 The differential run

For each case (data set + PromQL + window + step) the harness:

1. generates points (`datasets.py`: scrape interval, number of series, label sets, counter steady / reset / gap, gauge ramp), tagged `ckg_test=true` like the existing scripts, and sends them with `nrcommon.post_points` (create-only, owner-authorised sandbox, per session);
2. translates the PromQL with the new translator, appending the same `SINCE`/`UNTIL` pair the LIVE-C runs used, and runs it with `nrcommon.nrql`;
3. evaluates the PromQL with `ref_eval.py` in both modes over the same points;
4. aligns series on (label set, bucket start), drops the first and last bucket (partial, as LIVE-C found), and compares per bucket with the case's tolerance: EXACT within 1e-9 relative against `nr_aligned`, BUCKETED within the declared bound against `prom`, SHAPE_ONLY on the window mean only;
5. writes one evidence record (NRQL, response hash, both expectations, max error, verdict) to `tests/fixtures/promql-differential/`.

CI never needs the network: the committed evidence is replayed. An offline test recomputes the translator's NRQL for each case and checks that it equals the recorded NRQL; if a rule changes its output, the case must be re-measured and a human reviews the diff (the same discipline as golden files). The live step runs only when the owner approves it.

### 6.6 Ordered work plan: the 10 highest-value rules

Order = (panels unlocked in the community half, which is the realistic estate) x (certainty of the verdict) / (cost). Panels are query-layer counts from section 3.

0. **Front end first.** Replace the regex front end by `parse -> normalize -> plan` for every shape the old converter already writes, with the old golden tests as the acceptance bar (same NRQL text). This removes the single-quote, upper-case, 50-operand and `identifier` limits at once: +9 panels (6 + 3 sole blockers), and it is the base of everything else. Proof obligation: byte identical output on the existing fixtures.
1. **Check and fix `topk` ordering** (`FACET ... ORDER BY <agg> LIMIT n`): affects the 35 panels whose queries use `topk` (the converter writes them today). One live query decides; claim BUCKETED ("ranks over the whole window"). If the N1 quote is right, this is a correctness fix of existing output.
2. **Comparison filter on stat, table and gauge panels** (`SELECT ... FROM (SELECT ... FACET k) WHERE v > n`; `count(x == 0)` as `count(*)` over the inner rows): 108 panels contain a comparison, 18 are sole-blocked (3 end to end today), 22 community panels; of the 108, 44 are stat or singlestat panels and 6 are tables. Time series stay unconverted (threshold line) until a differential case shows the per-bucket form works. Claim EXACT for rows once measured.
3. **Metric type table instead of the name suffix heuristic** (`shape.counter_suffix_bare`, 27 panels, 9 community): the type comes from the name map or a data check (`SELECT latest(x) ... ` plus `Metric` type), so `system_cpu_count` is a gauge. Precondition: type known; otherwise keep the refusal.
4. **Average, min, max of rate across series** (`shape.avg_min_max_of_rate`, 4 community panels; the Node Exporter CPU idiom `100 * (1 - avg(rate(node_cpu_seconds_total{mode="idle"}[5m])))`): nested `FACET dimensions()` form of R-C 4.3. Claim BUCKETED.
5. **Nested aggregation** (`agg.nested`, 15 panels, 5 community): `agg(x) FROM (inner)`; this is also the chassis of rules 2 and 4 and of `subquery`.
6. **`*_over_time` with `TIMESERIES <w> SLIDE BY <step>`** (`sum`, `avg`, `max`, `min`: 20 panels) and `time()` as `aggregationendtime()` (4 panels, 3 community, 3 sole): SLIDE BY is documented (N1); claim BUCKETED.
7. **`round`, `clamp_min/max`, `abs`, `ceil`, `floor`, `sqrt`, `ln`** (9 panels): scalar functions with a table-driven mapping; `round(x, 0.001)` maps to decimals only when the second argument is a power of ten.
8. **`delta` and `deriv`** (7 panels): `derivative(g, ...)`; UNVERIFIED until measured; ship behind the "unverified pattern" note.
9. **`label_replace` / `label_join` as FACET math** (8 panels): `capture()`, `concat()`, `cases()` over `Metric` attributes; UNVERIFIED on `Metric`, so the first step is a differential case that decides whether it works at all.
10. **`on()` with an info metric used as a filter** (intent B: `WHERE key IN (FROM Metric SELECT uniques(key, 5000) WHERE ...)`, 44 panels with `on`, 4 community): exact for one join key; the label-adding `group_left` intent stays refused. Includes `and`/`unless` (`set.and`, 2 panels) as the same subquery form.

Not in the list because they cannot be static: `without` (63 panels; a metadata pass with `keyset()` could turn it into code 2, measured only by running it), `set.or`, `subquery`, `offset` with arithmetic, `group_left` that adds a label.

Each step is a small commit: failing test, rule, evidence record, census rerun (the table in section 3 is regenerated by the script, so the gain is a measured number, not an estimate).

## 7. What I did not verify

- No live query was run and no documentation page was fetched. Every verdict marked DOCS rests on the earlier notes' reading of N1 to N10; every UNVERIFIED pattern is a hypothesis.
- The projection assumes constructs compose. They may not: for example, a nested query with `TIMESERIES` plus a `rate` plus a division has no single-construct evidence.
- The census treats each target as one expression; library panels not in the export, `-- Dashboard --` reuse and server-side expressions are not counted (they are planner reasons).
- Panel types that are not charts (gauge, bar gauge, bar chart: 12 PromQL panels) are blocked by "the importer cannot create the widget yet", which is also not PromQL.
- The 13 built-in dashboards are Grafana Cloud internals; 6 community dashboards is a small sample of community practice. R-C measured 108 dashboards for shape frequencies and should be preferred for population estimates; this note's value is the exact blocked-panel accounting against the current converter.
- The `topk` ordering concern (section 4) is from a quotation in R-C, not from a run.

## 8. Recommendation

A better translator is worth building, but not first. The order is: (1) settle the variable gate (nested variables, N10), because it holds back 389 of 412 PromQL panels and no PromQL work shows in the end-to-end number until it moves; (2) run the one-query `topk` check, because it may mean today's output is wrong; (3) then replace the front end by the AST and add the rules above in order, each with a measured case. Expect +22 query-layer panels from verdicts 1 and 2, about +58 with code 3 (the nested forms), and a ceiling near 70% of this corpus (97% of the community half) before the vector-matching and `without` constructs, which need data or have no NRQL form.
