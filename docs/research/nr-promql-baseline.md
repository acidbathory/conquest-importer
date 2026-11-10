# Baseline: New Relic's PromQL translator on our dashboard expressions

Measured 2026-11-10 in the owner's sandbox account. Script: `scripts/live-test/grafana-sandbox/promql_baseline.py`. Raw results stay in `out/promql-baseline/` (git-ignored); this note holds the numbers and the evidence.

## 1. What was called

- Endpoint (docs page `infrastructure/prometheus-integrations/view-query-data/grafana.mdx`): `POST https://promql-gateway.service.newrelic.com/api/v1/translate/dashboard/grafana`, header `api-key` (user key), body `{"accountId": int, "dashboard": <Grafana dashboard JSON>}`. EU and JP hosts exist. The call only returns JSON; creating a dashboard is a separate NerdGraph `dashboardCreate` the docs show and we never ran. Nothing was created or edited.
- There is no documented expression-level translate endpoint. The translator takes a dashboard, so each batch of 40 expressions was wrapped in a synthetic dashboard of timeseries panels (title `e<id>`, one target each, no Grafana tenant data). The page lists no request-size limit; we used 10 requests, cap 20 in the script.
- The other route, the Prometheus-compatible query API (`prometheus-api.newrelic.com`, `X-Query-Key`), was not called: it returns data for a query, not the NRQL, and the owner's approval covered the translator and read-only NRQL.
- Corpus: 474 `expr` targets in the 6 public dashboards (1860, 13332, 14282, 9628, 9614, 4701), 463 distinct as written. Variables were made literal (`$__rate_interval` to `5m`, regex matchers to `.+`, other values to `ckg_value`, bare numeric to `1`). After that, more than 400 distinct expressions remained, so the script capped at 400 by thinning dashboard 1860 evenly (291 distinct as written, 231 kept; the other five are complete). Kept per dashboard: 1860: 231, 4701: 54, 13332: 52, 9628: 40, 9614: 17, 14282: 6.
- Our side: `convert_query(expr, "prometheus", QueryOptions(variables={}, name_map=NameMap(), timeseries=True))` on the same literal text, offline. This compares expression to expression. It does not include our panel-level logic (legend facets, variables as dashboard variables, unit and display handling).
- Every translated NRQL was also run once read-only (NerdGraph `nrql`) to check it parses and runs.

## 2. Counts

| | count |
|---|---|
| Expressions sent | 400 |
| Translated by New Relic | 398 |
| Rejected by New Relic | 2 (`changes()`: "changes is not currently supported") |
| HTTP or transport errors | 0 |
| Translated NRQL that runs | 397 of 398 |

Comparison with our converter:

| class | count |
|---|---|
| both convert | 328 |
| only New Relic converts | 70 |
| only we convert | 0 |
| neither | 2 (both `changes()`) |

| dashboard | both | NR only | neither |
|---|---|---|---|
| 1860 Node Exporter Full | 197 | 34 | 0 |
| 13332 kube-state-metrics | 38 | 12 | 2 |
| 4701 JVM Micrometer | 50 | 4 | 0 |
| 9628 PostgreSQL | 26 | 14 | 0 |
| 9614 NGINX Ingress | 12 | 5 | 0 |
| 14282 cAdvisor | 5 | 1 | 0 |

Our 72 refusals: 68 are "shape the planner reads but the converter does not write", 4 are "an average, minimum or maximum of rates has no correct New Relic form".

What the 70 "only New Relic" expressions are made of:

| construct | count |
|---|---|
| comparison used as a filter (`== 1`, `!= 0`, `> 1`, `a == b`) | 16 plus 2 |
| plain selector with a negative regex matcher (`fstype!~'rootfs'`) | 8 |
| `avg(rate(...))`, `avg(sum without(...)(rate(...)))` | 7 |
| `scalar(...)` and `count(count(...) by (cpu))` | 7 |
| `on(...)` / `group_left(...)` | 7 |
| guard `x / (y > 0)` | 6 |
| upper-case `SUM(`, `time()`, `round`/`clamp_min`, `avg_over_time`/`delta`, `bool`, `sum without`, others | 3, 3, 3, 3, 1, 1, 3 |

## 3. Method for correctness

