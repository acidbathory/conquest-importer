# PromQL prior art (research round 1, 2026-11-16)

Method: shallow/sparse clones in /tmp/community/promql-prior-art/, public docs only, nothing run or built. GitHub search API was rate-limited (60 calls/h unauthenticated), so converter discovery is partial. Items marked UNVERIFIED come from memory or search snippets and were not opened.
Licence rule: we are Apache-2.0. Apache-2.0/MIT/BSD content may be reused later as test fixtures (marked FIXTURE-OK). AGPL/ELv2/GPL/unlicensed: facts only.

## 1. New Relic and PromQL (item 4)

Answer: New Relic does NOT evaluate PromQL natively anywhere found in public docs. Every PromQL surface is a translation to NRQL, executed by NRDB with NRQL semantics.

Surfaces (all cite /tmp/community/promql-prior-art/nrdocs = newrelic/docs-website develop, sparse):
- Query builder "PromQL-style" mode: src/content/docs/infrastructure/prometheus-integrations/view-query-data/supported-promql-features.mdx L18 ("translates PromQL syntax queries into the closest NRQL approximation", "over 99.5% of queries across the 7.8 million top Grafana dashboard downloads"); view-query-your-prometheus-data.mdx L427-431 (query builder menu, PromQL, auto-translated to NRQL, Submit). Release note whats-new 2025-04-07 "PromQL translator now in the query console" (docs.newrelic.com/whats-new/2025/04/whats-new-04-07-promql-translator/; query console translates to NRQL).
- Prometheus-compatible query API (Grafana data source): query-your-data/grafana-integrations/set-configure/configure-new-relic-prometheus-data-source-grafana.mdx L38-42 (prometheus-api.newrelic.com, .eu., .jp.), headers X-Query-Key or api-key (L42, L92). grafana-support-prometheus-promql.mdx "Support for PromQL": "emulates Prometheus' query APIs ... automatically translate PromQL syntax queries into the closest NRQL approximation". So it is also translation.
- Documented dashboard translate endpoint: view-query-data/grafana.mdx L26-28: POST https://promql-gateway.service.newrelic.com/api/v1/translate/dashboard/grafana (eu / jp variants), user API key in `api-key`, body {accountId?, dashboard}, returns NR dashboard; untranslatable widgets become Markdown "Action Items" (L96-100 approx). Widget map table follows.
- Undocumented-but-public single-query endpoint: POST https://promql-gateway.service.newrelic.com/api/v1/translate with {promql, step, isRange, start, end, account_id}; response field `nrql` (seen in third-party repo greenszpila/promQLtoNRQL app.py L7, L88-110; unlicensed, facts only). Not in official docs. Could not verify live (no credentials).
- Alert conditions: grep of src/content/docs/alerts for promql/prometheus returned nothing. No native or translated PromQL alert condition is documented. Alerts are NRQL only.
- translate-promql-queries-nrql.mdx: manual PromQL-to-NRQL examples (L131-530), mapping table; useful as fixtures of NR's own idea of equivalence (docs content licence not checked; treat as facts-only).

