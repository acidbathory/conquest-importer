# Grafana source gaps vs the New Relic converter

Method: facts only were read from a shallow, sparse clone of grafana/grafana (AGPL-3.0, HEAD of 2026-11-12) in
/tmp/community/grafana-src/src. Nothing was copied; no downloaded file was executed. Counts come from
/tmp/gfcorpus (183 dashboard files that parse; the converter reads them as 536 dashboards, 3,563 data widgets) and
/tmp/community/grafana/in-r3 (200 rules: 160 Grafana-managed, 40 Prometheus rule-file style). Scripts are in
/tmp/community-tools/ (gfalerts.py, gfalerts2.py, gfshape.py, gfdash.py). Converter runs used the integrate-1111
worktree read-only: alerts 31 of 200 convert; dashboards 1,282 of 3,563 data widgets (2,281 placeholders). Ceilings
below are "sole-blocker" counts from those runs: items whose only recorded reason is the gap, so they would convert if
the gap closed and nothing else blocks them.

## Bad news first

1. Corpus bias. 34 of 36 single-query Grafana rules come from two repos (19 and 15 rules). Alert rankings move a lot
   if one repo is dropped. The 200-rule sample is small; treat alert numbers as +/- 10.
2. Most dashboard loss is not a converter bug but a New Relic limit (joins, gauge-sum semantics, browser-side
   transformations) or a different source (CloudWatch behind a Grafana panel).
3. No NR doc URL below was re-fetched in this job (no web agent run); they are from memory and marked unverified.

## Alert rules: what Grafana can express (from source)

- Rule model fields: condition (refId of the node that decides), data (list of query and expression nodes with
  relative time ranges `from`/`to`), for, keep_firing_for, no-data state (NoData, Alerting, OK, KeepLast), error state
  (Error, Alerting, OK, KeepLast), labels, annotations, notification settings per rule (receiver, group by, mute
  timings, repeat interval, or a named policy), recording-rule variant (record target metric, destination data
  source, remote-write batching), missing-series-evals-to-resolve (a count of consecutive evaluations), is_paused,
  rule group interval, and a Prometheus-style-rule marker.
- Expression types (pkg/expr/query.go): math, reduce, resample, classic_conditions, threshold, sql. Source also holds
  hysteresis (threshold with a separate recovery bound) and an ML node. SQL expressions run SQL over query results as
  tables and are being actively developed.
- Shapes on the wire: provisioning list, file export, ruler (Prometheus-compatible with a grafana_alert wrapper), and
  the new /apis resource kinds (AlertRule, RecordingRule, RuleSequence promoted to v1beta1 on 2026-10-22).

### Alert corpus counts (160 Grafana-managed rules)

| Construct | Rules | Converter | New Relic form |
|---|---|---|---|
| query > reduce > threshold | 100 | converts when the PromQL is simple | yes (NRQL static condition) |
| query only, comparison inside PromQL, no threshold node | 36 | refused (not the supported pipeline) | yes for simple forms, same as rule-file rules which already convert |
| query > threshold (no reduce) | 12 | converts if query simple | yes |
| two queries (no expression) | 6 | refused | partly: NRQL can combine two metrics only with shared facets |
| classic_conditions | 3 (all single condition) | converts | yes |
| math | 3 (+1 reduce>math>reduce) | refused | partly: arithmetic inside one NRQL SELECT only |
| SQL expression | 0 | refused | no direct form |
| keep_firing_for | 0 (field absent) | ignored by design | none: NR has no equivalent, closest is a longer recovery period (unverified) |
| noData: OK / NoData / Alerting / KeepLast | 110 / 29 / 20 / 1 | Alerting becomes loss of signal; others noted | partial (loss of signal; no KeepLast) |
| execErrState Error / KeepLast / Alerting / OK | 72 / 54 / 24 / 10 | noted | none (NR has no query-error state) |
| for > 0 | 136 | converts to threshold duration | yes |
| notification_settings on the rule | 51 | not converted (contact points not migrated) | yes via workflow filters, unmapped today |
| templated annotations ({{ }} in text) | 66 | description/summary kept in IR, not written | partial: NR has its own template variables; Go-template syntax differs |
| reduce mode dropNN | 27 | noted | n/a |
| reduce mode replaceNN | 2 | refused | no |
| threshold within_range / outside_range | 3 | refused | no single NRQL threshold; two conditions would be needed |
| data source types other than Prometheus/Loki | stackdriver 10, GCP logging 1, influx 5, ClickHouse and others by uid | refused | NR has no native Influx or ClickHouse; GCP and CloudWatch need cloud integrations (separate sources already exist) |
| recording rules | 0 in sample | refused | no (NR has no recording rules; closest is a NRQL-derived metric or dashboard query) |

PromQL inside rules (160 + 40 rule-file): set operators (and/or/unless) 27 + 13, rate 26 + 11, `*_over_time` 20 + 1,
logql parser/unwrap 12, histogram_quantile 7, subquery 5 + 1, math functions 4, offset 3, group_left 2, absent 1 + 2,
predict_linear/deriv 2.