Syntax: 397 of 398 outputs run. The one failure is `(time() - metric{...}) / 86400 > 1`: the translator writes `aggregationendtime()` inside `WHERE` too, and NRQL answers "Unknown function aggregationendtime()". The same function in `SELECT` alone (`time() - x`) runs.

Semantics: 25 "only New Relic" outputs were read against PromQL meaning (table in section 5). Where possible they were run against the node_exporter test data already in the account (`ckg_test = 'true'`: 12 cpus, 4 modes, 9 filesystem series, an 18 second interval, 7 minutes of data at 2026-11-10 02:33 UTC). The test data stores counters as deltas, the way the Metric API sees them from our sender.

## 4. Findings

Right (confirmed by running):

- `sum(gauge)` is written `average(x) * cardinality(x)`. On `node_filesystem_size_bytes` this gave 2 473 496 842 240, exactly the sum of the nine series. It is correct when values are steady inside the bucket and an approximation otherwise.
- `scalar(x) * 100 / count(count(node_cpu_seconds_total) by (cpu))` and `sum(rate(cpu{mode="system"}[5m])) / scalar(count(count(...) by (cpu)))` become nested subqueries with `cardinality(...) ... FACET cpu`. Run with the test data they returned the cpu count 12, and the rate-over-cpus series was identical to the hand-written `rate(sum(x),1 SECONDS)/uniqueCount(cpu)` (0.004652777...), and the load series matched `latest(node_load1)*100/12` (40.641...).
- Filters: `x == 1`, `x != 0`, `x > 1` become `ifthen(cond, value)` and an extra `WHERE getfield(x, latest) cond`. The statement runs, and a threshold nothing meets returns nothing, as PromQL does.
- `x / (y > 0)` becomes `x / ifthen(y > 0, y)`: right (null instead of dropping the point).
- `increase(c[5m])` is `sum(c)` over a 5 minute `SLIDE BY 15000` window, `avg_over_time` is `average` over a sliding window, `histogram_quantile` is `bucketpercentile(..., 100 * q)` with `FACET` on the `by` labels, `clamp_min`, `time()` in `SELECT`, `delta` as `latest - earliest`.
- Regex: `=~".*"` becomes `(a RLIKE '.*' OR a IS NULL)`, which keeps series without the label as PromQL does. A detail we should copy.

Wrong or unsafe:

1. `avg(rate(c[5m]))` becomes `rate(average(c), 1 SECONDS)`. `average` is the mean of the datapoints, not the sum, so the value is divided by the number of scrapes in the bucket. Run on the test data (12 cpus, 5 minute bucket): New Relic form 0.00441, true average of per-cpu rates 0.0883, a factor of 20 (300 s / 15 s). This is why our converter refuses the form. Seven expressions use it and look plausible but are about 20 times too small.
2. `avg(sum without(mode)(rate(c)))` puts `rate(sum(c))` in a subquery with no `FACET cpu`, so the outer average is over one series: the result is the total, not the per-cpu mean (too large by the cpu count). Reasoned from the query text.
3. Plain selectors (`node_filesystem_avail_bytes{...}`) and arithmetic between selectors get no `FACET`. 370 of the 398 outputs have none, about 300 are not aggregations at the top level. Prometheus returns one series per label set; New Relic merges them. Run: nine filesystem series gave one point of `524288000` (an arbitrary one of the nine). `size - avail` and `a == b` have the same defect. Only an explicit `by (...)` or a `group_left` match gets `FACET`.
4. `sum without (cpu)(rate(x))` produced `rate(sum(x),1 SECONDS)` with no `FACET`: every remaining label is collapsed. Wrong whenever more than the dropped label varies.
5. `round(v, 0.001)` became `round(v)`: the precision argument is dropped, so rates between 0 and 1 turn into 0 or 1. Reasoned from the text.
6. Time range baked in: all 398 statements end in `SINCE 60 MINUTES AGO UNTIL NOW`, and all use a fixed `TIMESERIES 15000` (15 s) or a window with `SLIDE BY 15000`. An NRQL `SINCE` in a widget overrides the dashboard time picker (NRQL behaviour; not tried in a dashboard because we create nothing). A migrated dashboard would not follow the picker. Fixing it means stripping `SINCE ... UNTIL ...` and using `TIMESERIES AUTO`.
7. `LIMIT 100` on every faceted or plain statement (default): a hidden cap on series. Ours uses `LIMIT MAX`.
8. `time()` inside a filtered expression writes `aggregationendtime()` in `WHERE`: invalid NRQL (1 of 398).
9. `on(chip) group_left(chip_name)` (hwmon, 5 panels) becomes `latest(a) * latest(b) ... FACET tuple(chip, chip_name)`, but `chip_name` exists only on the second metric, so the first has a null `chip_name` and the facets cannot line up. Not testable here (no hwmon data). Doubtful.
10. Failed queries: the translator keeps the widget and replaces the query with a comment plus a dummy `FROM Metric SELECT count(*) WHERE dataType = 'CHANGEME'`, so the widget exists and draws a flat line. The error text is in the comment and in an "Action Items" markdown widget. Detecting failure means looking for the comment.
11. Counter type: the translator assumes data written by the Prometheus remote-write or OpenMetrics path, where counters are deltas. A counter we write elsewhere (or a gauge named `_total`) gets `rate(sum())`, which is wrong for a true gauge (documented in the supported-features page).