Documented limits (supported-promql-features.mdx):
- Functions L35-157: aggregations avg count min max quantile stddev stdvar sum topk; histogram_quantile; *_over_time avg count last min max quantile stdev(sic) stvar(sic) sum; arithmetic + - * / % ^; and/or/unless; date/time fns; math fns abs ceil clamp_max clamp_min exp floor ln log10 log2 round sqrt; delta deriv idelta increase irate rate; predict_linear; sort, sort_desc; label_join; Grafana label_values. NOT listed: bottomk, count_values, group, absent, changes, resets, label_replace, histogram_* (other), subqueries, @ modifier, on/ignoring/group_left (not mentioned either way: UNKNOWN), limitk, atan2/trig, holt_winters, scalar/vector.
- offset only if every vector has the same offset (L139-141).
- Semantics differ: NRDB converts accumulating counters to delta counters, so rate/increase/delta on the wrong metric type give "different or incorrect answers" (L164-170).
- 366 steps max per range query; 100 series returned by default, topk() overrides and "topk() in our PromQL-style query is different from Prometheus" (L174-175); memory caps, unique count/quantile heavier (L176).
- Instant queries look back only 1 minute (not Prometheus' 5 minutes); scrape interval above 1 min gives "No data" (L187). Step must be smaller than range (L186). Sliding windows used for range vectors (L180).
Implication for us: PromQL-as-oracle on New Relic is not available; the only NR "oracle" is the NRQL the gateway emits (and NRDB's answer to it). The gateway's NRQL output is a candidate comparison target (needs an API key and a live account, not done here). Elastic can wrap PromQL natively (below), NR cannot.

## 2. Converters and native PromQL engines

| Name / URL | Licence | Approach | Subset and notes | Relevance |
|---|---|---|---|---|
| Elastic ES|QL `PROMQL` source command; docs raw.githubusercontent.com/elastic/elasticsearch/main/docs/reference/query-languages/esql/_snippets/commands/layout/promql.md and .../promql/_snippets/compatibility/{basics,operators,functions}.md; code in elastic/elasticsearch x-pack/plugin/esql (not cloned) | repo GitHub licence NOASSERTION (ELv2/SSPL/AGPL mix): facts only | Native PromQL parser compiled into ES|QL time-series plan (TS command) | preview 9.4, GA 9.5. Refuses with 4xx: absent, resets, info, histogram_stddev/stdvar, atan2, step()/range()/start()/end(), x[5m] as instant result, comparison with scalar first / two vectors / nested comparison (rows 15-19, 41-55). Row 59: rate/increase "computed over fixed time buckets, interpolated at bucket boundaries, can differ slightly" vs Prometheus extrapolation per window. Row 58: quantile_over_time approximate (t-digest). Row 56: absent_over_time returns no results. Implicit range selector = max(step, scrape_interval). Best published example of a "behaves differently" table: copy the idea (a numbered list of unsupported and differing constructs with workarounds). |
| tsouza/cerberus https://github.com/tsouza/cerberus | Apache-2.0 (FIXTURE-OK for docs/tests; code not to be copied) | PromQL/LogQL/TraceQL to ClickHouse SQL gateway, plan IR then SQL (internal/chplan, chsql) | Uses native ClickHouse timeSeriesRateToGrid aggregates for rate/increase/delta in range queries; hand SQL for the extrapolation clamp; left-open window; docs/compatibility.md. Verified against real Prometheus with the prometheus/compliance tester and a parity ratchet that fails when any case regresses. | Best design to copy: differential harness against a real engine plus a committed per-case baseline roster (compatibility/parity-baseline/manifest.json) with a regression ratchet. Its docs mention extrapolation, laginframe adjacency, left-open edge: read docs/clickhouse-optimizations.background.md L830-890 for pitfalls (paraphrase only). |
| timescale/promscale (deprecated, archived 2024) https://github.com/timescale/promscale ; specs https://github.com/timescale/promscale_specs | Apache-2.0 (promscale_extension: NOASSERTION) | PromQL pushed down to Postgres/Timescale via SQL functions | Not opened. UNVERIFIED subset. | Prior art for PromQL to Postgres. |
| wubin1989/promql2influxql https://github.com/wubin1989/promql2influxql | MIT (FIXTURE-OK) | AST rewrite of Prometheus parser output to InfluxQL (Go) | README (cloned): 24 of 70 functions supported, no binary ops where both sides are selectors, no multi-measurement queries. | Shows what an AST-rewrite to a weaker language refuses; cheap list of refusals. |
| metrico/promql2logql | AGPL-3.0 (facts only) | JS transpiler PromQL to LogQL for qryn | Not opened beyond README/licence. | LogQL direction; low value. |
| greenszpila/promQLtoNRQL | no licence (facts only) | thin Flask wrapper over NR promql-gateway /translate | 115-line app.py | Reveals the gateway request/response shape (see section 1). |
| PromSQL adavoudi/PromSQL, fil-ganichev/sql-prometheus, RaghavVijayanand/promql_to_sql (MIT), preetham02/rolex (PromQL to SQL++) | mixed/none | search hits, not opened | UNVERIFIED | Low-star hobby projects. |
| New Relic gateway (see section 1) | closed | rule-based PromQL to NRQL, counters treated as deltas | see limits | The thing we are competing with / complementing. |
| Not found in the search (rate limit and no hits): PromQL to DQL, to KQL / Azure Managed Prometheus (Azure Managed Prometheus accepts PromQL natively: UNVERIFIED), to Datadog queries, to Cloud Monitoring MQL (GCP Managed Prometheus accepts PromQL natively: UNVERIFIED), to Perses (perses/perses ships a Grafana migration with PromQL pass-through, Apache-2.0: UNVERIFIED), reverse Datadog/NRQL/MQL to PromQL. Not covered by a second round; no signs of an open source tool. | | | | |
| LLM/text-to-PromQL papers: arXiv 2503.03114 PromCopilot (2025), arXiv 2604.13048 catalog-driven NL to PromQL (2026), PromQLAgent (IEEE ICAIBD 2026, DOI 10.1109/icaibd69640.2026.11637335) | paper licences not checked | LLM | Generation, not translation; not opened. | Not an oracle. No formal-semantics or PromQL-to-SQL paper turned up in arXiv/Crossref searches. |

## 3. Parsers and tooling (item 2)

| Name | Licence | Notes for our hand-written parser |
|---|---|---|
| prometheus/prometheus promql/parser (Go, yacc grammar) https://github.com/prometheus/prometheus | Apache-2.0 | Reference grammar and function table (parser/functions.go). Use as the source of truth for what is valid; cannot be called from Python directly. FIXTURE-OK. |
| GreptimeTeam/promql-parser (Rust) | Apache-2.0 | Port of Prometheus grammar with AST and visitor; cloned. |
| messense/py-promql-parser (PyO3 over the Greptime crate) | MIT | Only AST library usable from Python. Could replace our regex normaliser's parse step or serve as a cross-check ("does our parser accept exactly what Prometheus accepts"). Adds a native wheel dependency: weigh against our "no installs" posture; it can be a dev-only test oracle. Not installed or opened for API shape. |
| VictoriaMetrics/metricsql (Go) | Apache-2.0 | MetricsQL superset parser; accepts many non-Prometheus constructs, so it is a worse correctness reference. |
| thanos-io/promql-engine | Apache-2.0 | Independent engine; tests compare against the Prometheus engine and fuzz with promqlsmith (cortexproject/promqlsmith, query generator: UNVERIFIED licence). engine/engine_test.go has `Load` + `Query` + expected tables in the promqltest load syntax. Design to copy: differential fuzzing against the reference engine. |
| Other Rust crates: vthriller/promql (Apache-2.0), ccollie/metricsql (Apache-2.0), SINTEF/rusty-promql-parser (Apache-2.0, 2026), lmangani/rust-promql-parser-js (MIT, Node binding) | as listed | Not opened. |
| prometheus-community/promql-langserver | Apache-2.0 | LSP: diagnostics; not opened. |
| prometheus/promlens | Apache-2.0, archived (merged into Prometheus UI) | Query explainer; not opened. |
| yeya24/promlinter | Apache-2.0 | Lints metric names; not about query semantics. |
| grafana/dashboard-linter | Apache-2.0 | Lints dashboards incl. PromQL via Prometheus parser; a possible corpus of lint rules. Not opened. |
| grafana/mimir (mimirtool analyze) | AGPL-3.0 | Extracts metric names from dashboards/rules via the Prometheus parser; facts only. |
| perses/promql-builder | Apache-2.0 | Go builder, not a parser. |
Where an AST library could help us: (a) vector matching clauses on/ignoring/group_left, (b) subqueries `[5m:1m]`, (c) `@` modifier and `offset` placement, (d) unary minus, precedence and `bool` modifier, (e) string escapes in label matchers. Use py-promql-parser (MIT) as a test-time cross-check, not as a runtime dependency, unless the owner approves a native dependency.

## 4. Oracles and corpora (item 3)

### prometheus/prometheus promql/promqltest/testdata (cloned: /tmp/community/promql-prior-art/prometheus)
- Licence Apache-2.0 (root LICENSE). FIXTURE-OK as test data with NOTICE retained; do not paste into code.
- 23 .test files, 13,651 lines, 346 `load` blocks, 2,551 `eval` commands (2,545 eval + 5 eval_fail + 1 eval_warn; newer files use `expect fail|warn|no_info` lines).
- Format (README.md in promqltest and the files): `load <interval>` followed by lines `series  points` using expanding notation (`0+10x9`, `_` gap, `stale`, `a+bxN`, native histogram `{{...}}`); `eval instant at <time> <expr>` or `eval range from X to Y step Z <expr>`; expected lines `{labels} value` (or `{labels} 1+1x3` for range); empty expectation means no result; `clear` resets. Single-line queries, so a line parser works. Estimated effort: about 200-300 lines of Python for load expansion plus eval/expected parsing, plus mapping to our Series/Vector types.
- Counts by file: functions 541, native_histograms 557, operators 214, histograms 195, extended_vectors 174, aggregators 167, bitwise 87, at_modifier 78, info 70, duration_expression 65, type_and_unit 65, fill-modifier 50, subquery 41, start_timestamps 39, limit 37, selectors 31, name_label_dropping 30, unquoted_utf8_names 27, literals 25, trig 21, range_queries 18, staleness 17, collision 2.
- Usable subset for a classic-float evaluator (core_view of 14 files: aggregators, at_modifier, bitwise, functions, histograms, operators, range_queries, selectors, staleness, subquery, trig, literals, limit, name_label_dropping): 1,502 evals; roughly 100-180 of them contain native-histogram `{{` syntax and must be skipped. Excluded as 3.x experimental syntax: extended_vectors (anchored/smoothed), info, duration_expression, fill-modifier, type_and_unit, start_timestamps, unquoted_utf8_names, native_histograms.
- Feature counts over the 1,502 core evals (regex on the query line, overlapping, so not additive): *_over_time 269, aggregation operators 236, rate 113, subquery 106, time/date fns 98, @ modifier 92, histogram_quantile 87, other histogram_* 72, clamp/round 57, set ops 50, vector matching (on/ignoring/group_*) 49, absent 43, offset 40, label_replace/join 33, resets/changes 25, delta/idelta 21, predict_linear/deriv 20, sort 18, irate 11, increase 8. Staleness proper lives in staleness.test (17 evals) plus `stale` markers in load. Over all 2,551 evals: rate 161, increase 124 (most native histograms), histogram_quantile 161.
- Caveat: expected values are Prometheus' own (extrapolated rate, 5 min lookback, staleness, left-open range windows). They validate our PromQL reference evaluator (diff_promql.py), not the NRQL-semantics model. Our evaluator's `_extrapolated` (diff_promql.py L254-275) uses the same structure as Prometheus' extrapolatedRate (1.1x average interval cut-off, zero-point clamp for counters); the corpus will test the edge cases (counter reset in middle, zero cutoff at promqltest functions.test L195-215, native-less cases). Fine-grained facts to check against: Prometheus 3.x range windows are left-open (we do `start < t <= end`, matches).
- So: the corpus can be a real independent oracle for the left half of our ladder (is our PromQL evaluator right?). It cannot say whether NRQL output matches PromQL semantics.

### prometheus/compliance promql (PromLabs tester) (cloned: .../compliance, sparse /promql)
- Apache-2.0. promql/promql-test-queries.yml: 146 query templates (variant args expand to more), but NO expected results: it diffs a reference Prometheus against a target on live data (demo.promlabs.com service or self-run docker image). Needs a running Prometheus: not available offline. Useful as a query-shape corpus (which constructs vendors usually get wrong). The older repo promlabs/promql-compliance-tester (Apache-2.0, last push 2022) is superseded.
- cerberus runs it in CI against real Prometheus; its seed fixture is the model for ours.

### Others
- thanos-io/promql-engine engine/*_test.go: small Load/Query/Result tables (Apache-2.0), plus promqlsmith fuzzers. Not counted.
- Cortex, Mimir (AGPL), VictoriaMetrics test suites: not opened (facts-only for Mimir). Mimir and Thanos both reuse the upstream promqltest files ("run upstream tests against our engine") per memory: UNVERIFIED.
- Real-query datasets: none found. The NR claim "7.8 million top Grafana dashboard downloads" is not downloadable. Our own corpus (Grafana dashboards via corpus-grafana agents) is likely the best real-world set.

## 5. Academic / engineering write-ups
- No formal semantics or PromQL-to-SQL paper turned up (arXiv API and Crossref queries for "promql": only text-to-PromQL LLM papers listed above). Known engineering posts (PromLabs "Comparing PromQL Correctness Across Vendors", rounds 2 and 3; Chronosphere, Grafana, Cortex posts) not fetched: UNVERIFIED. PromLabs results page https://promlabs.com/promql-compliance-tests/ is public (2022-05-19 results listing).

## Not verified
- Live behaviour of the NR gateway and whether /api/v1/translate is stable or supported; any NR alert-condition PromQL feature added after the docs checkout (develop branch as cloned today).
- Converters for DQL, KQL, Datadog, MQL, Perses: not searched successfully (API rate limit).
- Native-histogram share inside the 1,502-eval core subset is estimated from `{{` line counts, not from a parse.