Converter's top alert blockers (manual, 169 rules): binary operator or comparison 30, rate/increase/range function
26, query not a simple selector 14, per-series equality test 11, comparison inside query while rule also has a
threshold node 10, Google Cloud Monitoring 9, histogram_quantile 7, counter rate without sum 7, LogQL parser 6,
vector matching 5, sum then reduce last 5, without() 4.

## Dashboards: what Grafana can express (from source)

- Two schemas. Classic JSON (schemaVersion up to 42 in the corpus; the corpus has 14 files in the pre-8 `rows` layout)
  and the v2 resource (dashboard.grafana.app v2/v2beta1; elements map plus layouts). v2 layouts: grid, auto grid,
  rows, tabs, with conditional rendering (by variable, by data presence, by time-range size). v2 variable kinds:
  query, text, constant, datasource, interval, custom, group-by, adhoc, switch. Annotations can sit on rows and tabs
  (added 2026-11-04). Library panels are element references. v2 adds field-config `varMappings` (2026-11-11).
- Corpus has zero v2 dashboards (all classic). Community dashboards on grafana.com are classic.
- Panel types in the core list today: alertlist, annolist, barchart, bargauge, candlestick, canvas, dashlist, debug,
  flamegraph, gauge, geomap, heatmap, histogram, logs, logstable, news, nodeGraph, piechart, stat, state-timeline,
  status-history, table, text, timeseries, traces, trend, xychart. Legacy graph, singlestat and table-old are
  auto-migrated by Grafana but still present in old JSON.
- Transformations in source: calculateField, concat, convertFieldType, convertFrameType, ensureColumns, filter by
  name/refId/value, formatString, formatTime, groupBy, groupToNestedTable, groupingToMatrix, histogram, joinByField,
  labelsToFields, limit, merge, order, organize, reduce, rename, renameByRegex, seriesToRows, sortBy, transpose.

### Dashboard corpus counts

Panels (data panels about 3,500): timeseries 1236, graph 1100, stat 478, singlestat 316, table 115, gauge 71,
heatmap 45, table-old 38, piechart 38, bargauge 36, logs 12, alertlist 9, nodeGraph 3, barchart 3, geomap 1.
Datasource on panels: prometheus (directly or by variable) the large majority; cloudwatch 166+20, loki 42, graphite 6,
others small (some panels also 695 inherit with no datasource field).
Variables (429 query, 112 datasource, 35 interval, 28 custom, 8 textbox, 8 constant, 6 adhoc, 0 switch/group-by).
Dashboards (of 183): with query variables 155, label_values 120, includeAll 101, multi 69, hidden 56, regex 29,
query_result 12, transformations 44, overrides 64, value mappings 36, links 40, custom annotations 15, repeated panels
27 (70 panels), per-panel time override 7 (15 panels), library panels 0.
Transformations by use: organize 97, renameByRegex 46, calculateField 23, merge 22, filterFieldsByName 21, sortBy 13,
seriesToColumns 12, groupBy 9, joinByField 8, filterByValue 7.
Overrides: 522 panels; properties color 884, unit 227, width 162, displayName 127, fillOpacity 108, min/max ~95,
lineStyle 87; matchers byRegexp 864, byName 616, byFrameRefID 65, byValue 46.
Value mappings: 129 panels (special 84, value 24, range 9). Thresholds on 1164 panels.
Prom/Loki targets 5361: use a variable 4754, legendFormat 4398, aggregation 3408, rate 1264, arithmetic 1245,
interval macros 1198, table/instant 907, irate 519, vector matching 404 (group_left 374), histogram_quantile 258,
without 257, or/unless 220, over_time 169, topk 138, subquery 93, math functions 71, LogQL parser 68, label_replace 64,
predict_linear/deriv 25, offset 25. 1047 panels have more than one target; 41 use a server-side expression target.

### Gap ranking, dashboards (converter output; 2,281 data widgets not converted)

Sole-blocker count is the ceiling. Ranked:

| # | Gap | Widgets blocked (sole) | New Relic form |
|---|---|---|---|
| 1 | sum/count across series of a gauge: needs knowing which labels identify a series | 321 | yes, once series identity is known (prom-hints flow exists); NRQL `sum` over a window double counts otherwise |
| 2 | CloudWatch data source panels | 253 | yes, but via the AWS/CloudWatch source, not Prometheus naming; separate effort |
| 3 | comparison, set operator or operator inside aggregation | 202 | partial: `filter()` and arithmetic work; `and/or/unless` set logic has no NRQL form; a plain `> N` comparison becomes a threshold line only |
| 4 | panel uses a variable that stays manual | 198 | partial: NR variables are substituted whole, no partial-text or regex-mixed matchers; chained and `All` handling varies |
| 5 | vector matching (on, ignoring, group_left) | 186 | no. NRQL cannot join two metrics on labels. NRQL JOIN exists only for event types and is limited (unverified for Metric) |
| 6 | counter-named metric read without rate (names ending _total/_count etc. that are really gauges, e.g. restic_locks_total, pg_stat_activity_count) | 176 | yes: `latest()` or `average()`. Sampled 6 of 6 were gauge-like. Cheapest lever |
| 7 | transformations | 157 | mostly no. organize/rename/renameByRegex on a table map to `AS` aliases and column choice; calculateField to NRQL arithmetic; merge, join, groupBy, seriesToColumns have no widget-level equivalent |
| 8 | functions with no counterpart (time(), delta()) | 63 | partial |
| 9 | options that change what is shown (mappings, overrides, unit conversion) | 54 | no for value mappings and per-series overrides (NR has per-series colour rules only on some charts, unverified) |
| 10 | subquery | 47 | no |
| 11 | heatmap panel | 45 | probably (NR heatmap chart over bucket metrics); unverified |
| 12 | `*_over_time` | 45 | partial: NRQL `max/min/average` over a `SINCE` window; moving window needs `SLIDE BY` |
| 13 | gauge 30, bargauge 17, logs 12, alertlist 9 | 68 | gauge, bargauge: billboard or bullet via threshold (approximate); logs: table (already for some); alertlist: none |
| 14 | offset | 25 | yes: `COMPARE WITH` (shifted series shown as a second line) |

Panel-level behaviours with no NR equivalent: repeat by row or column (only collapsed to FACET), conditional rendering
(v2), per-panel relative time or time shift (NR has `SINCE ... UNTIL` per widget, so partial), per-panel
`maxDataPoints`/`interval` (the NRQL TIMESERIES bucket is the only analog), panel links and data links with variables
(NR has no per-series drilldown links, only widget-level), annotations, `$__interval` macro maths, shared tooltip,
stacking per override, value-mapping text/colours.

## What is going on (commits since 2026-05-01)

- About 390 commits touched alerting and about 780 touched dashboards or panels in the period.
- Dashboard schema: v2 (dashboard.grafana.app v2 and v2beta1) is the active direction: tabs and auto-grid layouts,
  conditional rendering, switch and group-by variables, annotations on rows and tabs, dashboard preferences kind, a
  schema `varMappings` addition, an AI-assisted dashboard composer with Select, Text and Remove tools, a text panel v2
  with templates and iframe sandbox. Classic JSON stays and is still returned when a provisioned classic dashboard is
  saved. Migration meaning: the converter should keep reading classic JSON first; a v2 reader is only worth building
  once exports in v2 show up (none in the corpus). New layouts (tabs, auto grid) flatten into NR pages or markdown
  headings.
- Alerting: AlertRule, RecordingRule, RuleSequence promoted to v1beta1 under /apis; a rule import wizard moved to
  Settings > Import for Mimir and Prometheus rules, with imported rules evaluated in sequence; recording-rule remote
  write now batches and supports Amazon Managed Prometheus; the forked and remote Alertmanager modes were removed;
  missing-series resolution counts raised past a 16-bit limit; legacy hysteresis dimension handling removed; SQL
  expressions got many fixes and a docs example for dynamic thresholds. Meaning: the /apis shape is stable enough to
  read (the converter already reads it); SQL expressions and recording rules are growing, and NR has no form for
  either, so the manual share will not shrink from the Grafana side.
- Deprecations and removals: Azure Monitor removed from core plugins (2026-11-09); several feature toggles removed
  (group-by variable, panel time settings, remote Alertmanager). Angular-era panels remain only as auto-migrations.
- Tooling: dashboard export and sharing code paths were reorganised (legacy export viewer removed), snapshots accept
  write-only blobs, folders moved to the app platform API.

## Levers, with measured ceilings (not built)

1. Counter-named metric used without rate: read as a gauge when not wrapped in rate, using `latest()`/`average()`.
   Ceiling 176 widgets (about 5 points of the 3,563 data widgets). Needs a human check that the name list is a real
   gauge: sample was 6 of 6.
2. Query-only Grafana rules (condition = the query, comparison inside PromQL): route through the existing rule-file
   handler. Ceiling about 15-20 rules in the sample (36 rules, about half are a simple selector or aggregate against
   a number), but 34 of 36 come from two repos, so real-world gain is uncertain.
3. Gauge sum/count series identity (321 widgets): already a hints flow; the lever is feeding series keys without a live
   Prometheus (for example from the dashboard's own label_values variables). Ceiling 321 sole-blocker widgets, the
   largest single bucket, but unverified how many have a recoverable key.

## Could not verify

- All New Relic capabilities named above (heatmap chart, COMPARE WITH, SLIDE BY, JOIN on Metric, loss-of-signal
  details, per-widget time) are from memory of docs.newrelic.com NRQL and dashboard pages and were not re-fetched.
- Corpus counts of panels use my own parser; the converter counts 536 dashboards from the 183 files (nested lists), so
  per-dashboard percentages are not comparable one to one.
- Ceiling for the CloudWatch, variable and set-operator buckets was not sampled.