## 5. The 25 inspected outputs (only New Relic converts)

Verdict: R right, A approximate (right for one series), W wrong. "run" means checked against the test data; "text" means reasoned from the statement.

| id | PromQL (shortened) | New Relic form (shortened) | verdict |
|---|---|---|---|
| 15 | `kube_node_status_condition{...}==1` | `ifthen(latest(x)=1, latest(x))` + `WHERE getfield(x,latest)=1` | R, run on a stand-in metric; A with several series (no FACET) |
| 41 | `sum(hpa_current{hpa=~".*"})` | `average(x)*cardinality(x)` with `hpa RLIKE '.*' OR hpa IS NULL` | R, run (idiom) |
| 42 | `current == max` | `ifthen(latest(a)=latest(b), latest(a))` | A (no label matching, no FACET) |
| 45 | `(time()-x)/86400>1` | `aggregationendtime()` in SELECT and WHERE | W: query does not run |
| 49 | `sum(phase{...}==1)` | subquery `AS __result_0`, outer `average*cardinality(conditional(...))` | R by text |
| 57 | `(time()-start)/86400` | `(aggregationendtime()/1000 - latest(x))/86400` | A (no FACET), runs |
| 62 | `scalar(load1)*100/count(count(cpu) by (cpu))` | nested `cardinality ... FACET cpu` | R, run (40.64 equal) |
| 63 | `clamp_min((1 - avail/total)*100, 0)` | `clamp_min(...)` | R by text |
| 64 | swap used percent with `/ (total > 0)` | `ifthen(total>0, total)` | R by text |
| 70 | `avg(rate(cpu{mode="user"}[5m]))` | `rate(average(x), 1 SECONDS)` | W, run: 20 times too small |
| 72 | `avg(sum without(mode)(rate(...)))` | outer `average` over unfaceted `rate(sum())` | W by text (total, not mean) |
| 80 | `sum(rate(cpu{mode="system"}[5m])) / scalar(count(count(cpu) by (cpu)))` | rate over conditional sum / cpu-count subquery | R, run: identical series |
| 86 | `sum by(instance)(rate(guest)) / on(instance) group_left sum by(instance)(rate(cpu))` | `ifthen` guard, `FACET instance` | R by text (long; not run) |
| 93 | `(speed > bool 0) * (rate(tx)/speed)` | `(ifthen(speed>0,1) OR 0) * ...` | A: `OR 0` as a null filler is plausible, not proven |
| 97 | `filesystem_avail{fstype!~'rootfs'}` | `latest(x)` no FACET | W for multi-series, run: 9 series merged to 1 |
| 100 | `size - avail` | `latest(a)-latest(b)` no FACET | W for multi-series (same defect) |
| 152 | `rate(waiting)/(rate(running)+rate(waiting))` | `rate(sum())` ratio with guard | A: per-cpu series merged |
| 156 | `sum without(cpu)(rate(interrupts))` | `rate(sum(x),1 SECONDS)` | W: other labels collapsed |
| 159 | `temp * on(chip) group_left(chip_name) chip_names` | `latest(a)*latest(b) FACET tuple(chip, chip_name)` | doubtful (facet label on one side only) |
| 162 | `100*cur/(max>0)` | `100*latest(a)/ifthen(latest(b)>0, latest(b))` | R, A with several series |
| 318 | `avg_over_time(cpu_usage[15m])` | `average(x) ... TIMESERIES 900000 SLIDE BY 15000` | R by text |
| 339 | `delta(gauge[1m])` | `latest(x)-earliest(x)` sliding 1 minute | A: no extrapolation as in Prometheus |
| 343 | `round(sum(irate(req[2m])), 0.001)` | `round(avg(irate)*cardinality)`, no precision | W: precision dropped |
| 359 | `avg(ssl_expire) by (host) - time()` | `average(x) - aggregationendtime()/1000 FACET host` | R, runs |
| 379 | `active_count != 0` | `ifthen(NOT (latest(x)=0), ...)` + `NOT getfield(...)=0` | R by text |

Tally: R 12, A 5, W 7, doubtful 1.

## 6. What New Relic does that we do not, and what to borrow

New Relic converts 70 expressions we refuse. Borrowable techniques, all visible in the output above:

- Comparison filters: `ifthen(cond, value)` plus a `WHERE getfield(x, latest) cond` so empty buckets are dropped. It covers `==`, `!=`, `>` against a constant or another selector, and `> bool`.
- `x / (y > 0)` as `x / ifthen(y > 0, y)`.
- `sum(gauge)` as `average(x) * cardinality(x)`, and `count(vector)` as `cardinality(...)` of a faceted subquery. This is how `count(count(x) by (cpu))` and `scalar(...)` normalisations (7 panels in dashboard 1860) become a nested `(SELECT ... )` scalar.
- `time()` as `aggregationendtime() / 1000`, only in `SELECT`.
- `on(...)`/`group_left(...)` as `FACET tuple(a, b)` with both sides in one SELECT. Needs care (item 9).
- `=~".*"` as `RLIKE '.*' OR attr IS NULL`; negative regex matchers on plain selectors (8 panels we refuse).
- `histogram_quantile` as `bucketpercentile(..., 100*q)`; `increase` as a sliding `sum`.
- `SLIDE BY` windows to keep `[5m]` ranges while drawing at a fine step. We use `TIMESERIES AUTO` and lose the range.

Things not to borrow: unfaceted plain selectors, `avg(rate())`, dropped `round` precision, the baked-in `SINCE`, `LIMIT 100`, `aggregationendtime()` in `WHERE`, silent placeholder queries. Our refusal of `avg(rate())` is correct, and measured.

Where we are ahead: nothing converts only on our side (0 of 400), but our output is safer in four ways: refusal where New Relic is wrong (7 `avg(rate())` panels), facets from legends, `LIMIT MAX`, and no baked-in time range.

## 7. Can the translator or the Prometheus-compatible API be used for migration?

- Dashboards, as an oracle and a second opinion: yes. The translator is a documented, read-only, per-dashboard call. It covered 398 of 400 expressions, but 8 of the 25 outputs we inspected are wrong or doubtful (section 5), and every output bakes in the time range. Using its output directly would put wrong numbers on migrated dashboards without a signal. It is useful to find constructs to implement (section 6) and as a regression comparison. If used in the product it needs the same labelling as our own output (APPROXIMATE) plus fixes: strip `SINCE`, add `FACET` for multi-series selectors, refuse `avg(rate())`, check for the placeholder comment.
- Alerts: no. The translator emits dashboard widgets, not alert conditions. Conditions need an NRQL that is one number per window and the converter's alert rules (refuse counter-looking names, no joins). The translator's `ifthen`/`getfield` forms, `SLIDE BY` and `LIMIT 100` are not suitable as condition queries without a rewrite. Not tested here (no alert objects were created).
- Prometheus-compatible query API: viable only for keeping Grafana, where Grafana's data source points at New Relic and PromQL stays PromQL (same translator underneath, so the same defects, plus the 366-step and 100-series limits). It does not migrate dashboards or alerts into New Relic objects, and it was not called here.

## 8. Reproduce

```
SSL_CERT_FILE=/tmp/q/ca.pem uv run python scripts/live-test/grafana-sandbox/promql_baseline.py <dir with d*.json> --validate 400
```
Add `--offline` to recompute the comparison from `out/promql-baseline/translated.json` without the network. Caps: 400 expressions, 20 translator requests (40 expressions each), 400 read-only NRQL statements. TLS verification is on; keys come from the environment (the repository `.env` is read) and are never printed or stored.
