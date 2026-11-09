# Advanced Grafana dashboard features and their New Relic equivalents

Research for the Grafana dashboard converter of Conquest Importer. Public documentation and public source only. No Grafana or New Relic instance was called, no `.env` was read, no cloud command was run. The only network reads were `curl` GETs of public documentation pages, public source files and the public grafana.com dashboard catalogue (section 10). Written 2026-11-09 against grafana/grafana `main`, Grafana docs "latest", New Relic docs-website `develop` and New Relic Terraform provider 3.101.12 (the version in `scripts/live-test/gcp-link/.terraform.lock.hcl`).

It extends `docs/research/grafana-dashboards.md` (the base note: JSON shapes, read APIs, panel table, coverage estimate). It does not repeat that note; where a statement there is corrected or sharpened, section 1 says so.

## 0. How to read this file

Evidence tags.

- DOCS: read on a vendor documentation page. The source id (G or N) points to the index below.
- CODE: read in a source file (Grafana source, Terraform provider source, plugin repositories). The id (S, T, P) gives the path.
- REPO: read in this repository.
- MEASURED: counted by me over the corpus of section 10 (180 public grafana.com dashboards). The corpus is biased towards popular community exports; section 10 states the limits.
- UNVERIFIED: from memory or inference. Collected in section 12. Do not build on these without a check.

Verdicts.

- EXACT: the New Relic construct produces the same numbers and the same visible result, given the data prerequisite.
- APPROXIMATE: same intent, different numbers or visuals, or exact only under a condition the converter can test.
- DIFFERENT: New Relic solves the same user need with another design (a feature that is not a 1:1 setting).
- NONE: no New Relic construct; the feature is dropped or the panel stays manual.

Effort: S under a day, M a few days, L a week or more, for one engineer inside the existing planner and IR.

Source index.

| id | source |
|---|---|
| G1 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/transform-data/ |
| G2 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-overrides/ |
| G3 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-value-mappings/ |
| G4 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/variable-syntax/ |
| G5 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/add-template-variables/ |
| G6 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/built-in-variables/ |
| G7 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/ (query options) |
| G8 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/query-transform-data/expression-queries/ |
| G9 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/modify-dashboard-settings/ |
| G10 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/create-dashboard/ (rows, tabs, repeat, show/hide rules) |
| G11 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/annotate-visualizations/ |
| G12 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-data-links/ |
| G13 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/configure-thresholds/ |
| G14 | https://grafana.com/docs/grafana/latest/datasources/ |
| G15 | https://grafana.com/docs/grafana/latest/as-code/ |
| G16 | https://grafana.com/docs/grafana/latest/administration/provisioning/ |
| G17 | https://grafana.com/docs/grafana/latest/visualizations/panels-visualizations/visualizations/ |
| G18 | https://grafana.com/docs/grafana/latest/as-code/observability-as-code/foundation-sdk/ |
| G19 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/manage-dashboard-links/ |
| G20 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/share-dashboards-panels/ and `.../shared-dashboards/` |
| S1 | grafana/grafana `packages/grafana-data/src/transformations/transformers/` (`ids.ts`, `calculateField.ts`, `reduce.ts`, `groupBy.ts`, `filterByValue.ts`, `joinShared.ts`) |
| S2 | grafana/grafana `public/app/features/transformers/standardTransformers.tsx` (the registered transformations) |
| S3 | grafana/grafana `packages/grafana-data/src/transformations/fieldReducer.ts`, `matchers/ids.ts`, `matchers/valueMatchers/ids.ts`, `utils/binaryOperators.ts` |
| S4 | grafana/grafana `packages/grafana-data/src/valueFormats/categories.ts` (unit ids) |
| S5 | grafana/grafana `packages/grafana-data/src/types/` (`valueMapping.ts`, `thresholds.ts`, `fieldColor.ts`, `dataLink.ts`, `templateVars.ts`) |
| S6 | grafana/grafana `packages/grafana-schema/src/common/common.gen.ts` (field config and options enums) |
| S7 | github.com directory listings (JSON) of grafana/grafana `public/app/plugins/panel`, `public/app/plugins/datasource`, `public/app/features/variables` |
| S8 | grafana-operator `api/v1beta1/` (`groupversion_info.go`, `content.go`, `grafanadashboard_types.go`); terraform-provider-grafana `docs/resources/dashboard.md`; prometheus-community/helm-charts `kube-prometheus-stack` (`values.yaml`, `templates/grafana/dashboards-1.14/apiserver.yaml`); grafana/grafonnet and grafana/grafana-foundation-sdk `README.md` |
| P1 | `src/plugin.json` of the plugin repositories (id check): grafana/grafana-polystat-panel, grafana/clock-panel, Vonage/Grafana_Status_panel, grafana/piechart-panel, marcusolsson/grafana-treemap-panel, VolkovLabs/business-charts, VolkovLabs/business-text, grafana/grafana-infinity-datasource |
| N1 | New Relic `nrql/nrql-syntax-clauses-functions` |
| N2 | New Relic `apis/nerdgraph/examples/nerdgraph-dashboards` (section "Limits") |
| N3 | New Relic `query-your-data/explore-query-data/use-charts/use-your-charts` |
| N4 | New Relic `.../use-charts/chart-types` |
| N5 | New Relic `.../dashboards/dashboard-template-variables` |
| N6 | New Relic `nrql/using-nrql/subqueries-in-nrql` |
| N7 | New Relic `nrql/using-nrql/subquery-joins` |
| N8 | New Relic `nrql/using-nrql/lookups` |
| N9 | New Relic `nrql/using-nrql/nested-aggregation-make-ordered-computations-single-query` |
| N10 | New Relic `nrql/nrql-macros/nrql-macros-overview` |
| N11 | New Relic `.../dashboards/manage-your-dashboard` |
| N12 | New Relic `.../dashboards/share-dashboards-publicly` |
| N13 | New Relic `infrastructure/prometheus-integrations/view-query-data/supported-promql-features`, `.../translate-promql-queries-nrql`, and `query-your-data/grafana-integrations/get-started/grafana-support-prometheus-promql` |
| N14 | New Relic `change-tracking/overview` |
| N15 | New Relic `.../dashboards/filter-new-relic-one-dashboards-facets` |
| N16 | New Relic `.../use-charts/chart-refresh-rates` |
| N17 | New Relic `.../dashboards/dashboards-charts-import-export-data` |
| N18 | New Relic `.../dashboards/nested-variables` |
| N19 | New Relic `.../dashboards/troubleshooting-chart-errors` |
| N20 | New Relic `new-relic-solutions/build-nr-ui/sdk-component/charts/Charts.mdx` and `.../tables/MetricTableRowCell.mdx` (unit names of the chart library) |
| N21 | New Relic `infrastructure/microsoft-azure-integrations/get-started/azure-integration-metrics`, `infrastructure/amazon-integrations/manage-aws-data/work-with-aws-metrics-data`, `infrastructure/google-cloud-platform-integrations/...` |
| N22 | New Relic `nrql/nrql-references/nrql-math-using-select`, `nrql/using-nrql/rate-limits-nrql-queries`, `nrql/using-nrql/create-smoother-charts-sliding-windows` |
| N23 | New Relic `new-relic-solutions/build-nr-ui/newrelic-cli` and `nr1-cli/*` pages |
| T1 | Terraform provider newrelic 3.101.12, offline schema: `terraform providers schema -json` in `scripts/live-test/gcp-link`, resource `newrelic_one_dashboard` (read only) |
| T2 | terraform-provider-newrelic `website/docs/r/one_dashboard.html.markdown` |
| T3 | terraform-provider-newrelic `newrelic/resource_newrelic_one_dashboard.go`, `structures_newrelic_one_dashboard.go`, `resource_newrelic_one_dashboard_test.go` |

New Relic docs are read as raw files of github.com/newrelic/docs-website (branch `develop`), path `src/content/docs/<path>.mdx`, published at `https://docs.newrelic.com/docs/<path>/`. Raw files for G, S, P, N and T were fetched on 2026-11-09 with `curl -sSL`.

## 1. Answers first

1. The target is narrower than the source in several places that matter. New Relic dashboards have no dashboard-level time zone, default time range, refresh, annotation layer, link bar, repeat or collapsible row in the provider schema or the NerdGraph limits page (T1, N2). The converter cannot carry these as settings; it carries them as text, as separate widgets or not at all.
2. The most useful mechanism New Relic has for transformations is NRQL itself: `FACET`, `FACET CASES`, `filter()`, `percentage()`, nested aggregation (`SELECT f(x) FROM (SELECT ... TIMESERIES)`), subqueries in `WHERE`, `JOIN` of a subquery, `capture()`/`aparse()`/`jparse()`, `SLIDE BY`, `COMPARE WITH` (N1, N6, N7, N9). Of the 34 transformations registered in the Grafana UI (S2), 5 are EXACT, 18 are APPROXIMATE (each under a stated condition), 2 are DIFFERENT designs and 9 have no counterpart. Table in section 3.
3. In the 180-dashboard corpus, 4.7% of panels carry transformations (169 of 3,633), 24% of dashboards have at least one, and the first six ids (`organize` 97, `renameByRegex` 46, `calculateField` 23, `merge` 22, `filterFieldsByName` 21, `sortBy` 13) are 222 of 288 uses (77%) (MEASURED). The base note's rule "any transformation fails the widget" is too strict for `organize`, `renameByRegex` with static names, `filterFieldsByName`, `sortBy`, `limit` and `merge` of same-shape series, which are all expressible.
4. Field overrides are the larger problem: 14.4% of panels (522) and 36% of dashboards have `fieldConfig.overrides` (MEASURED). The matcher is `byRegexp` in 864 of 1,591 override rules (54%) and `byName` in 616 (39%), `byFrameRefID` 65, `byValue` 46; no `byType`. The properties are `color` (884 of about 2,300 properties), `unit` 227, `custom.width` 162, `displayName` 127, `custom.fillOpacity` 108. New Relic's per-series overrides cover colour, unit, null handling and the right axis, keyed by an exact series name (T1). A `byRegexp` override can be expanded to exact names only if the series names are known at conversion time, which for Prometheus labels they are not.
5. Units are the next largest silent-wrong risk. 54% of panels (1,980 of 3,633) set a unit. `percent` (0 to 100) must become `percentunit` (0 to 1) because New Relic's `PERCENTAGE` unit formats `1` as 100% (N20), so the NRQL needs `/100`. Grafana `bytes` is IEC (1024) and `decbytes` is SI (S4); New Relic charts use SI (N3 callout). The strings accepted by `units.unit` are not enumerated in any document I read (T2 says only "choose a unit"; T3 tests use `"BYTES"`, T2 example uses `"ms"`). Section 4.4 has the 60-row mapping.
6. Variables are the highest-frequency advanced feature. 95% of dashboards have variables (171 of 180); 626 variables: query 429, datasource 112, interval 35, custom 28, textbox 8, constant 8, adhoc 6 (MEASURED). 77% of query variables use `label_values()`, 52% reference another variable (chained), 24% of all variables are multi-value, 35% include All, 19% define a custom `allValue` (94 of 121 are `.*`). New Relic can express query, custom, textbox, constant (as substitution), the multi/All pair and a `FACET {{x}}` group-by; chained variables are a plan-limited feature (N18); interval and datasource variables have no counterpart (section 4.11).
7. `$__rate_interval` appears 1,130 times, `$__interval` 220, `$__range` 102 in the corpus (MEASURED). All three become `TIMESERIES AUTO` or `rate(..., 1 second)` and need no variable; the dashboard must not ship the macro text. `${var:queryparam}` (32), `${var:raw}` (34) and `${var:text}` (15) are the only format options seen; `queryparam` is a URL format (G4).
8. Repeating panels are rare (43 panels, 9 dashboards) but repeating rows are on 11% of dashboards (20 of 180). New Relic has no repeat. The honest conversions are `FACET` on the variable's label (panel repeat, APPROXIMATE) or expansion to the current values (stale); a repeated row becomes one page or one block of widgets. Section 4.10.
9. Limits in the base note and in the IR are partly wrong or unsourced. NerdGraph limits (N2): 25 pages per dashboard, 150 widgets per page, 20 queries per widget, widget height 1 to 32, width 1 to 12, column 1 to 12, 30 accounts per cross-account query, widget title 255 characters. The IR clamps height to 12 and allows 50 raw series per widget and 300 widgets per dashboard (REPO `dashboard_ir.py`, `docs/ir-spec.md`). The 50 raw series exceed the 20-query limit and must be capped at 20; the 300-widget cap is lower than 150 x 25 but a single page cannot exceed 150, so the emitter needs pagination at 150. The height clamp of 12 is stricter than the 32 the limits page states; the provider validates height only as at least 1 (T3, `resource_newrelic_one_dashboard.go`). Verify in the sandbox before relaxing it.
10. The stacked bar and log table widget ids are now confirmed in provider source: `viz.stacked-bar` and `logger.log-table-widget` (T3, `structures_newrelic_one_dashboard.go`). The gauge and treemap exist in the New Relic UI (N4) but have no block in the provider schema (T1) and no id in the NerdGraph dashboard pages (N2 and the widget examples page) or anywhere in the 7,384 docs files I scanned for `viz.gauge` and `viz.treemap`; they can only be created through `newrelic_one_dashboard_json` or NerdGraph with an id I could not find (UNVERIFIED).
11. New Relic can also act as a Prometheus data source for Grafana (N13: PromQL-style queries are translated to NRQL, "over 99.5% of queries across the 7.8 million top Grafana dashboard downloads" is a New Relic claim, not verified here). That is not a migration of the dashboard, but it is a fallback for a customer who keeps Grafana for a while, and the translator shows what New Relic itself considers supported PromQL (aggregations, `*_over_time`, `rate`-like functions, arithmetic, logical operators, date functions, `histogram_quantile`, `predict_linear`).
12. Dashboards-as-code is detectable cheaply. Seven forms were verified (section 7): Grafana provisioning YAML with `providers[].options.path`, the legacy and `/apis` JSON, Terraform `grafana_dashboard.config_json`, Grafana Operator `GrafanaDashboard` (group `grafana.integreatly.org/v1beta1`) with `spec.json | gzipJson | url | jsonnet | jsonnetLib | configMapRef | grafanaCom`, ConfigMaps labelled `grafana_dashboard: "1"` for the sidecar, Helm templates that wrap the JSON in Go template markers, and Jsonnet/Grafonnet/Foundation SDK sources that must be rendered first. The importer should read the rendered JSON, not the generators.

## 2. The New Relic target in one page

What the provider can set (T1, offline schema of `newrelic_one_dashboard`, version 3.101.12).

- Dashboard: `name`, `description`, `permissions` (`private`, `public_read_only`, `public_read_write`, T3 line 53), `account_id`, `page[]`. Nothing for time range, time zone, refresh, links, annotations.
- Page: `name`, `description`, widgets.
- Widget common attributes: `title`, `row`, `column`, `width`, `height`, `refresh_rate` (`auto`, `0`, `5000`, `30000`, `60000`, `300000`, `1800000`, `3600000`, `10800000`, `43200000`, `86400000` ms, T2), `ignore_time_range`, `legend_enabled`, `facet_show_other_series`, `y_axis_left_min`, `y_axis_left_max`, `nrql_query[]` (`account_id` single or a JSON array, `query`), `colors{color, series_overrides[]}`, `units{unit, series_overrides[]}`, `null_values{null_value, series_overrides[]}`, `data_format[]{name, type, format, precision}`, `initial_sorting{name, direction}`, `chart_styles{line_interpolation, gradient{enabled}}`.
- Per widget type: line has `threshold[]{name, from, to, severity}`, `tooltip{mode}`, `y_axis_left_zero`, `is_label_visible`, `y_axis_right{y_axis_right_min, y_axis_right_max, y_axis_right_zero, y_axis_right_series}`; area has `tooltip`; stacked bar has `tooltip`; table has `threshold[]{column_name, from, to, severity}`; billboard has `critical`, `warning`, `billboard_settings{grid_options{columns, label, value}, link{url, title, new_tab}, visual{alignment, display}}`; bar, heatmap, pie, table have `filter_current_dashboard` and `linked_entity_guids` (facet linking); bullet has `limit`; markdown has `text`.
- Variables: `name`, `title`, `type` (`enum`, `nrql`, `string`), `is_multi_selection`, `default_values`, `replacement_strategy` (`default`, `identifier`, `number`, `string`), `item[]{title, value}`, `nrql_query{account_ids, query}`, `options{excluded, ignore_time_range, show_apply_action}` (T1, T3 lines 170 and 181).
- Widget kinds and ids: `viz.area`, `viz.bar`, `viz.billboard`, `viz.bullet`, `viz.funnel`, `viz.heatmap`, `viz.histogram`, `viz.json`, `viz.line`, `viz.markdown`, `viz.pie`, `viz.stacked-bar`, `viz.table`, `logger.log-table-widget` (T3). That is 14. Not in the provider: gauge, treemap, simplified bar, event feed, service map, inventory (N4, N2 mention some).
- Severity values for thresholds: `success`, `warning`, `unavailable`, `severe`, `critical` (T3 `resource_newrelic_one_dashboard.go`).
- `data_format.type`: `decimal`, `date`, `duration`, `recent-relative`, `custom`, `humanized`; custom date formats `%b %d, %Y`, `%d/%m/%Y`, `%x`, `%I:%M%p`, `%H:%Mh`, `%H:%Mh UTC (%Z)`, `%Y-%m-%dT%X.%L%Z`, `%b %d, %Y, %X`, `%X`; applies to billboards and tables only (T2).

Limits (N2): pages 25, widgets per page 150, queries per widget 20, accounts per cross-account query 30, widget title 255, dashboard name 255, description 1,024, column 1 to 12, width 1 to 12, height 1 to 32, entities linked to a widget 1. NRQL: default `LIMIT` 10, maximum 5,000 (`LIMIT MAX`); 3 subqueries per query; a joined subquery returns at most 5,000 rows, has no `TIMESERIES`, cardinality at most 1:100; `TIMESERIES` at most 366 buckets (N19); lookup tables up to 20,000 rows (N8); query duration 1 minute (Original Data) or 10 minutes (Data Plus); 3,000 queries per account per minute over the API, UI exempt; repetitive-query limit 120 per minute (N22). A NRQL query-length limit is not stated in the pages read (UNVERIFIED; the repository caps at 4,096).

What the UI has that the provider lacks (N4, N3, N11). Gauge (min, max, label display, threshold markers, thresholds Good, Warning, Approaching critical, Critical, Neutral); treemap (facet, up to two levels); simplified bar; billboard thresholds with five levels; "thresholds per series"; widget link and description (`link`, `description` in the dashboard JSON, 2,048-character http/https link); markdown with Mermaid; TV mode with page cycling at 20 s, 1 min, 5 min, 30 min, 1 h; time picker from last 5 minutes to last 6 months or custom; per-widget "Ignore time picker".

Time handling. The widget query owns its window when `ignore_time_range` is set; otherwise the dashboard time picker overrides `SINCE`/`UNTIL`. `COMPARE WITH` and `TIMESERIES AUTO|MAX|n` are query-level. `WITH TIMEZONE 'zone'` is a query clause (N1). Public live URLs take a `time_zone` parameter that cannot be set through NerdGraph (N12).


## 3. (A) Transformations

Grafana runs transformations in the browser on the frames the data source returned, in the order listed in the panel's `transformations[]`, each `{id, options}` plus optional `disabled`, `filter` and `topic` (the type `DataTransformerConfig` is in the schema package, not opened here, UNVERIFIED); the output of one is the input of the next (G1 "Order of transformations"). A New Relic widget has no post-processing stage: the NRQL result is the widget input. A transformation is therefore convertible only if the converter can push its effect into the query it already builds, and only for query shapes it understands.

Citations of the form CODE `name.ts` in the tables point to files under `packages/grafana-data/src/transformations/transformers/` or `public/app/features/transformers/` in grafana/grafana (S1, S2). Ids. The enum in `ids.ts` has 42 ids (S1): `append`, `reduce`, `order`, `organize`, `rename`, `calculateField`, `seriesToColumns` (deprecated alias of `joinByField`), `seriesToRows`, `merge`, `concatenate`, `labelsToFields`, `filterFields`, `filterFieldsByName`, `filterFrames`, `filterByRefId`, `renameByRegex`, `filterByValue`, `noop`, `ensureColumns`, `groupBy`, `sortBy`, `histogram`, `configFromData`, `rowsToFields`, `prepareTimeSeries`, `convertFieldType`, `convertFrameType`, `fieldLookup`, `heatmap`, `spatial`, `joinByField`, `joinByLabels`, `extractFields`, `groupingToMatrix`, `limit`, `partitionByValues`, `timeSeriesTable`, `transpose`, `formatTime`, `formatString`, `regression`, `smoothing`, `groupToNestedTable`. The UI registers 34 (S2); `append`, `order`, `rename`, `filterFields`, `filterFrames`, `noop`, `ensureColumns`, `convertFrameType` and `seriesToColumns` are internal, hidden or aliases. A dashboard file can still carry any id, and `disabled: true` entries must be skipped. Variables can appear inside the options of some transformations (G1 "Dashboard variables in transformations"). The `topic` option takes the `DataTopic` values `series`, `annotations` or `alertStates` (S6) and says which data the transformation applies to.

NRQL constructs that stand in for transformations (all DOCS N1 unless noted).

| construct | what it does | used for |
|---|---|---|
| `SELECT a AS 'x', b AS 'y'` | named output columns and series | `organize` rename and hide, `filterFieldsByName` |
| `SELECT a+b, a/b, a*100` with `abs() exp() ln() log2() log10() floor() ceil() round() sqrt() pow() mod()` | arithmetic over aggregates of one event type (N22) | `calculateField` binary and unary |
| `FACET attr[, attr...]` (up to 5 attributes), `FACET ... ORDER BY`, `LIMIT n` / `LIMIT MAX` (5,000) | group and cut | `groupBy`, `labelsToFields`, `partitionByValues`, `limit`, `sortBy` |
| `FACET CASES (WHERE ... AS 'label', ...)` | rule-based grouping | value mappings, bucketing, `partitionByValues` with conditions |
| `filter(agg, WHERE cond)` | several conditions in one query, as separate columns or series | `joinByField` outer on one event type, `rowsToFields` with known names |
| `percentage(agg, WHERE cond)`, `rate(agg, 1 minute)`, `derivative()`, `latest()`, `earliest()`, `uniques()`, `uniqueCount()`, `median()`, `percentile(x, n, ...)`, `stddev()`, `stdvar()`, `histogram()`, `cdfPercentage()`, `predictLinear()`, `apdex()` | aggregators | `reduce`, `groupBy` aggregations, `regression` (linear), `histogram` |
| nested aggregation `SELECT f(v) FROM (SELECT g(x) AS v FROM E TIMESERIES 1 minute)` (N9) | aggregate over the result of a query, `TIMESERIES` allowed in the inner query, `SINCE`/`UNTIL`/`COMPARE WITH` only outermost | `reduce` over a time series, post-aggregation `filterByValue` |
| subquery in `WHERE` (`WHERE x > (SELECT ...)`, `IN (SELECT uniques(...))`) | up to 3 per query, not in alert conditions, not in `FACET`, no `TIMESERIES` or `COMPARE WITH` in a subquery (N6) | `filterByValue` against another query |
| `JOIN (subquery) ON key [INNER|LEFT]` | one subquery, `LIMIT` 5,000, no `TIMESERIES` in the joined query, cardinality 1:100 (N1 `sel-join`, N7) | `joinByField` for tabular data |
| `FROM A, B` | one query over several event types | `merge` of same-attribute data |
| `capture(attr, r'...(?P<name>...)')`, `aparse(attr, 'pattern*')`, `jparse(attr, 'path')`, `substring()`, `lower()`, `upper()`, `concat()`, `string()`, `numeric()`, `boolean()` | string and JSON extraction, type conversion (N1 "Type conversion", `func-capture`, `func-aparse`, `func-jparse`) | `extractFields`, `renameByRegex` on facet values, `convertFieldType`, `formatString` |
| `TIMESERIES n|AUTO|MAX [SLIDE BY m|AUTO|MAX]` | bucket width (at most 366 buckets, N19) and sliding window | `calculateField` window mean, `smoothing`, `$__interval` |
| `COMPARE WITH 1 week ago` | the same query shifted in time, shown as second series (billboards: `COMPARE WITH` does not render in public links, images or PDFs, N4) | `timeShift` overlays, `timeCompare` option |
| `lookup(tableName)` | CSV lookup tables uploaded to the account, up to 20,000 rows (N8) | `fieldLookup`-style enrichment (different design) |
| NRQL macros (`$name`, organisation level, N10) | saved expressions | repeated filters; not a Grafana feature |

What does not exist in NRQL, as far as the pages read show: UNION of two arbitrary queries (only `FROM A, B`), HAVING (use a nested `FROM (...) WHERE`), pivot or transpose, cumulative sums, row index, lag/lead, a regex flavour identical to JavaScript. `cumulativeCount` and `cdfPercentage()` exist inside distribution functions only (N1). UNVERIFIED: none of these is available through an undocumented function.

### 3.1 The 34 registered transformations

Verdict is for the common case. "Cond." is the condition under which the verdict holds. Counts in the corpus are from the 180 dashboards (MEASURED; `seriesToColumns` is counted with `joinByField`).

| id | what Grafana does (options) | New Relic | verdict | prerequisite and recipe | corpus uses |
|---|---|---|---|---|---|
| `organize` | `excludeByName{}`, `indexByName{}`, `renameByName{}`, `includeByName{}` per field (G1) | `AS` for rename, omit from `SELECT` to hide, `SELECT` order for order. Column order in a widget follows the select list (UNVERIFIED for table widgets) | APPROXIMATE | rename and hide are exact; order is unproven. Recipe: apply `renameByName` to the alias of the matching `SELECT` term, drop excluded fields from the list. Fields that are labels of a Prometheus series are renamed through `AS` on the facet only if the label is a `FACET` attribute | 97 |
| `renameByRegex` | `regex`, `renamePattern` on field display names (CODE `renameByRegex.ts`) | `capture()` or `aparse()` inside the `FACET` expression produces the renamed facet value; otherwise `AS` on static names | APPROXIMATE | Grafana uses JavaScript regex, `capture()` uses a named-group pattern `r'(?P<n>...)'` (N5 shows the form); lookahead and backreferences fail. Recipe: rewrite `FACET capture(attr, r'...')`; if names are static, rename with `AS` | 46 |
| `filterFieldsByName` | `include`, `exclude` (each names and pattern), `byVariable` (CODE `filterByName.ts`) | the `SELECT` list; regex over known names | EXACT | names are exact; a pattern is exact only when the converter knows the candidate names (the select list) | 21 |
| `filterByRefId` | `include` or `exclude` (regex over query refIds) (CODE `filterByRefId.ts`) | omit the other queries from `nrql_query[]` (T1; at most 20 per widget, N2) | EXACT | the refIds are known at conversion time | 0 |
| `filterByValue` | `type` include or exclude, `match` any or all, `filters[{fieldName, config{id, options}}]` with matcher ids `regex isNull isNotNull greater greaterOrEqual lower lowerOrEqual equal notEqual substring notSubstring between inSet` (S1, S3) | `WHERE` on the event, or `SELECT * FROM (SELECT agg FACET x) WHERE value > n` for filtering aggregated rows (N9 intro lists "list only the ones with usage over 90%") | APPROXIMATE | Grafana filters result rows (including every time point of a series), NRQL `WHERE` filters events before aggregation. Equal only for instant, one-row-per-series tables via the nested form. Dropping time points from a time series: NONE | 7 |
| `reduce` | `mode` `seriesToRows` or `reduceFields`, `reducers[]` from the reducer list, `includeTimeField`, `labelsToFields` (S1) | an aggregator without `TIMESERIES` per series, or the nested form `SELECT agg(v) FROM (SELECT ... AS v TIMESERIES 1 minute)` | APPROXIMATE | reducer map: `sum`->`sum()`, `max`->`max()`, `min`->`min()`, `mean`->`average()`, `median`->`median()`, `count`->`count()`, `last`/`lastNotNull`->`latest()`, `first`/`firstNotNull`->`earliest()`, `stdDev`->`stddev()`, `variance`->`stdvar()`, `distinctCount`->`uniqueCount()`, `p1`..`p99`->`percentile(x, n)`, `range`->`max(v)-min(v)`. No NRQL form for `diff`, `diffperc`, `delta`, `step`, `changeCount`, `allIsZero`, `allIsNull`, `allValues`, `uniqueValues`, `logmin`, `countAll`. Reducing a rate series: use the nested form to keep the bucket semantics | 4 |
| `calculateField` | `mode` `reduceRow`, `binary`, `unary`, `cumulativeFunctions`, `windowFunctions`, `index`; `alias`, `replaceFields`; binary operators `+ - * /` only (S1, S3) | `binary`: arithmetic in `SELECT`; `unary`: `abs() exp() ln() floor() ceil()`; `reduceRow` sum or mean: arithmetic across selected aggregates; `windowFunctions` mean: `SLIDE BY`; `cumulativeFunctions`, `index`, window `stddev` and `variance`, centred windows: none | APPROXIMATE | the fields must be aggregates of the same event type and facet set (N22). `reduceRow` max or min across fields has no direct form (UNVERIFIED that `if()` can express it over aggregates). Window mean: Grafana window size is a count of points or a percentage, `SLIDE BY` is a duration, so a point count needs the bucket width | 23 |
| `groupBy` | `fields{name:{operation: groupby|aggregate, aggregations[]}}` (S1) | `FACET` + aggregators | APPROXIMATE | exact when the converter rewrites the original query (SQL rows or PromQL `sum by`), because NRQL groups events, not result rows. The aggregation set is the `reduce` map above | 9 |
| `sortBy` | `sort[{field, desc}]` (G1) | `initial_sorting{name, direction}` on tables (T1); `ORDER BY` for non-faceted `SELECT *` queries; facets sort by the first select term (N1 `sel-facet`) | EXACT | sorting by a column other than the first select term on a faceted chart is not possible: `ORDER BY` after `FACET` exists (`sel-facet-order`) but the text read does not state its limits (UNVERIFIED) | 13 |
| `limit` | `limitField` (row count) (CODE `limit.ts`) | `LIMIT n` (max 5,000) | EXACT | for facets this is the number of series | 5 |
| `merge` | union of frames with the same columns (G1) | several `nrql_query` blocks in one widget for charts; `FROM A, B` for one query | APPROXIMATE | exact for a line or area widget where the frames only need to be drawn together; for tables, the merged rows need one query over several event types | 22 |
| `joinByField` (and `seriesToColumns`) | `byField`, `mode` `outer`, `inner`, `outerTabular` (S1: `JoinMode`) | outer on time, one event type: several aggregates in one `SELECT` or `filter()` columns; tabular: `JOIN (subquery) ON key` (INNER or LEFT) | APPROXIMATE | `JOIN` has no FULL OUTER form; the joined query cannot have `TIMESERIES`; 1:100 cardinality; 5,000 rows. Time-series joins across event types: NONE | 20 |
| `seriesToRows` | stack all series into `Time, Metric, Value` rows (G1) | none for a chart; for a table, a faceted query without `TIMESERIES` | DIFFERENT | New Relic charts draw one series per facet already; the long table of time points has no widget. Convert only when the panel is a table of instant values | 3 |
| `concatenate` | side-by-side fields from all frames (G1) | a multi-aggregate `SELECT` when the frames share a facet | NONE | no row alignment guarantee otherwise; stay manual | 0 |
| `labelsToFields` | `mode` `columns` or `rows`, `valueLabel`, `keepLabels` (CODE `labelsToFields.ts`) | `FACET a, b` makes each label a table column | APPROXIMATE | the standard way to turn `sum by (a,b)(...)` into a table; column names come from the attribute names | 4 |
| `rowsToFields` | `nameField`, `valueField`, `mappings[]`: rows with a name and a value become fields, optionally config such as unit or thresholds (G1, CODE `rowsToFields/rowsToFields.ts`) | `filter(latest(v), WHERE name='x') AS 'x'` for a fixed list of names | NONE | dynamic config-by-row has no counterpart; the fixed-names case is a manual rewrite | 0 |
| `extractFields` | `source`, `format` (`json`, `kvp`, `auto`, regex), `jsonPaths`, `delimiter`, `regExp`, `keepTime`, `replace` (CODE `extractFields/types.ts`) | `jparse(attr, 'path')`, `aparse()`, `capture()` | APPROXIMATE | only when the field is a string attribute in an NRDB event (Logs). For metric labels there is nothing to extract | 5 |
| `convertFieldType` | `conversions[{targetField, destinationType: number|string|time|boolean|enum, dateFormat, joinWith, timezone, enumConfig}]` (CODE `convertFieldType.ts`) | `numeric()`, `string()`, `boolean()`; time via `data_format` type `date` or `custom` (T2) | APPROXIMATE | NRQL has no coercion (N1 "Type conversion"); `enum` has none | 2 |
| `formatTime` | `timeField`, `outputFormat`, `timezone` (CODE `formatTime.ts`) | `data_format{name, type=custom, format}` with one of nine strftime patterns (T2) | APPROXIMATE | Grafana formats are moment-style (`YYYY-MM-DD HH:mm`), New Relic accepts only the nine listed patterns | 1 |
| `formatString` | `stringField`, `substringStart`, `substringEnd`, `outputFormat` (case and trim styles) (CODE `formatString.ts`) | `upper()`, `lower()`, `substring()`, `concat()` | APPROXIMATE | trim, case styles other than upper/lower: none | 0 |
| `partitionByValues` | split one frame by the values of a field (`fields[]`, `naming{asLabels, append, withNames, separator1, separator2}`, `keepFields`) (CODE `partitionByValues.ts`) | `FACET field` | APPROXIMATE | series names differ from Grafana's `naming` options | 1 |
| `prepareTimeSeries` | `format` `multi`, `wide`, `many` (G1) | none needed; every NRQL time series is already one series per facet | EXACT | no-op | 1 |
| `histogram` | `bucketSize`, `bucketCount`, `bucketOffset`, `combine` (CODE `histogram.ts`) | `histogram(attr, width, buckets, start)` with 1 to 500 buckets, or `histogram(metric_bucket, ...)` for Prometheus buckets and distribution metrics (N1 `func-histogram`) | APPROXIMATE | needs the raw attribute, not an already aggregated series. Output goes to `viz.histogram` | 0 |
| `heatmap` | `calculateHeatmap`: `xBuckets`, `yBuckets`, `calculation` (G1) | `viz.heatmap` with `SELECT histogram(attr) FROM E FACET key` (N1 `histogram-facet-heatmap`) | DIFFERENT | the New Relic example builds a heatmap from `histogram()` with `FACET` (value buckets by facet); a time-by-value-bucket heatmap is not shown anywhere in the pages read (inference, UNVERIFIED), so the two are probably not the same picture. Prometheus `format: heatmap` queries need the `histogram(x_bucket, ...)` form | 0 |
| `regression` | fits a `linear` or `polynomial` model to the data and emits predicted values; options `modelType`, `degree`, `predictionCount`, `xFieldName`, `yFieldName` (G1, CODE `regression/regression.ts`) | `predictLinear()` for a linear forecast; the UI "predictions" feature (N1, `nrql-predictions`) | APPROXIMATE | polynomial and fit overlays: none | 0 |
| `smoothing` | adaptive smoothing with the ASAP algorithm, `resolution` 1 to 1000 (G1) | `TIMESERIES ... SLIDE BY` moving mean (N22) | APPROXIMATE | different algorithm; a visual approximation only | 0 |
| `joinByLabels` | `join` label keys, `value`, `refId` (CODE `joinByLabels.ts`) | several aggregates with the same `FACET` | APPROXIMATE | same event type only | 0 |
| `configFromData` | options `configRefId`, `mappings[{fieldName, handlerKey, reducerId}]`, `applyTo`: set thresholds, min, max, unit, color from another query (CODE `configFromQuery/configFromQuery.ts`; the `mappings` item keys are from memory) | none | NONE | dynamic thresholds from a query have no widget counterpart; a static snapshot of the threshold value would be a lie | 4 |
| `groupingToMatrix` | `columnField`, `rowField`, `valueField`, `emptyValue` pivot (CODE `groupingToMatrix.ts`) | none | NONE | no pivot in NRQL | 0 |
| `groupToNestedTable` | grouped rows with a nested table; `showSubframeHeaders`, `expandAllRows`, `rules` (CODE `groupToNestedTable.ts`) | none | NONE | no nested tables | 0 |
| `timeSeriesTable` | one table row per series with a sparkline cell; per refId options `stat`, `timeField`, `inlineStat` (CODE `timeSeriesTable/timeSeriesTableTransformer.ts`; the schema 39 move from `refIdToStat` is in the base note) | none known. A billboard with `FACET` and `TIMESERIES` shows sparklines (the provider gives billboards `line_interpolation`, T1) but not a table | NONE | UNVERIFIED that the billboard grid is an acceptable stand-in | 0 |
| `spatial` | geo fields from lat/lon, geohash, lookup (G1) | none | NONE | no map widget in the provider (T1) | 0 |
| `transpose` | rows to columns; `firstFieldName`, `restFieldsName`, `emptyValue` (CODE `transpose.ts`) | none | NONE | no pivot | 0 |
| `fieldLookup` | gazetteer lookup (countries to coordinates) (G1) | `lookup()` of an uploaded CSV table is a different design (N8) | NONE | the table must be uploaded by hand | 0 |

Legacy `table-old` panels carry a `transform` field (55 panels in the corpus, MEASURED; values not inspected) that the frontend migrates to transformations (base note section 2.4); treat it as the same set.

Counts. Of the 34 registered transformations: 5 EXACT (`filterFieldsByName`, `filterByRefId`, `sortBy`, `limit`, `prepareTimeSeries`), 18 APPROXIMATE, 2 DIFFERENT (`seriesToRows`, `heatmap`), 9 NONE.

Corpus weights (MEASURED): 288 transformation uses; `organize` 97, `renameByRegex` 46, `calculateField` 23, `merge` 22, `filterFieldsByName` 21, `sortBy` 13, `seriesToColumns` 12, `groupBy` 9, `joinByField` 8, `filterByValue` 7. A converter that handles `organize`, `renameByRegex` (static or via `capture()`), `filterFieldsByName`, `sortBy`, `limit`, `merge` and binary `calculateField` covers at most 222 of 288 uses (77%; the figure counts every `calculateField` mode); but a panel converts only if every transformation in its chain does, and chains are common (`organize` after `merge`).

Recipe for the planner. Map each id to one of three actions: `absorb` (rewrite the query), `ignore` (EXACT no-op: `prepareTimeSeries`, a `sortBy` the table widget already applies), or `block` (the panel stays manual with the id in the reason). Never `ignore` a transformation that changes values. The reason strings in `reasons.py` (`P_TRANSFORM`) should name the id.


## 4. (B) Field config, dashboard settings and variables

Grafana keeps display settings in `panel.fieldConfig.defaults` (applies to every field) and `panel.fieldConfig.overrides[]` (per matcher), plus panel `options` (per plugin). The New Relic provider has a per-widget settings block with a smaller vocabulary (section 2). Frequencies are MEASURED over the 3,633 panels and 180 dashboards of section 10 unless stated.

### 4.1 Overrides

JSON: `fieldConfig.overrides[] = {matcher: {id, options}, properties: [{id, value}]}`. Matcher ids (S3 `matchers/ids.ts`): `byName`, `byNames`, `byRegexp`, `byRegexpOrNames`, `byType`, `byTypes`, `byFrameRefID`, `byValue` (options: `reducer`, `op`, `value`); the docs name five rule kinds: name, regex, type, query refId, reducer value (G2). Any property of `fieldConfig.defaults` can be overridden, including every `custom.*` key. Overrides apply to bar chart, bar gauge, candlestick, canvas, gauge, geomap, heatmap, histogram, pie, stat, state timeline, status history, table, time series, trend and XY chart (G2). Legacy graph panels use `seriesOverrides[{alias, color, fill, lines, bars, points, linewidth, yaxis, stack, zindex, dashes, hiddenSeries, transform, legend, hideTooltip}]`, where `alias` is a name or a `/regex/`.

New Relic: `colors.series_overrides[{series_name, color}]`, `units.series_overrides[{series_name, unit}]`, `null_values.series_overrides[{series_name, null_value}]`, `y_axis_right.y_axis_right_series[]` (T1). Everything keyed by an exact series name. Nothing keyed by regex, type, query or value.

Corpus (MEASURED): 522 panels (14.4%) and 64 dashboards (36%) have overrides; 1,591 rules: `byRegexp` 864, `byName` 616, `byFrameRefID` 65, `byValue` 46. Properties (about 2,300 in total): `color` 884, `unit` 227, `custom.width` 162, `displayName` 127, `custom.fillOpacity` 108, `max` 95, `min` 93, `custom.lineStyle` 87, `custom.lineWidth` 70, `custom.hideFrom` 68, `custom.axisPlacement` 67, `custom.transform` 62, `decimals` 42, `custom.align` 34, `custom.displayMode` 29, `custom.cellOptions` 29, `thresholds` 25, `custom.hidden` 22, `links` 20, `custom.stacking` 19, `mappings` 10. 224 legacy graph panels have `seriesOverrides`.

| property | New Relic | verdict | prerequisite and recipe |
|---|---|---|---|
| `color` (`{mode: fixed, fixedColor}`) | `colors.series_overrides` | APPROXIMATE | EXACT only when the series names the matcher selects are known at conversion time. A query with a constant `legendFormat` gives one known name (`AS 'name'`). A `{{pod}}` legend gives names the converter never sees; the override is lost. `fixedColor` is a name (`red`, `dark-green`) or hex; New Relic takes a "color code" (T1); map names to hex using Grafana's palette |
| `unit` | `units.series_overrides` | APPROXIMATE | same series-name condition; unit mapping in 4.4 |
| `displayName` | `AS 'name'` in the query | EXACT | for known series; a templated displayName with `${__field.labels.x}` needs the facet label (see 4.7 legend format) |
| `custom.fillOpacity`, `custom.lineWidth`, `custom.lineStyle`, `custom.hideFrom` (legend or viz), `custom.stacking`, `custom.fillBelowTo`, `custom.transform` | none | NONE | per-series styling is not in the provider. `hideFrom.viz` -> drop the query or series from the select list; `hideFrom.legend` -> none |
| `custom.axisPlacement: right` | `y_axis_right{y_axis_right_series}` (line only) | APPROXIMATE | series known by name; right-axis `min` and `max` -> `y_axis_right_min/max` |
| `min`, `max` | `y_axis_left_min/max`, or right axis | APPROXIMATE | per-series min or max cannot be kept; only an axis can |
| `custom.width`, `custom.align`, `custom.displayMode`, `custom.cellOptions`, `custom.hidden`, `custom.filterable`, `custom.inspect` (table) | none | NONE | column width, alignment, cell type (`color-text`, `color-background`, `gauge`, `json-view`, `sparkline`, `image`) have no table setting. `custom.hidden` -> omit the column from `SELECT`. Threshold colouring of cells -> table `threshold{column_name}` (T1) |
| `thresholds` | line `threshold[]` or table `threshold[]` (`column_name`), billboard `critical`/`warning` | APPROXIMATE | see 4.3 |
| `mappings` | none | NONE | see 4.2 |
| `links` | none per field | NONE | see 4.7 |
| `decimals` | `data_format{name, type=decimal, precision}` on tables and billboards | APPROXIMATE | needs the column name; not for charts |

Matchers. `byFrameRefID` selects every field of one query: if that query has a constant alias, EXACT for colour and unit overrides; `byName` equals the Prometheus legend or the field display name; `byRegexp` is JavaScript syntax, RE2-incompatible forms (lookahead) fail; `byType` (string, number, time, boolean) has no series meaning in NRQL; `byValue` (series whose reduced value meets a condition) needs the data and cannot be converted. Recipe for the planner: list each override with its matcher; if `byName`/`byFrameRefID`/`byRegexp` resolves against a constant-alias series set, emit the series override; otherwise record the matcher id and the property ids in the widget `reasons` (REPO `IRWidget.reasons`, `src/importer/core/dashboard_ir.py`; the dashboard IR has no `unmapped` field) and lower the confidence to approximate.

### 4.2 Value mappings

JSON: `fieldConfig.defaults.mappings[]` of four types (S5 `valueMapping.ts`): `{type: "value", options: {"<value>": {text, color, index}}}`, `{type: "range", options: {from, to, result: {text, color, index}}}`, `{type: "regex", options: {pattern, result}}`, `{type: "special", options: {match: null|nan|null+nan|true|false|empty, result}}`. The legacy forms are numeric: `mappingType` 1 (value) and 2 (range) with `valueMaps[]` and `rangeMaps[]` on `singlestat`, migrated at schema version 30 (base note 2.4). Supported on bar chart, bar gauge, candlestick, canvas, gauge, geomap, histogram, pie, stat, state timeline, status history, table, time series, trend (G3). Mappings bypass unit formatting (G3).

New Relic: no mapping property on widgets (T1). In the query, `if(cond, a, b)` and `FACET CASES (WHERE ... AS 'label')` rename or bucket values of event attributes (N1 `func-if`, `sel-facet-cases`). The `if()` examples in the docs apply to attributes inside `FACET` (N1); `if()` over an aggregate result is not shown (UNVERIFIED).

| mapping | verdict | recipe |
|---|---|---|
| `value` or `range` on a categorical or numeric event attribute used as a facet or table column | DIFFERENT | `FACET if(attr = 1, 'UP', 'DOWN')` or `FACET CASES (WHERE attr = 1 AS 'UP', WHERE attr = 0 AS 'DOWN')`. Colours of the result text are lost |
| `value` or `range` on a stat, gauge or bar gauge (`1 -> UP`) | NONE | the number is the widget value; show the number with a threshold colour and put the legend in the title |
| `special: null / nan / null+nan` (84 of the 142 modern mappings) | NONE | commonly "N/A" or "No data" text; New Relic shows its own empty state. `noValue` (101 panels) likewise has no setting |
| `regex` | NONE for display; `capture()` can rewrite facet values | cut a prefix: `FACET capture(host, r'(?P<h>[^.]+)')` |

Corpus: 129 panels with modern `mappings[]` holding 142 mappings (`special` 84, `value` 24, legacy numeric type 1 24, `range` 9, legacy type 2 1) and 320 panels with legacy `valueMaps`/`rangeMaps` (MEASURED); 83 of 180 dashboards (46%) have at least one mapping. The mapping texts were not inspected. The `special` kind matches null, NaN, empty, true or false by construction (S5), so most of the 84 are "no data" style texts. Mappings are the largest single reason to keep singlestat and stat panels at "approximate" rather than "exact".

### 4.3 Thresholds and colour

JSON: `fieldConfig.defaults.thresholds = {mode: "absolute"|"percentage", steps: [{value: null|number, color}]}` (S5 `thresholds.ts`); the first step has `value: null` (minus infinity, G13). Percentage mode is relative to min and max (G13). `color = {mode, fixedColor, seriesBy}` with modes `thresholds`, `palette-classic`, `palette-classic-by-name`, `palette-saturated`, `palette-colorblind`, `palette-categorical-next*` (flagged experimental), `continuous-GrYlRd`, `continuous-RdYlGr`, `continuous-BlYlRd`, `continuous-YlRd`, `continuous-BlPu`, `continuous-YlBl`, `continuous-blues`, `continuous-reds`, `continuous-greens`, `continuous-purples`, `continuous-viridis`, `continuous-magma`, plus `fixed` and `shades` (S5 `fieldColor.ts`). Time series `custom.thresholdsStyle.mode`: `off`, `line`, `dashed`, `area`, `line+area`, `dashed+area`, `series` (S6).

New Relic: line `threshold[{name, from, to, severity}]` (ranges; line charts draw boundary lines and shaded regions, N3), table `threshold[{column_name, from, to, severity}]` (cell colours, N3), billboard `critical` and `warning` (two numbers). Severities: `success`, `warning`, `unavailable`, `severe`, `critical` (T3). The UI has five levels (Good, Warning, Approaching critical, Critical, Neutral) on billboard and gauge (N4) but the provider exposes two for billboard. Colours are fixed by severity, not free.

| Grafana | New Relic | verdict | recipe |
|---|---|---|---|
| absolute steps on a stat (green, orange, red) | billboard `warning` and `critical` | APPROXIMATE | first red step -> `critical`, first orange or yellow step -> `warning`; green base step dropped. A billboard "lower is worse" scheme needs `warning` above `critical` (N4). Steps with colours other than green/yellow/orange/red (blue, purple, `#1F60C4`) have no severity |
| absolute steps on a time series | line `threshold` ranges, severity from colour (green -> `success`, yellow/orange -> `warning`, dark-red -> `severe`, red -> `critical`) | APPROXIMATE | `from` = step value, `to` = next step value; the mapping from colour name to severity is an assumption (UNVERIFIED) |
| absolute steps on a table column | table `threshold` with `column_name` | APPROXIMATE | needs the column name; overrides with `thresholds` + `custom.cellOptions: color-background` are the common form |
| `mode: percentage` (37 panels) | none directly | NONE unless `min` and `max` are both set | with `min`/`max`: absolute = min + pct * (max - min), then as above |
| `color.mode: palette-classic` (1,075 panels) | New Relic palette | EXACT (default) | nothing to do; series colours differ from Grafana |
| `color.mode: fixed` (140) | `colors.color` | EXACT | a single colour per widget; hex or name -> hex |
| `color.mode: thresholds` (265) | threshold colouring (billboard, line, table) | APPROXIMATE | see above |
| `shades` (28), `continuous-*` (10), `palette-*` other | none | NONE | |
| `thresholdsStyle` (56 time series show it) | the line `threshold` itself draws boundary lines and areas | APPROXIMATE | `dashed`, `series` styles lost |
| stat `colorMode` `background`, `background_solid`, `value`, `none` (195 stats use a background) | billboard threshold colours the tile | APPROXIMATE | fixed to New Relic's styling |

Corpus: 1,577 absolute and 37 percentage threshold blocks; step colours `green` 1,434, `red` 1,079, `#EAB839` 46, `orange` 40, `transparent` 37, `dark-red` 28, `yellow` 18 (MEASURED).

### 4.4 Units

JSON: `fieldConfig.defaults.unit` is a string id from `categories.ts` (S4): 274 `id:` entries (`grep -c "id: '"` over the file) in 25 categories (Misc 15, Acceleration 3, Angle 5, Area 5, Computation 8, Concentration 12, Currency 30, Data 14, Data rate 25, Date & time 8, Energy 40, Flow 8, Force 4, Hash rate 7, Mass 5, Length 7, Pressure 8, Radiation 13, Rotational Speed 7, Temperature 3, Time 15, Throughput 19, Velocity 4, Volume 6, Boolean 3). Legacy panels use `format` (singlestat) and `yaxes[].format` (graph), which the frontend migrates to `unit` (base note 2.4). A custom unit is written `suffix:text`, `prefix:text`, `time:FORMAT` or `si:text` (UNVERIFIED for the syntax; the corpus has `si:/s`, `time: YYYY-MM-DD HH:mm:ss`, `count:req/day`, `p/m`).

New Relic: `units{unit}` at widget level and `units.series_overrides` per series for area, line and stacked bar (N3 "Units customization"); `data_format` for table and billboard columns (T2). The accepted `unit` strings are not enumerated in the provider documentation or schema (T1, T2). Two lists of names exist in the chart library docs (N20): charts take `UNKNOWN`, `COUNT`, `PERCENTAGE`, `MS`, `TIMESTAMP`, `BITS`, `BITS_PER_SECOND`, `BYTES`, `BYTES_PER_SECOND`; table cells take `APDEX`, `BITS`, `BITS_PER_MS`, `BITS_PER_SECOND`, `BYTES`, `BYTES_PER_MS`, `BYTES_PER_SECOND`, `CELSIUS`, `COUNT`, `HERTZ`, `MS`, `PAGES_PER_SECOND`, `PERCENTAGE`, `REQUESTS_PER_SECOND`, `SECONDS`, `TIMESTAMP`, `UNKNOWN`. The provider's own test uses `"BYTES"` (T3) and its doc example uses `"ms"` (T2). I take the second list as the candidate set for dashboard widgets; whether the dashboard accepts all of them is UNVERIFIED and needs one read-back in the sandbox (the repository rule: do not write an unconfirmed name). Semantics that matter (N20, N3): `PERCENTAGE` formats `1` as 100%, so Grafana `percent` (0 to 100) needs `/100` in the NRQL; `MS` humanises values above 1 (`60000` as "1 minute") and uses SI prefixes below 1; byte units use SI (1 KB = 1,000 bytes, N3 callout).

Grafana semantics (S4): `bytes`, `bits` and the `k/m/g/t/pbytes` family are IEC (1024); `decbytes`, `decbits` and `deck/dec m/...bytes` are SI (1000); `Bps`, `bps`, `KBs`... are SI, `binBps`, `binbps`, `KiBs`... are IEC; `percent` is 0 to 100, `percentunit` 0 to 1; `s`, `m`, `h`, `d` input is in that unit; `dtdurationms` and `dtdurations` format a duration given in ms or s; date ids expect epoch milliseconds.

The 60 most common ids: the 40 that occur in the corpus (counts are panels with that `unit` or `format`, MEASURED) completed with the ones a Prometheus, CloudWatch or SQL dashboard commonly uses. "scale" is what the NRQL must multiply by so the New Relic unit shows the right number.

| # | Grafana id | meaning | corpus | New Relic unit | NRQL scale | verdict |
|---|---|---|---|---|---|---|
| 1 | `none` | number | 366 | `UNKNOWN` (omit) | | EXACT |
| 2 | `short` | 1.2 K, 3 Mil | 580 | `COUNT` | | APPROXIMATE (prefix words differ) |
| 3 | `sishort` | SI prefix | 0 | `COUNT` | | APPROXIMATE |
| 4 | `percent` | 0-100 | 126 | `PERCENTAGE` | `/ 100` | EXACT (with scaling) |
| 5 | `percentunit` | 0-1 | 119 | `PERCENTAGE` | | EXACT |
| 6 | `bytes` | IEC | 167 | `BYTES` | | APPROXIMATE (1024 vs 1000 display) |
| 7 | `decbytes` | SI | 83 | `BYTES` | | EXACT |
| 8 | `bits` | IEC | 1 | `BITS` | | APPROXIMATE |
| 9 | `decbits` | SI | 1 | `BITS` | | EXACT |
| 10 | `kbytes` | KiB input | 0 | `BYTES` | `* 1024` | APPROXIMATE |
| 11 | `mbytes` | MiB input | 2 | `BYTES` | `* 1048576` | APPROXIMATE |
| 12 | `gbytes` | GiB input | 26 | `BYTES` | `* 1073741824` | APPROXIMATE |
| 13 | `tbytes` | TiB input | 0 | `BYTES` | `* 1099511627776` | APPROXIMATE |
| 14 | `deckbytes` | KB input | 2 | `BYTES` | `* 1000` | EXACT |
| 15 | `decmbytes` | MB input | 4 | `BYTES` | `* 1e6` | EXACT |
| 16 | `decgbytes` | GB input | 0 | `BYTES` | `* 1e9` | EXACT |
| 17 | `dectbytes` | TB input | 0 | `BYTES` | `* 1e12` | EXACT |
| 18 | `Bps` | bytes/s SI | 49 | `BYTES_PER_SECOND` | | EXACT |
| 19 | `binBps` | bytes/s IEC | 14 | `BYTES_PER_SECOND` | | APPROXIMATE |
| 20 | `bps` | bits/s SI | 16 | `BITS_PER_SECOND` | | EXACT |
| 21 | `binbps` | bits/s IEC | 4 | `BITS_PER_SECOND` | | APPROXIMATE |
| 22 | `KBs` | KB/s input | 0 | `BYTES_PER_SECOND` | `* 1000` | EXACT |
| 23 | `MBs` | MB/s input | 0 | `BYTES_PER_SECOND` | `* 1e6` | EXACT |
| 24 | `GBs` | GB/s input | 0 | `BYTES_PER_SECOND` | `* 1e9` | EXACT |
| 25 | `Kbits` | Kb/s input | 0 | `BITS_PER_SECOND` | `* 1000` | EXACT |
| 26 | `Mbits` | Mb/s input | 0 | `BITS_PER_SECOND` | `* 1e6` | EXACT |
| 27 | `ns` | nanoseconds | 1 | `MS` | `/ 1e6` | EXACT |
| 28 | `µs` | microseconds | 0 | `MS` | `/ 1000` | EXACT |
| 29 | `ms` | milliseconds | 35 | `MS` | | EXACT |
| 30 | `s` | seconds | 124 | `MS` (charts) or `SECONDS` (table cells) | `* 1000` for `MS` | EXACT |
| 31 | `m` | minutes | 0 | `MS` | `* 60000` | EXACT |
| 32 | `h` | hours | 0 | `MS` | `* 3600000` | EXACT |
| 33 | `d` | days | 1 | `MS` | `* 86400000` | EXACT |
| 34 | `dtdurationms` | duration in ms | 1 | `MS`; `data_format type=duration` on tables | | EXACT |
| 35 | `dtdurations` | duration in s | 3 | `MS`; `data_format type=duration` | `* 1000` | EXACT |
| 36 | `dthms` | hh:mm:ss | 0 | `MS` | `* 1000` | APPROXIMATE (format differs) |
| 37 | `hertz` | Hz | 1 | `HERTZ` (table cells) | | APPROXIMATE (chart unit unconfirmed) |
| 38 | `reqps` | requests/s | 40 | `REQUESTS_PER_SECOND` (table cells) | | APPROXIMATE (chart unit unconfirmed) |
| 39 | `rps` | reads/s | 2 | `COUNT` | | APPROXIMATE (per-second lost) |
| 40 | `ops` | ops/s | 49 | `COUNT` | | APPROXIMATE |
| 41 | `iops` | I/O ops/s | 4 | `COUNT` | | APPROXIMATE |
| 42 | `eps` | events/s | 16 | `COUNT` | | APPROXIMATE |
| 43 | `cps` | counts/s | 8 | `COUNT` | | APPROXIMATE |
| 44 | `wps` | writes/s | 0 | `COUNT` | | APPROXIMATE |
| 45 | `pps` | packets/s | 28 | `COUNT` | | APPROXIMATE |
| 46 | `reqpm` | requests/min | 0 | `REQUESTS_PER_SECOND` | `/ 60` | APPROXIMATE |
| 47 | `cpm` | counts/min | 2 | `COUNT` | | APPROXIMATE |
| 48 | `opm` | ops/min | 0 | `COUNT` | | APPROXIMATE |
| 49 | `celsius` | degrees C | 1 | `CELSIUS` (table cells) | | APPROXIMATE |
| 50 | `fahrenheit` | degrees F | 0 | `CELSIUS` | `(x - 32) * 5 / 9` | APPROXIMATE |
| 51 | `locale` | thousands separators | 3 | `COUNT` | | APPROXIMATE |
| 52 | `string` | text | 3 | `UNKNOWN` | | EXACT |
| 53 | `bool` | true/false | 1 | none | | NONE |
| 54 | `bool_yes_no` | Yes/No | 2 | none | | NONE |
| 55 | `bool_on_off` | On/Off | 3 | none | | NONE |
| 56 | `dateTimeAsIso` | ISO time | 2 | `TIMESTAMP`; `data_format type=date` | | EXACT |
| 57 | `dateTimeAsLocal` | local time | 2 | `TIMESTAMP`; `data_format type=date` | | EXACT |
| 58 | `dateTimeFromNow` | "3 days ago" | 5 | `data_format type=recent-relative` | | EXACT |
| 59 | `currencyUSD` | dollars | 41 | none | | NONE |
| 60 | `currencyEUR` | euros | 0 | none | | NONE |

Everything else in the 274 ids (length, mass, area, volume, pressure, energy and electrical units, flow, force, angle, acceleration, radiation, concentration, hash rate, flops, rotational speed, velocity, humidity, `dB`, `hex`, `sci`, `ordinal`, `pixel`, the other 28 currencies) has no New Relic unit among the names above: NONE. The converter should keep the number and write the unit into the widget title or description. Totals for the 60: EXACT 29, APPROXIMATE 26, NONE 5. The corpus top-10 (short 580, none 366, bytes 167, percent 126, s 124, percentunit 119, decbytes 83, ops 49, Bps 49, currencyUSD 41) are 1,704 of 1,980 unit-bearing panels (86%).

Corpus note: 1,980 of 3,633 panels (54%) carry a `unit` or legacy `format`; `yaxes[].format` of legacy graph panels adds `short` 1,421, `bytes` 190, `s` 122, `Bps` 107, `none` 83, `pps` 80, `ops` 48, `percentunit` 47, `decbytes` 35, `percent` 31, `ms` 15 axis-format entries, which duplicate the migrated unit.

### 4.5 Decimals, min and max, null handling

| Grafana | JSON | New Relic | verdict | note and corpus |
|---|---|---|---|---|
| decimals | `defaults.decimals` | `data_format{type=decimal, precision}` on table and billboard | APPROXIMATE | no setting on line or area. 325 panels (9%) |
| `humanized` autoformat | unit and no decimals | `data_format type=humanized` | EXACT | default behaviour of New Relic numbers |
| min and max | `defaults.min`, `defaults.max` | `y_axis_left_min`, `y_axis_left_max` (line, area, bar) | EXACT | hard limits. `min` is set (not null) on 514 panels (14%), `max` on 92 (2.5%) |
| soft limits | `custom.axisSoftMin`, `axisSoftMax` | `y_axis_left_zero` for softMin 0 (semantics UNVERIFIED) | APPROXIMATE | 57 time series panels |
| gauge / bar gauge min and max | same keys | gauge `min`/`max` exist in the UI (N4), not in the provider; `viz.bullet` needs `limit` (T1) | APPROXIMATE | bullet shows value against a limit |
| null handling (time series) | `custom.spanNulls` (boolean or max gap in ms), `custom.insertNulls` | `null_values{null_value: default|remove|preserve|zero}` (T1, N3) | APPROXIMATE | `spanNulls: true` -> `remove` (points removed so the line is continuous: the text says "Null points won't exist", whether the line joins neighbours is UNVERIFIED); 143 time series use `spanNulls` truthy |
| legacy graph null mode | `nullPointMode` `null` 662, `connected` 255, `null as zero` 183 | `default`, `remove`, `zero` | APPROXIMATE | `preserve` (last value) has no Grafana twin |
| null in stat value | `reduceOptions.calcs` (`lastNotNull` 321, `last` 73, `mean` 61, `sum` 15) | `latest()`, `average()`, `sum()` | EXACT | a stat with `lastNotNull`: `latest()` ignores null in NRQL (UNVERIFIED) |
| `noValue` | text for empty | none | NONE | 101 panels |
| `calcs` on reduce | `max`, `min`, `delta`, `diff` | `max()`, `min()`; `delta`/`diff` none | EXACT / NONE | |

### 4.6 Time series styling, legend, tooltip

| Grafana | JSON | New Relic | verdict | corpus |
|---|---|---|---|---|
| line vs area | `custom.fillOpacity` > 0, legacy `fill` > 0 | `viz.line` vs `viz.area` | APPROXIMATE | 803 time series and 958 graph panels have fill; the area widget also stacks series in New Relic? UNVERIFIED |
| stacking | `custom.stacking.mode` `normal` or `percent`, `group`; legacy `stack`, `percentage` | `viz.area` (stacking UNVERIFIED), `viz.stacked-bar` (T3, time axis UNVERIFIED) | APPROXIMATE for `normal`; NONE for `percent` | 155 time series, 183 graph panels, 3 graph `percentage` |
| gradient | `custom.gradientMode` `hue`, `opacity`, `scheme` | `chart_styles.gradient.enabled` (area) | APPROXIMATE | 256 time series |
| interpolation | `custom.lineInterpolation` `linear`, `smooth`, `stepBefore`, `stepAfter`; legacy `steppedLine` | `chart_styles.line_interpolation` with the same four values (line, area, billboard; T2) | EXACT | 177 time series; 43 graph `steppedLine` -> `stepAfter` (UNVERIFIED) |
| draw style | `custom.drawStyle` `line`, `bars`, `points`; legacy `bars`, `points` | none for bars over time; `viz.bar` is categorical (base note 4.1) | NONE | 46 `bars`, 10 `points`; 52 graph `bars` |
| line width, dash, point size, show points | `lineWidth`, `lineStyle`, `pointSize`, `showPoints` | none | NONE | |
| axis | `axisPlacement`, `axisLabel`, `axisWidth`, `axisCenteredZero`, `axisColorMode`, `axisGridShow`, `axisBorderShow`, `scaleDistribution` (`linear`, `log`, `symlog`, `ordinal`) | right axis by series; nothing else | NONE | log scale 3 time series and 6 graph axes; `axisLabel` 80 |
| legend visibility | `options.legend.showLegend`, `displayMode` `list`, `table`, `hidden`; legacy `legend.show` | `legend_enabled` | EXACT | 12 hidden |
| legend placement | `placement` `bottom`, `right` (271 right) | none | NONE | |
| legend values | `legend.calcs[]` (778 panels), legacy `legend.values`, `avg`, `max`, `min`, `current`, `total` (628 graph panels) | none in the legend; add a table widget with `SELECT latest(x), average(x), max(x), min(x) FACET series` | DIFFERENT | 777 panels use table mode, 611 graph panels `alignAsTable`. One extra widget per chart |
| tooltip mode | `options.tooltip.mode` `single`, `multi`, `none`; `sort` | `tooltip{mode: single|all|hidden}` (line, area, stacked bar) | EXACT | `multi` 880, `single` 254 |
| shared crosshair | dashboard `graphTooltip` 0, 1 (crosshair), 2 (tooltip) | none | NONE | 53 of 180 dashboards (29%) |
| series order, hide series | `legend.sortBy`, `hideFrom` | none | NONE | |
| legend text | `legendFormat` `{{label}}`, `__auto` | `AS` for constant text; `FACET` value for label text | APPROXIMATE | base note 5.5; legendFormat on 3,677 targets |

### 4.7 Links and drilldown

JSON: field links in `fieldConfig.defaults.links[]` and in a `links` override property; panel links in `panel.links[]`; all `{title, url, targetBlank, oneClick?, internal?}` (S5 `dataLink.ts`); dashboard links in `dashboard.links[]` of type `link` or `dashboards` with `url`, `tags`, `asDropdown`, `includeVars`, `keepTime`, `targetBlank`, `icon`, `tooltip` (G19). Variables inside URLs: `${__value.raw|text|numeric|time}`, `${__data.fields.NAME}` and `${__data.fields[0]}`, `${__field.name}`, `${__field.labels.X}`, `${__series.name}`, `${__from}`, `${__to}`, `${__url_time_range}`, `${__all_variables}`, `${varname}` (G12, G6). 

Corpus: panel links 49 panels (1.3%), field links 43 default plus 20 override (1.7%), dashboard links on 37 of 180 dashboards (21%: 59 `link`, 29 `dashboards`); `${__url_time_range}` 34 uses, `${__data.fields}` 32, `${__field.name}` 30, `${__all_variables}` 1 (MEASURED).

| item | New Relic | verdict | recipe |
|---|---|---|---|
| static panel or data link (`https://runbook/...`) | widget `link` (one URL, http/https, 2,048 characters, in the dashboard JSON `link`, N3) or `billboard_settings.link{url, title, new_tab}` (T1) | EXACT for one link | only the first link of several; the rest go into the widget `description` (plain text, N3) |
| templated link (`${__value.raw}`, `${__url_time_range}`, `${var}`) | none: the NRQL result cannot be substituted into the URL | NONE | drop with a reason; if the URL only carries `${__url_time_range}` and `var-x` for another Grafana dashboard, the target does not exist in New Relic |
| internal links (to Explore with a query) | none | NONE | |
| drilldown into another dashboard filtered by the clicked facet | facet filtering: `filter_current_dashboard` and `linked_entity_guids` on bar, heatmap, pie, table widgets (T1; needs a `FACET`, N15) | DIFFERENT | only for facet-based widgets; `{{selfEntity}}` in `linkedEntityGuids` keeps links to the dashboard's own copy (N15); needs the target dashboard's GUID, i.e. two-pass creation |
| dashboard `links` (type `link`) | no link bar; a markdown widget with the links | DIFFERENT | one top-of-page widget; escape text through `md_cell`; URLs with `includeVars`, `keepTime` lose the parameters |
| dashboard `links` of type `dashboards` (by tag) | none | NONE | list the matching dashboards by name in the report |

### 4.8 Annotations

JSON (classic): `annotations.list[]` entries `{name, enable, hide, iconColor, builtIn?, datasource, type: "dashboard"|"tags", tags[], limit, matchAny, showIn, expr | query | target, titleFormat, textFormat, tagKeys, step, useValueForTime}`; v2: `annotations[]` of `AnnotationQuery` (base note 7.1). Panel-level annotation visibility is `options.annotations` (S6). The built-in entry (`-- Grafana --`) shows annotations stored in Grafana and alert state; other entries query a data source (G11). Supported on time series, state timeline and candlestick (G11). The Grafana data source adds query types "Annotations and alerts" and "Time regions" (G14).

New Relic: change tracking records (deployments, config changes) are shown as markers on charts of APM, browser, mobile and custom dashboards (N14), created through NerdGraph (`changeTrackingCreateEvent`), the New Relic CLI or a GitHub Action (N14 pages `change-tracking/config/*`); they attach to an entity GUID. There is no query-based annotation layer in the dashboard schema (T1).

| Grafana | verdict | recipe |
|---|---|---|
| built-in (152 in the corpus) | NONE | nothing to migrate; stored annotations live in Grafana's database (not in a dashboard export) |
| custom annotation query, 26 in the corpus (7 of 69 dashboards in the base note) | DIFFERENT | not convertible as a dashboard setting. Offer a follow-up task: write events to New Relic when they happen (deployment markers), which needs the entity GUID and a producer. Report the query (escaped), data source type, `enable` flag |
| time regions | NONE | |
| alert state overlays | NONE | |

### 4.9 Time controls

JSON: dashboard `time{from, to}`, `timepicker{refresh_intervals[], hidden, nowDelay, time_options}`, `refresh` (string), `timezone`, `fiscalYearStartMonth`, `weekStart`, `liveNow`; panel `timeFrom`, `timeShift`, `hideTimeOverride`, `interval`, `maxDataPoints`, `cacheTimeout`. Relative-time and shift expressions use `now-5m`, `now/d`, `now-5d/d`, `1w/w`, `1M/M` (rounding with `/`); panel overrides have no effect when the dashboard range is absolute (G7). Query options (G7): Max data points (defaults to panel pixel width), Min interval, Interval (`$__interval`), Relative time, Time shift, Cache timeout.

Corpus (MEASURED): default range `now-1h` 46 dashboards, `now-24h` 27, `now-6h` 24, `now-30m` 20, `now-3h` 16, `now-15m` 13, `now-5m` 11, `now-30d` 5; `refresh` unset, empty or false on 87, `10s` on 30, `1m` 24, `30s` 16, `5s` 9, `15s` 7; `timezone` browser 56, utc 25, empty 98; `graphTooltip` set on 53; `nowDelay` 1; `timepicker.hidden` 3; no dashboard sets a non-default `fiscalYearStartMonth` or `weekStart`; panels: `maxDataPoints` 478, `interval` 221, `timeFrom` 15, `hideTimeOverride` 14, no `timeShift`.

New Relic facts: a query without `SINCE` defaults to 1 hour ago (N1 `sel-since`); on a dashboard the time picker supersedes a query's `SINCE` and `UNTIL` unless the widget is set to ignore it (N11 and `query-time-range`); the picker offers last 5 minutes to last 6 months and a custom range (N11); `refresh_rate` is per widget with fixed values (T2); a chart's default refresh depends on the window or the `TIMESERIES` bucket (N16); `COMPARE WITH`, `SINCE x ago UNTIL y ago`, `WITH TIMEZONE` live in the query (N1). The provider has no dashboard-level time field (T1).

| Grafana | New Relic | verdict | recipe |
|---|---|---|---|
| default range `time.from` | `SINCE <n> <unit> ago` in each widget query, overridden by the picker | APPROXIMATE | the base note 7.4 found no dashboard field for a default range and marked the question UNVERIFIED; it can be carried per widget as `SINCE`, and the picker still wins. The IR forbids `SINCE`, `UNTIL`, `COMPARE WITH` in raw queries (`check_raw_nrql`, REPO `docs/ir-spec.md`); a deliberate `default_since` option on the IR dashboard would be needed. Absolute ranges (`from` as a timestamp): `SINCE '2026-01-01 00:00:00'`, rarely wanted |
| panel `timeFrom` (relative time, e.g. `now-5m`) | `SINCE 5 minutes ago` + `ignore_time_range = true` | EXACT | `now/d` (the day so far) -> `SINCE today`; `now-5d/d` -> `SINCE 5 days ago` is not the same alignment (UNVERIFIED) |
| panel `timeShift` | `COMPARE WITH` for an overlay; `SINCE 2 weeks ago UNTIL 1 week ago` + `ignore_time_range` for a shifted window | APPROXIMATE | `1w/w` (last entire week) needs `SINCE last week UNTIL this week` style keywords (N1 time clauses, UNVERIFIED) |
| `hideTimeOverride` | none | NONE | cosmetic |
| `refresh` | widget `refresh_rate` | APPROXIMATE | allowed values 5 s, 30 s, 60 s, 5 min, 30 min, 1 h, 3 h, 12 h, 24 h; map `10s` and `15s` to the nearest (5 s or 30 s) and record it |
| `timepicker.refresh_intervals`, `hidden`, `nowDelay`, `liveNow` | none | NONE | `nowDelay` has no equivalent in New Relic's picker |
| `timezone` (browser or named) | none; `WITH TIMEZONE` only affects date strings inside a query | NONE | user-level time zone belongs to the New Relic user profile (UNVERIFIED) |
| `fiscalYearStartMonth`, `weekStart` | none | NONE | |
| `maxDataPoints`, `interval`, `$__interval` | `TIMESERIES AUTO` or `MAX` or a fixed bucket; at most 366 buckets (N19) | APPROXIMATE | see base note 5.4 |
| `cacheTimeout`, `queryCachingTTL` | none | NONE | |

### 4.10 Repeats, rows, tabs

| Grafana | JSON | New Relic | verdict | recipe and corpus |
|---|---|---|---|---|
| repeat panel | `repeat: "var"`, `repeatDirection: "h"|"v"`, `maxPerRow`; clones carry `repeatIteration`, `repeatPanelId` (removed at schema 27) | `FACET` on the variable's label: one series per selected value | APPROXIMATE | works for series-style panels; for a stat repeated per host use a faceted billboard (grid of billboards, T1 `billboard_settings.grid_options{columns}`). 43 panels (36 `h`, 6 `v`) on 9 dashboards |
| repeat row | row `repeat: "var"` | none; expand once for the default values (stale) or fold into `FACET` | NONE for a faithful row repeat | 27 rows on 20 of 180 dashboards (11%); the members of a repeated row are the larger loss |
| row (`type: "row"`, `collapsed`, `panels[]`) | | markdown heading widget, or one page per row | DIFFERENT | 123 of 180 dashboards (68%) have rows, 696 row panels, 42 dashboards (23%) have collapsed rows. Base note 7.3 |
| tab (v2 `TabsLayout`) | | one page per tab (25 pages max, N2) | EXACT | |
| show/hide rules (v2 conditional rendering: variable value, time range shorter than, data present; G10) | `ConditionalRenderingVariable`, `...Data`, `...TimeRangeSize` | none | NONE | the panel is shown always; record the rule |
| grid and auto grid layouts | `gridPos{x, y, w, h}` 24 columns, 30 px per unit (base note) | `column`, `row`, `width`, `height`; 12 columns, height up to 32 (N2) | EXACT after scaling | column = x // 2 + 1, width = round(w / 2); height factor unconfirmed (base note 4) |
| library panels | `libraryPanel{uid, name}` (0 in the corpus; classic exports inline them under `__elements`: key present on 71 files, empty on all) | resolve with `/api/library-elements` first | | base note 3 |

### 4.11 Dashboard variables

Grafana types (S5 `templateVars.ts`, G5): `query`, `custom`, `textbox`, `constant`, `datasource`, `interval`, `adhoc` (renamed "Filter" in Grafana 13.1, kind still `AdhocVariable`), `groupby`, `switch`, and the internal `user`, `org`, `dashboard`, `snapshot`. Common fields: `name`, `label`, `description`, `hide` (0 show, 1 hide label, 2 hide variable, 3 in controls menu; `VariableHide` in S5), `skipUrlSync`, `current`, `options`, `multi`, `includeAll`, `allValue`, `allowCustomValue`. Query variables add `datasource`, `definition`, `query`, `regex`, `regexApplyTo` (`value`|`text`), `sort` (0 disabled, 1 alphabetical asc, 2 desc, 3 numerical asc, 4 desc, 5 case-insensitive asc, 6 desc, 7 natural asc, 8 desc), `refresh` (1 on dashboard load, 2 on time range change), `staticOptions`, `staticOptionsOrder`. Interval variables: `auto`, `auto_count`, `auto_min`, `query` (comma list). Custom variables: `query` comma list with optional `name : value` pairs, or `valuesFormat` `csv`|`json`.

Corpus (MEASURED): 626 variables in 171 dashboards: query 429, datasource 112, interval 35, custom 28, textbox 8, constant 8, adhoc 6. Query variables: `refresh` 1 on 241, 2 on 167, unset on 21; `sort` 1 on 199, 0 or unset on 177, 5 on 31, 3 on 11, 2 on 8, 4 on 3; with `regex` 52 (12%, of which 8 use lookaround or backreferences); referencing another variable 223 (52%); `label_values()` 331 (77%), `query_result()` 40 (9%), others (CloudWatch `dimension_values`, `regions()`, `ec2_instance_attribute`) 45, empty 13. All variables: `multi` 152 (24%), `includeAll` 219 (35%), custom `allValue` 121 (19%: `.*` 94, `*` 12, `.+` 11, `All` 3); `hide: 2` on 97 (15%); interval variables with `auto` 23 of 35.

New Relic variable facts (N5, N18, T1, T2): types `nrql`, `enum`, `string`; multi-select for nrql and enum only; `default_values = ["*"]` selects all; `replacement_strategy`: `default` (a value with quotes added when needed), `identifier`, `number`, `string`; use inside `SELECT`, `FROM`, `FACET`, `ORDER BY`, `WHERE`, and as a function argument (`percentile(duration, {{percentile}})`, N5); not allowed in `AS`, `COMPARE WITH`, `LIMIT`, `OFFSET`, `SINCE`, `SLIDE BY`, `TIMESERIES`, `UNTIL`, `WITH` (N5); `options.excluded` ("Include variable" toggle) replaces the variable's condition with `true` (N5 section "Include and exclude variables"); variable queries are capped by `uniques()` limits (5,000 by default, `uniques(attr, 10000)`, N5); nested variables (a variable query that uses another variable) exist only for some plans (N18); PDF export and public sharing do not render widgets with variables (N5, N12).

| Grafana | New Relic | verdict | recipe |
|---|---|---|---|
| `query` + `label_values(metric, label)` | `nrql` variable: `FROM Metric SELECT uniques(label) WHERE metricName = 'metric'` | APPROXIMATE | base note section 6; the form needs a data check. `label_values(label)` -> no `metricName` filter |
| `query` chained (`label_values(m{job=~"$job"}, instance)`) | nested `nrql` variable | APPROXIMATE | plan-limited (N18); otherwise the child filter is dropped and the dashboard shows more values; 52% of query variables are chained |
| `query` + `query_result()` | none | NONE | |
| `query` + `regex` | `WHERE attr RLIKE '...'` in the variable query | APPROXIMATE | JavaScript regex vs RE2; lookaround fails (8 of 52) |
| `query` + CloudWatch / Azure / SQL / Elasticsearch queries | `nrql` variable on the equivalent attribute | APPROXIMATE or NONE | depends on 6.3 |
| `refresh` 1 / 2 | `options.ignore_time_range` true / false | APPROXIMATE | |
| `sort` | none | NONE | cosmetic; whether `uniques()` returns sorted values is UNVERIFIED |
| `custom` | `enum` with `item[{title, value}]` | EXACT | `name : value` pairs -> title, value |
| `textbox` | `string` | EXACT | no multi-select |
| `constant` | substitute into queries, no variable | EXACT | 8 variables |
| `datasource` | none | NONE | 112 variables (18%). Resolve the variable value to a plugin type to decide convertibility, then drop it. The `__inputs` block of exported files lists `{name: DS_PROMETHEUS, pluginId}`; it exists in 169 of 180 files (prometheus 116, cloudwatch 14, loki 11, postgres 1, cardinality 3) and is the key to resolving `${DS_PROMETHEUS}` placeholders, which appear on tens of thousands of targets |
| `interval` | drop; `TIMESERIES AUTO` | NONE | variables cannot sit in `TIMESERIES` (N5); 35 variables |
| `adhoc` (Filter) | two variables (`key` as identifier, `value` as string) in a `WHERE {{key}} = {{value}}` | APPROXIMATE | one filter only, not a list; UNVERIFIED that identifier replacement works in `WHERE` position (N5 shows it in `FACET`) |
| `groupby` (Grafana 13.1) | variable in `FACET {{x}}` with `identifier` | EXACT | N5 example `FACET {{location}}` |
| `switch` (v2) | `enum` with two items | EXACT | |
| `multi` + `includeAll` | `is_multi_selection = true`, `default_values = ["*"]`, query `IN ({{v}})` | EXACT | |
| custom `allValue` (`.*`, `*`, `.+`) | `options.excluded` | EXACT | "All" in Grafana means "do not filter"; `excluded` drops the condition (N5) |
| `hide: 2` | variable bar visibility (`hiddenOnVariablesBar` in NerdGraph, base note 6) | APPROXIMATE | not in the provider schema (T1) |
| `description`, `label` | `title` | EXACT | |
| `allowCustomValue` | none | NONE | |

Built-in (global) variables (G6; corpus counts are string occurrences in the raw files):

| Grafana | corpus | New Relic | verdict |
|---|---|---|---|
| `$__interval`, `$__interval_ms` | 220 / 0 | `TIMESERIES AUTO` (or `MAX`) | EXACT in intent |
| `$__rate_interval`, `$__rate_interval_ms` | 1,130 / 0 | `rate(sum(x), 1 second)` per bucket; no variable | EXACT in intent (base note 5.2) |
| `$__range`, `$__range_s`, `$__range_ms` | 102 / 18 / 0 | the dashboard window itself: a query without `TIMESERIES` aggregates the picker window | EXACT in intent |
| `$__from`, `$__to`, `${__from:date:iso}` | 0 | none | NONE (links only) |
| `$__timeFilter(col)`, `$timeFilter` | 11 (SQL) | implicit in every NRQL query | EXACT in intent |
| `$__dashboard`, `$__name`, `$__org`, `$__user`, `$__namespace`, `$__timezone`, `$__theme` | 12 / ... | none | NONE |
| `${__url_time_range}`, `${__all_variables}` | 34 / 1 | none | NONE (links) |
| `${var:format}` | `queryparam` 32, `raw` 34, `text` 15 (MEASURED, regex also matched unrelated colons) | `IN ({{v}})` ignores the format | see below |

Format options (G4, S6 `VariableFormatID`): `csv`, `customqueryparam`, `date`, `distributed`, `doublequote`, `glob`, `html`, `json`, `join:<sep>`, `lucene`, `percentencode`, `pipe`, `queryparam`, `raw`, `regex`, `singlequote`, `sqlstring`, `text`, `uriencode`. New Relic substitutes values according to `replacement_strategy` and `IN (...)`, with no formats. Rule: `csv`, `singlequote`, `doublequote` and the default (all in `IN` lists) are EXACT; `pipe`, `regex`, `glob` are regex or glob lists for `=~` and become `IN` or `RLIKE` (APPROXIMATE; `RLIKE` with a variable is shown in N5); `lucene`, `sqlstring`, `json`, `distributed`, `queryparam` and URL formats belong to other query languages or links: NONE. The Prometheus automatic escape (multi-value becomes `(a|b)`, G4) disappears with `IN`.

### 4.12 Snapshots, public dashboards, reports, playlists

| Grafana | What it is | New Relic | verdict |
|---|---|---|---|
| snapshot | static copy of the data stored by Grafana or an external server; `snapshot` object inside a dashboard (G20) | PNG, PDF export, permalink (N11) | NONE for migration; skip snapshots (they carry no queries) |
| public / shared dashboard | token URL, optional time picker and annotations (G20 `shared-dashboards`) | live dashboard URL for Pro and Enterprise; widgets with template variables, facet linking, custom visualisations, log tables and Lens do not render; TV mode stays active 72 hours (N12) | DIFFERENT; recreate after migration; not part of the dashboard JSON |
| reports (Enterprise and Cloud) | scheduled PDF mail (G20) | PDF/PNG export, schedule feature UNVERIFIED | NONE |
| playlists | rotating list of dashboards by uid or tag with interval; kiosk mode | TV mode "Cycle to the next page" over the pages of one dashboard at 20 s, 1 min, 5 min, 30 min, 1 h (N11) | DIFFERENT; only by merging dashboards into pages |
| dashboard versions, permissions, folders | | permissions `private` by default in the emitter (REPO), folders none (dashboards live in a flat list; folder concept is entity-level, UNVERIFIED) | NONE |


## 5. (C) Panels and widgets

Built-in panel plugins in `public/app/plugins/panel` (S7, directory listing of 2026-11-09): `alertlist`, `annolist`, `barchart`, `bargauge`, `candlestick`, `canvas`, `dashlist`, `debug`, `flamegraph`, `gauge`, `geomap`, `heatmap`, `histogram`, `live`, `logs`, `logstable`, `news`, `nodeGraph`, `piechart`, `stat`, `state-timeline`, `status-history`, `table`, `text`, `timeseries`, `traces`, `trend`, `xychart`: 28 directories. The docs list 25 as user-facing visualisations (G17; `debug`, `live`, `logstable` are not in that list). The frontend renames old types when it opens a dashboard: `graph` -> `timeseries`, `singlestat` and `grafana-singlestat-panel` -> `stat`, `table-old` -> `table`, `grafana-piechart-panel` -> `piechart`, `grafana-worldmap-panel` -> `geomap`, `natel-discrete-panel` -> `state-timeline` (base note 2.4; REPO `panels.py` `ALIASES`).

Widgets New Relic can create from the provider (T1, T3): 14 (section 2). What each widget type accepts, from the offline schema (T1): all widgets: `title`, `row`, `column`, `width`, `height`, `refresh_rate`, `ignore_time_range`, `legend_enabled`, `facet_show_other_series`, `nrql_query[]`, `colors`, `units`, `null_values`, `data_format`, `initial_sorting`, `chart_styles`, `y_axis_left_min/max`. Additions: `viz.line` (thresholds, tooltip, `y_axis_left_zero`, `is_label_visible`, right axis); `viz.area` (tooltip, gradient); `viz.stacked-bar` (tooltip); `viz.table` (thresholds by `column_name`, facet linking); `viz.billboard` (`critical`, `warning`, `billboard_settings`); `viz.bar`, `viz.pie`, `viz.heatmap` (facet linking); `viz.bullet` (`limit`); `viz.markdown` (`text`; no query); `viz.histogram`, `viz.funnel`, `viz.json`, `logger.log-table-widget` (common attributes only).

Panel table. Corpus counts are panels of the 3,633 (MEASURED). "Effort" is the work to emit the widget and its options once the query converts; the query work is separate.

| Grafana panel | options that matter (JSON) | New Relic | verdict | effort | corpus |
|---|---|---|---|---|---|
| `timeseries` | `fieldConfig.defaults.custom.*` (4.6), `options.legend`, `options.tooltip` | `viz.line` or `viz.area` with thresholds, tooltip, null handling, units, interpolation | APPROXIMATE | S (exists in IR) | 1,236 |
| `graph` (legacy) | `lines`, `bars`, `points`, `stack`, `fill`, `linewidth`, `nullPointMode`, `yaxes[]`, `seriesOverrides[]`, `legend{}`, `tooltip{}`, `aliasColors` | same as `timeseries` after migration | APPROXIMATE | S | 1,100 |
| `stat` | `options.reduceOptions{calcs, fields, values, limit}`, `colorMode`, `graphMode`, `justifyMode`, `textMode`, `wideLayout`, `showPercentChange`, `percentChangeColorMode`, `orientation` (S6, CODE `stat/panelcfg.cue`) | `viz.billboard`: `critical`, `warning`, `billboard_settings{grid_options, link, visual}` | APPROXIMATE | S (exists) | 478 |
| `singlestat` (legacy) | `valueName`, `sparkline`, `gauge`, `colorBackground`, `colorValue`, `thresholds` string, `valueMaps`, `rangeMaps`, `format` | `viz.billboard` | APPROXIMATE | S | 316 |
| `gauge` | `showThresholdLabels`, `showThresholdMarkers`, `minVizHeight`, `minVizWidth`, `sizing`, `shape` (`gauge`, `circle`), `barShape`, `segmentCount`, `neutral` (CODE `gauge/panelcfg.cue`) | a gauge chart exists in the UI (min, max, label display, threshold markers, N4) but not in the provider; fallback `viz.billboard` with thresholds, or `viz.bullet` with `limit` | APPROXIMATE | S for billboard; M for a real gauge (id unknown) | 71 |
| `bargauge` | `displayMode` (`basic`, `gradient`, `lcd`), `valueMode`, `namePlacement`, `showUnfilled`, `orientation`, `minVizWidth`, `maxVizHeight` (CODE) | `viz.bullet` (needs `limit`, T1) or `viz.bar` with `FACET` | APPROXIMATE | S | 36 |
| `table` | `showHeader`, `sortBy[]`, `cellHeight`, `footer`, `frozenColumns`, `enablePagination`, `pageSize`, `showColumnsSidebar`, field `custom.cellOptions` (CODE `table/panelcfg.cue`) | `viz.table` with `initial_sorting`, `threshold{column_name}`, `data_format`, `filter_current_dashboard` | APPROXIMATE | S (exists) | 115 |
| `table-old` | `styles[]` (per-column pattern, type, unit, decimals, colors, thresholds, link), `transform`, `columns` | `viz.table` | APPROXIMATE | M (styles to data_format and thresholds) | 38 |
| `piechart` | `pieType` (`pie`, `donut`), `displayLabels[]`, `legend{values}`, `reduceOptions`, `tooltip` (CODE) | `viz.pie` | APPROXIMATE | S | 38 |
| `grafana-piechart-panel` | `pieType`, `legendType`, `combine`, `strokeWidth`, `fontSize` (Angular, deprecated; plugin id verified, P1) | `viz.pie` | APPROXIMATE | S | 5 |
| `barchart` | `orientation`, `xField`, `stacking`, `showValue`, `barWidth`, `groupWidth`, `xTickLabelRotation`, `barRadius` (CODE) | `viz.bar` (categorical, `FACET`) or `viz.stacked-bar` (T3) | APPROXIMATE | S | 3 |
| `histogram` | `bucketCount`, `bucketSize`, `bucketOffset`, `combine` (CODE) | `viz.histogram` with `histogram(attr, width, buckets)` | APPROXIMATE | M | 0 |
| `heatmap` | `calculate`, `calculation`, `yAxis`, `cellGap`, `color{scheme, mode, fill, scale, exponent, steps}`, `rowsFrame`, `filterValues` (CODE `heatmap/panelcfg.cue`) | `viz.heatmap` (facet by value bucket) | DIFFERENT | M | 45 |
| `state-timeline` | `showValue`, `rowHeight`, `mergeValues`, `alignValue`, `perPage`, value mappings carry the states (CODE) | none | NONE | | 0 |
| `status-history` | `rowHeight`, `colWidth`, `showValue` (CODE) | none | NONE | | 2 |
| `text` | `mode` (`markdown`, `html`, `code`), `content`, `code{language, showLineNumbers, showMiniMap}` (CODE `text/panelcfg.cue` for code options; `mode` and `content` are from the corpus) | `viz.markdown` (`text`) | EXACT for `markdown`, NONE for `html`/`code` | S (exists) | 122 (19 `markdown`, 19 `html`, 84 unset = markdown) |
| `logs` | `showTime`, `showLabels`, `wrapLogMessage`, `sortOrder`, `dedupStrategy`, `enableLogDetails`, `prettifyLogMessage` (CODE `logs/panelcfg.cue`) | `logger.log-table-widget` (T3) over `Log` | APPROXIMATE | M | 12 |
| `logstable` | `displayedFields`, `sortOrder`, `wrapText`, `allowDownload` (CODE) | `viz.table` or log table | APPROXIMATE | M | 0 |
| `alertlist` | `viewMode`, `groupMode`, `maxItems`, `alertName`, `stateFilter`, `dashboardAlerts` (no panelcfg file in the repository listing, options from memory, UNVERIFIED) | none; a table over alert events (`NrAiIssue`, name from memory, UNVERIFIED) | NONE | | 9 |
| `annolist` | `onlyFromThisDashboard`, `onlyInTimeRange`, `tags`, `limit`, `showUser`, `showTime`, `showTags`, `navigateToPanel` (CODE) | none | NONE | | 0 |
| `dashlist` | `showStarred`, `showRecentlyViewed`, `showSearch`, `showHeadings`, `maxItems`, `query`, `tags`, `folderUID` (CODE) | `viz.markdown` with the list of converted dashboards (names only) | DIFFERENT | S | 0 |
| `news` | `feedUrl`, `showImage` (CODE) | none | NONE | | 0 |
| `geomap` | `view`, `controls`, `basemap`, `layers[]`, `tooltip` (CODE) | none in the provider; no map chart in the chart-type list (N4) | NONE | | 1 |
| `nodeGraph` | `mainStatUnit`, `secondaryStatUnit`, `arcs[]` (CODE) | none (service map is entity-based) | NONE | | 3 |
| `traces`, `flamegraph` | no panelcfg in the listing | none | NONE | | 0 |
| `canvas` | `root{elements[]}`, `inlineEditing`, `showAdvancedTypes` (partly CODE) | none | NONE | | 0 |
| `xychart` | `mapping`, `series[]` (CODE) | none (no scatter widget) | NONE | | 0 |
| `candlestick` | `mode`, `candleStyle`, `colorStrategy`, `fields`, `colors` (CODE) | none | NONE | | 0 |
| `trend` | `xField`, legend, tooltip (CODE) | `viz.line` only when x is time | APPROXIMATE | S | 0 |
| `debug`, `live` | `mode`, `counters`; `channel` | none | NONE | | 0 |

Plugin panels (ids checked from `plugin.json` of the plugin repositories, P1, unless marked).

| plugin | id | what it does | New Relic | verdict | effort | corpus |
|---|---|---|---|---|---|---|
| Polystat | `grafana-polystat-panel` (P1) | hexagon grid, one cell per series, thresholds, composites | `viz.billboard` over `FACET` with `grid_options{columns}` and thresholds | DIFFERENT | M | 0 |
| Boom Table | `yesoreyeram-boomtable-panel` (from memory, not found at the repository paths I tried, UNVERIFIED) | Angular table with patterns and thresholds | `viz.table` with `threshold` and `data_format` | APPROXIMATE | M | 0 |
| Status Panel | `vonage-status-panel` (P1) | tiles by state and threshold | `viz.billboard` grid | DIFFERENT | M | 0 |
| Pie Chart (plugin) | `grafana-piechart-panel` (P1) | auto-migrated to `piechart` | `viz.pie` | APPROXIMATE | S | 5 |
| Sankey | `netsage-sankey-panel` (from memory, UNVERIFIED) | flow diagram | none | NONE | | 0 |
| Business Charts | `volkovlabs-echarts-panel` (P1) | user-written Apache ECharts JavaScript | none | NONE | | 0 |
| Plotly | `natel-plotly-panel` (from memory, UNVERIFIED) | Plotly JSON with data bindings | none | NONE | | 0 |
| Treemap | `marcusolsson-treemap-panel` (P1) | treemap | treemap chart exists in the UI (N4) but not in the provider | APPROXIMATE (UI only; creation id unknown) | M | 2 |
| Clock | `grafana-clock-panel` (P1) | clock and countdown | none | NONE | | 1 |
| Dynamic text, Business Text | `marcusolsson-dynamictext-panel` (P1) | Handlebars/HTML over query results | `viz.markdown` for static text only | NONE for data-driven | | 0 |
| Business Variable | `volkovlabs-variable-panel` (P1) | variable selector as a panel | dashboard variable bar | DIFFERENT | S | 0 |
| Infinity | `yesoreyeram-infinity-datasource` (P1, a data source, not a panel) | JSON, CSV, XML, GraphQL over HTTP | see 6.3 | NONE | | 0 |

Corpus panel mix after migration (MEASURED): time series 64% (1,236 + 1,100 of 3,633), stat 22% (478 + 316), table 4% (115 + 38), text 3.4%, gauge 2.0%, heatmap 1.2%, pie 1.2% (38 + 5), bar gauge 1.0%, logs 0.3%, alert list 0.2%. 86% of the panels are time series or stat, which is where every conversion has to be right before any exotic panel is worth building.

Dashboard-level New Relic limits that affect layout (N2, T3): 12 columns, widget width 1 to 12, height 1 to 32 (provider: at least 1), 150 widgets per page, 25 pages, 20 queries per widget, widget title 255 characters; the widget title may contain a variable value (N5). Grafana's 24-column grid and 30-pixel row unit convert as column = x // 2 + 1 and width = round(w / 2) (base note 4). Row height in pixels for New Relic is not in the pages read (UNVERIFIED). A Grafana dashboard with 300 panels has to be split into pages of at most 150; row panels can mark the split.

## 6. (D) Query-level features

### 6.1 Mixed, Dashboard and expression queries

| Grafana | JSON | New Relic | verdict | note and corpus |
|---|---|---|---|---|
| Mixed data source | panel `datasource: {uid: "-- Mixed --"}`, each target with its own `datasource` (G14); queries run in parallel and failures are per query | one widget with several `nrql_query` blocks, each with an `account_id`; at most 20 (T1, N2) | APPROXIMATE | convertible when every visible target converts. 127 panels (3.5%) in the corpus use Mixed |
| Dashboard data source | `datasource: {type: datasource, uid: "-- Dashboard --"}`, target `{panelId, withTransforms}` reuses another panel's result (G14); repeats reference the original row's panel (G10) | copy the source panel's NRQL (each widget runs its own query, no sharing); apply this panel's transformations | APPROXIMATE | only if the source panel converts. 11 panels (0.3%) |
| expression queries | target `{datasource: {type: "__expr__"}, type: math|reduce|resample|sql|classic_conditions|threshold, expression, reducer, settings, refId}` (G8). Math operators and functions `abs`, `ceil`, `floor`, `is_inf`, `is_nan`, `is_null`, `is_number`, `log`, `round`, `inf`, `infn`, `nan`, `null`; reduce functions count, mean, min, max, sum, last; modes strict, drop non-numeric, replace non-numeric; resample | `math` over aggregates of the same event type and facet set -> arithmetic in `SELECT` (N22); `reduce` -> aggregator in a nested query (N9); `resample` -> `TIMESERIES n`; `sql` and `classic_conditions` -> none | APPROXIMATE for math and reduce | the engine requires identical timestamps across queries (G8); NRQL arithmetic requires one event type. 0 expression targets in the corpus (exports from grafana.com predate or avoid them); base note: 39 in the v2 devenv files |
| hidden queries | `hide: true` on a target | omit from `nrql_query[]` unless an expression uses it | EXACT | 82 targets |
| query reuse inside a panel (`$A` in `$B`) | | none (no query chaining; the Dashboard source also cannot chain, G14) | NONE | |

### 6.2 Query options and per-target flags

| Grafana | JSON | New Relic | verdict | note |
|---|---|---|---|---|
| Max data points | panel `maxDataPoints` (478 panels), v2 `queryOptions.maxDataPoints`; default is the panel width in pixels (G7) | `TIMESERIES AUTO` or `MAX`; `MAX` gives the maximum number of buckets for the window, at most 366 (N1, N19) | APPROXIMATE | Grafana's value changes with panel width and with calculated reducers (G7); New Relic's does not |
| Min interval, Interval | target/panel `interval` (221 panels), `intervalFactor` (2,668 targets), Prometheus `step` (1,581 targets) | fixed `TIMESERIES n minutes` or `AUTO` | APPROXIMATE | the base note 5.4 rule |
| Relative time, Time shift | `timeFrom`, `timeShift`, `hideTimeOverride` | see 4.9 | | |
| Cache timeout, query caching TTL | `cacheTimeout`, `queryCachingTTL` | none | NONE | |
| instant vs range | target `instant`, `range` (881 and 1,276 targets); `instant: true` returns one sample per series (the Prometheus instant query) | a query without `TIMESERIES` returns one value per facet over the window; the value is an aggregate over the window, not the sample at the end of the range | APPROXIMATE | used for stat, table and bar gauge panels; `instant: true` with `range: true` makes two frames (table and series) |
| format as | target `format` `time_series` (2,527), `table` (410), `heatmap` (25) | `time_series` -> `TIMESERIES`; `table` -> no `TIMESERIES`, `viz.table` with `FACET`; `heatmap` -> `histogram()` with `FACET` | APPROXIMATE | |
| exemplars | target `exemplar: true` (636 targets) | none | NONE | trace links from points; New Relic's equivalent (distributed tracing context on metrics) is not described in the pages read (UNVERIFIED) |
| legend format | `legendFormat` (3,677 targets) | `AS` or facet value (base note 5.5) | APPROXIMATE | |
| editor mode | `editorMode` `builder` or `code` | irrelevant | | the `expr` is what counts |
| `queryType`, `maxLines`, `direction` (Loki) | | `LIMIT` | APPROXIMATE | |

### 6.3 Data source types beyond Prometheus

Resolved data source type of the corpus targets (MEASURED, placeholders such as `${DS_PROMETHEUS}` resolved through `__inputs` and datasource variables): Prometheus 5,069 of 5,912 targets (86%), CloudWatch 421 (7%; includes 54 on a `${DS_CLOUDWATCH:...}` form), no data source given 129, unresolved variable 90, Loki 79, grafanacloud-cardinality 15, Dashboard 11, Postgres 6, MySQL 6. The corpus has no Elasticsearch, InfluxDB, Azure Monitor, Google Cloud Monitoring, Graphite, Tempo, Zabbix or Splunk target; popularity ranking on grafana.com is dominated by Prometheus exporters. The base note's sample (data-source-specific dashboards, REPO `grafana-dashboards.md` section 8) found 25% of panels on unsupported sources.

Core data sources shipped with Grafana (G14): AWS CloudWatch, Azure Monitor, Google Cloud Monitoring, Graphite, InfluxDB, OpenTSDB, Prometheus; Elasticsearch, Loki; Jaeger, Tempo, Zipkin; Pyroscope; Microsoft SQL Server, MySQL, PostgreSQL. The repository directory `public/app/plugins/datasource` lists only alertmanager, azuremonitor, dashboard, grafana-testdata-datasource, grafana, graphite and mixed in `main` (S7); the other core sources have moved out of that tree, so the plugin type ids come from the target JSON of a dashboard, not from that directory.

| source | target keys | New Relic data equivalent | what the converter can do | verdict | effort |
|---|---|---|---|---|---|
| Prometheus | `expr`, `legendFormat`, `format`, `instant`, `range`, `step`, `interval`, `exemplar`, `hide` | `Metric` events by `metricName` through remote write, the Prometheus agent or OpenMetrics integration (N13) | the PromQL rule set of the base note 5.2; New Relic's own PromQL translator documents the supported functions (N13) | APPROXIMATE | L (exists in part) |
| Loki | `expr` (LogQL), `queryType`, `maxLines` | `Log` events: `FROM Log SELECT count(*) WHERE message LIKE '...' FACET ...`; `aparse()`, `jparse()`, `capture()` for parsers | LogQL stream selectors and line filters to `WHERE`; metric queries (`rate`, `count_over_time`) to `count(*)` with `TIMESERIES`; pipelines (`logfmt`, `json`, `| label_format`) partly | APPROXIMATE | M |
| CloudWatch | `namespace`, `metricName`, `dimensions{}`, `statistic`, `period`, `region`, `expression`, `id`, `metricQueryType`, `matchExact`, `queryMode` (Logs) | Metric Streams: `aws.<service>.<Metric>` dimensions `aws.<service>.<Dimension>` with `collector.name = 'cloudwatch-metric-streams'` (N21; REPO `cloudwatch_map.json`, `docs/research/metric-names-verified.md`) | adapter to the CloudWatch dashboard path (base note 5.6); metric math (`expression`) and Logs Insights stay manual | APPROXIMATE | M |
| Azure Monitor | `queryType`, `azureMonitor{resourceUri, metricNamespace, metricName, aggregation, dimensionFilters, timeGrain}`, `azureLogAnalytics{query}` (KQL), `azureResourceGraph` | `azure.<resource type lower-cased, / to .>.<MetricName without spaces>` e.g. `azure.compute.virtualmachines.PercentageCPU` (N21); KQL has no counterpart | metrics only; no source translator exists in the repository | APPROXIMATE for metrics, NONE for KQL | M |
| Google Cloud Monitoring | `metricType`, `filters[]`, `groupBys[]`, `perSeriesAligner`, `crossSeriesReducer`, `alignmentPeriod` | `gcp.<service>.<metric path>` (REPO `docs/ir-spec.md`, `gcp-metric-names-verified.md`; N21 lists the names per service) | adapter to the GCP dashboard path; 5-minute polling raises the period | APPROXIMATE | S to M |
| Elasticsearch, OpenSearch | `query` (Lucene), `metrics[{type, field}]`, `bucketAggs[{type: date_histogram|terms|filters, field, settings}]`, `timeField`, `alias` | `Log` or custom events if the data is also sent to New Relic; no Elasticsearch index concept | structural mapping `date_histogram` to `TIMESERIES`, `terms` to `FACET`, `avg`/`sum`/`count` metrics to aggregators; Lucene to `WHERE` for simple terms | NONE for the data, APPROXIMATE for the shape | L |
| InfluxDB (InfluxQL builder) | `measurement`, `select[[{type, params}]]`, `groupBy[{type: time|tag|fill}]`, `tags[]`, `policy`, `resultFormat`, `alias` | custom events or `Metric` if the data is re-ingested (ingest path UNVERIFIED) | `SELECT mean("v") FROM "m" WHERE "t" = 'x' GROUP BY time($__interval), "tag"` maps to `SELECT average(v) FROM m WHERE t = 'x' FACET tag TIMESERIES AUTO` | APPROXIMATE | M |
| InfluxDB Flux | `query` (Flux script) | none | | NONE | |
| MySQL, PostgreSQL, MSSQL | `rawSql`, `format` (`time_series` or `table`), `table`, `timeColumn`, `metricColumn`, macros `$__timeFilter`, `$__timeGroup`, `$__unixEpochFilter` | NRQL has no SQL; New Relic infrastructure integrations for these databases produce their own events (names UNVERIFIED) | none for arbitrary SQL; for database-health panels re-map to the integration's attributes by hand | NONE | L |
| Graphite | `target` (function chain), `textEditor` | metrics in `Metric` only if ingested (UNVERIFIED) | none | NONE | |
| Tempo, Jaeger, Zipkin | `queryType` (`traceql`, `nativeSearch`, `serviceMap`), `query`, `filters`, `limit` | spans as `Span` events, traces in the distributed tracing UI | none; TraceQL has no NRQL translation known | NONE | |
| Pyroscope | profile queries | none | | NONE | |
| Zabbix (plugin) | `group`, `host`, `item`, `functions[]`, `mode` | none | | NONE | |
| Splunk (Enterprise plugin) | SPL `rawQuery` | none | | NONE | |
| Datadog (Enterprise plugin) | metric query strings | New Relic publishes a Datadog dashboard migration tutorial (`tutorial-dd-migration/migrating-dashboards`, DOCS path seen, not read); a Datadog source in this tool would own this | route to the Datadog converter | NONE here | |
| JSON API, Infinity | `type` (`json`, `csv`, `xml`, `graphql`), `url`, `parser`, `root_selector`, `columns[]` | the URL is the data; no NRDB equivalent | | NONE | |
| New Relic (Enterprise plugin, if used) | NRQL string | the same NRQL | copy the NRQL, check it against `check_raw_nrql` (id and existence of the plugin UNVERIFIED) | EXACT | S |
| TestData, `-- Grafana --`, Alertmanager | scenario ids | none | report and skip | NONE | |

Cross-account note: a New Relic widget can query up to 30 accounts at once (N2, N22); a Grafana Mixed panel that mixes several Prometheus instances is therefore expressible only if all of them stream into the accounts named in `account_id`.

## 7. (E) Dashboards as code

What the importer finds on disk or in a repository, and how to read each form. Every form below ends as the same object: one dashboard model (classic, v1 resource or v2 resource, base note 2.1). Rule: read rendered artifacts only; never execute a user's Jsonnet, Helm chart, Go or TypeScript program, and never run `terraform` with their code. The tool should print the render command and ask the user to supply the output.

| form | how to detect | how to read | evidence | verdict |
|---|---|---|---|---|
| plain JSON export or API response | top level `panels` or `rows`, or an `apiVersion` + `kind: Dashboard` envelope, or a `dashboard` wrapper | the base note rules | G16 shows the v1 envelope `"apiVersion": "dashboard.grafana.app/v1"` | EXACT |
| exported "for sharing externally" | `__inputs[]`, `__requires[]`, placeholders `${DS_PROMETHEUS}` | substitute each placeholder by the `pluginId` of its `__inputs` entry to learn the data source type; 169 of 180 corpus files are in this form (MEASURED) | CODE `export_inputs.go` (base note) | EXACT |
| provisioning | YAML with `apiVersion: 1` and `providers:` list (`name`, `orgId`, `folder`, `folderUid`, `type: file`, `disableDeletion`, `updateIntervalSeconds`, `allowUiUpdates`, `options.path`, `options.foldersFromFilesStructure`); the dashboards are the JSON files under `options.path`, folder names from the directory names when `foldersFromFilesStructure` is true | follow `path` relative to the YAML; folder = `folder` or directory name | G16 | EXACT |
| ConfigMap for the sidecar | `kind: ConfigMap` with a label whose key is the chart's `sidecar.dashboards.label` (default `grafana_dashboard`, value `"1"` in kube-prometheus-stack) and `data: {name.json: |- ...}` | parse each `data` value as JSON; optional annotation for the folder | CODE `kube-prometheus-stack/values.yaml` lines 1581 to 1582, `apiserver.yaml` | EXACT |
| Helm chart templates | `.tpl`/`templates/*.yaml` with `{{- ... }}` and JSON inside Go-template backtick escapes (the real `apiserver.yaml` in kube-prometheus-stack starts with `{{- /* Generated from ... */ -}}` and builds `{{`{"editable":`}}{{ .Values.grafana.defaultDashboardsEditable }}{{`,...`}}`) | not parseable; require `helm template` output | CODE `apiserver.yaml` | NONE without rendering |
| Grafana Operator | `apiVersion: grafana.integreatly.org/v1beta1`, `kind: GrafanaDashboard`; `spec` with exactly one source among `json`, `gzipJson` (bytes, base64), `url` (+ `urlAuthorization`), `jsonnet`, `jsonnetLib`, `configMapRef`, `grafanaCom{id, revision}`; plus `folder`, `folderUID`, `folderRef`, `uid`, `datasources[]`, `plugins[]`, `instanceSelector`, `resyncPeriod`, `allowCrossNamespaceImport`, `publicSharing` | `json`: parse; `gzipJson`: gunzip with a size cap; `configMapRef`: look up the ConfigMap in the same input set; `url`, `grafanaCom`: do not fetch, report; `jsonnet*`: render first | CODE grafana-operator `api/v1beta1/content.go`, `grafanadashboard_types.go` (the `datasources[]` mapping, `instanceSelector` and `resyncPeriod` names are from memory, UNVERIFIED) | EXACT for `json`, NONE otherwise |
| Terraform | `resource "grafana_dashboard"` with `config_json` (string: `jsonencode({...})`, `file("x.json")`, `templatefile(...)`), `folder`, `overwrite`, `message`; `grafana_library_panel`, `grafana_folder`, `grafana_dashboard_permission` | parse HCL (python-hcl2 style) and evaluate only `jsonencode` of literals and `file()` of a relative path; refuse `templatefile()` and interpolations | CODE terraform-provider-grafana `docs/resources/dashboard.md` | APPROXIMATE |
| Git Sync | dashboard files in a GitHub repository linked to Grafana (G15) | same as plain JSON; resource YAML/JSON with the Kubernetes envelope | G15 | EXACT |
| Jsonnet / Grafonnet / mixins | `*.jsonnet`, `*.libsonnet`, `jsonnetfile.json`, `import 'github.com/grafana/grafonnet/...'`; mixin repositories with `dashboards.libsonnet` | not readable; render with `jsonnet -J vendor file.jsonnet` (go-jsonnet) or `mixtool` (rendering tools not verified here, UNVERIFIED) | CODE grafonnet README (`jb install`, `jsonnet -J vendor dashboard.jsonnet`); Grafonnet is marked experimental and "not meant to be used in production" in its README | NONE without rendering |
| Foundation SDK (Go, TypeScript, Python, Java, PHP) | `grafana-foundation-sdk` imports, `NewDashboardBuilder` | run the program and capture the JSON it prints (`json.MarshalIndent(dashboard)` in the Go example) | G18, CODE foundation SDK README | NONE without rendering |
| `gcx` / Grafana CLI, Grizzly | `gcx` pushes resources; Grizzly has been removed (G15) | their resource files are the Kubernetes-style envelopes | G15 | EXACT after unwrap |
| Flux / Argo CD | a `Kustomization` / `HelmRelease` / Argo `Application` pointing at a path with the manifests above | resolve the path in the checked-out repository, run no controller; render `kustomize build` / `helm template` output | UNVERIFIED (no Flux or Argo documentation read) | depends on the artifacts |

New Relic equivalents as outputs (T1, T3, N17, N23).

| output | what it is | notes |
|---|---|---|
| Terraform `newrelic_one_dashboard` | widget blocks as in section 2 | currently emitted by the repository (REPO `emit/dashboard.py`) |
| Terraform `newrelic_one_dashboard_json` | a JSON string in the NerdGraph dashboard shape | exists in the provider (T3 file `resource_newrelic_one_dashboard_json.go`); would let the importer carry widgets the block form lacks, if their ids were known |
| Terraform `newrelic_one_dashboard_raw` | per-widget `rawConfiguration` | exists (T3 `resource_newrelic_one_dashboard_raw.go`) |
| NerdGraph `dashboardCreate` / `dashboardUpdate` | GraphQL; variables, pages, widgets with `rawConfiguration`; limits in N2 | repository's NerdGraph emitter; the field names for widget options are UNVERIFIED (`docs/ir-spec.md`) |
| UI "Import dashboard" | paste the JSON copied from "Download dashboard > JSON" (N17) | manual path for a person |
| `newrelic` CLI | `newrelic nerdgraph` runs GraphQL; there is no dashboard-specific command in the list (N23) | use as transport only |
| `nr1` CLI | Nerdpack tooling: `nr1 catalog`, `common`, `config`, `nerdpack`, `subscription` (N23 file names) | no dashboard commands in the pages read; not an equivalent |
| Guard rails | `permissions: private` default; resources disabled where applicable | repository rule |

Detection order for a folder scan: (1) `*.json` with dashboard markers; (2) YAML documents (split `---`): `ConfigMap` with the dashboard label, `GrafanaDashboard`, K8s envelope, provisioning file; (3) `*.tf` with `grafana_dashboard`; (4) report templates and generators (`*.jsonnet`, `*.libsonnet`, `Chart.yaml` with `templates/`, `*.go`/`*.ts` importing the Foundation SDK) as "found, not read, render first". Counting how many of each form an estate has is the first thing to ask the owner: it decides whether the importer needs an input mode for rendered output of Helm or Jsonnet.


## 8. (F) Summary matrix

One row per convertible unit of work. Verdicts are the ones argued in sections 3 to 7. "Freq" is MEASURED over the 180-dashboard corpus (3,633 panels, section 10) unless it says "n/a" (not measurable in classic exports) or "base" (base note sample of 69 dashboards, REPO `grafana-dashboards.md` section 8). Percentages of panels use 3,633, of dashboards use 180. Effort is S under a day, M a few days, L a week or more; "-" where there is nothing to build (the verdict is NONE and the action is to report).

| # | feature | Grafana JSON path | New Relic equivalent | verdict | effort | freq |
|---|---|---|---|---|---|---|
| T1 | transformation `organize` (rename, hide, order) | `panels[].transformations[].id = organize` | `AS`, select list | APPROXIMATE | S | 97 uses |
| T2 | `renameByRegex` | `transformations[].options.regex, renamePattern` | `capture()` in `FACET`, or `AS` for static names | APPROXIMATE | S | 46 uses |
| T3 | `filterFieldsByName` | `options.include, exclude` | select list | EXACT | S | 21 uses |
| T4 | `filterByRefId` | `options.include, exclude` | omit queries | EXACT | S | 0 |
| T5 | `filterByValue` | `options.filters[], type, match` | nested `FROM (...) WHERE` | APPROXIMATE | M | 7 uses |
| T6 | `reduce` | `options.reducers[], mode` | aggregator without `TIMESERIES`, nested aggregation | APPROXIMATE | M | 4 uses |
| T7 | `calculateField` binary and unary | `options.mode = binary / unary / reduceRow` | arithmetic in `SELECT` | APPROXIMATE | M | 23 uses (all modes) |
| T8 | `calculateField` cumulative, index, window stddev, centred window | `options.mode = cumulativeFunctions / index / windowFunctions` | none | NONE | - | within the 23 |
| T9 | `groupBy` | `options.fields{}` | `FACET` + aggregators | APPROXIMATE | M | 9 uses |
| T10 | `sortBy` | `options.sort[]` | `initial_sorting`, `ORDER BY` | EXACT | S | 13 uses |
| T11 | `limit` | `options.limitField` | `LIMIT` | EXACT | S | 5 uses |
| T12 | `merge` | `transformations[].id = merge` | several queries in one widget, `FROM A, B` | APPROXIMATE | M | 22 uses |
| T13 | `joinByField` / `seriesToColumns` | `options.byField, mode` | multi-select, `filter()`, `JOIN` | APPROXIMATE | M | 20 uses |
| T14 | `seriesToRows` | `transformations[].id` | none for charts | DIFFERENT | - | 3 uses |
| T15 | `labelsToFields` | `options.mode, keepLabels, valueLabel` | `FACET a, b` | APPROXIMATE | S | 4 uses |
| T16 | `extractFields` | `options.source, format, jsonPaths` | `jparse()`, `aparse()`, `capture()` | APPROXIMATE | M | 5 uses |
| T17 | `convertFieldType`, `formatTime`, `formatString` | `options.conversions[] / outputFormat` | `numeric()`, `string()`, `data_format`, `upper()` | APPROXIMATE | S | 3 uses |
| T18 | `partitionByValues` | `options.fields, naming` | `FACET` | APPROXIMATE | S | 1 use |
| T19 | `prepareTimeSeries` | `options.format` | none needed | EXACT | S | 1 use |
| T20 | `histogram` | `options.bucketSize, bucketCount` | `histogram(attr, width, buckets)` | APPROXIMATE | M | 0 |
| T21 | `heatmap` (calculate) | `options.xBuckets, yBuckets` | `viz.heatmap` over `histogram() FACET` | DIFFERENT | M | 0 |
| T22 | `regression`, `smoothing` | `options.modelType` / `resolution` | `predictLinear()`, `SLIDE BY` | APPROXIMATE | S | 0 |
| T23 | `configFromData` | `options.configRefId, mappings[]` | none | NONE | - | 4 uses |
| T24 | `rowsToFields`, `concatenate`, `groupingToMatrix`, `transpose`, `groupToNestedTable`, `timeSeriesTable`, `spatial`, `fieldLookup` | `transformations[].id` | none | NONE | - | 0 |
| B1 | override by name or query for colour, unit, display name, known series | `fieldConfig.overrides[].matcher byName / byFrameRefID` | `colors.series_overrides`, `units.series_overrides`, `AS` | APPROXIMATE | M | 14.4% of panels have overrides; 39% of rules are `byName` |
| B2 | override by regex over dynamic series | `overrides[].matcher byRegexp` | none (series names unknown at conversion) | NONE | - | 54% of override rules |
| B3 | override by value or type | `matcher byValue / byType` | none | NONE | - | 46 rules (3%) |
| B4 | per-series styling overrides | `properties custom.fillOpacity, lineWidth, lineStyle, hideFrom, stacking, transform` | none | NONE | - | about 430 of 2,300 properties |
| B5 | table column overrides (width, align, cell type) | `properties custom.width, align, cellOptions, displayMode` | none; thresholds by column | NONE | - | about 280 properties |
| B6 | right-axis override | `properties custom.axisPlacement = right` | `y_axis_right_series` | APPROXIMATE | S | 67 properties |
| B7 | value mapping `special` (null, NaN, empty) | `defaults.mappings[].type = special` | none | NONE | - | 84 mappings |
| B8 | value mapping `value` or `range` on categorical attributes | `mappings[].type = value / range` | `FACET CASES`, `if()` | DIFFERENT | M | 33 mappings (+25 legacy numeric) |
| B9 | value mapping on numeric stat or gauge | same | none | NONE | - | 320 panels with legacy `valueMaps` |
| B10 | thresholds absolute | `defaults.thresholds.steps[]` | `critical`/`warning`, line and table `threshold` | APPROXIMATE | S | 1,577 blocks |
| B11 | thresholds percentage | `thresholds.mode = percentage` | none (absolute only if min and max are set) | NONE | - | 37 blocks |
| B12 | colour mode palette-classic, fixed | `defaults.color.mode` | default palette, `colors.color` | EXACT | S | 1,075 and 140 panels |
| B13 | colour mode continuous, shades, other palettes | `color.mode` | none | NONE | - | 38 panels |
| B14 | thresholds style on time series | `custom.thresholdsStyle.mode` | line `threshold` | APPROXIMATE | S | 56 |
| B15 | unit with a direct New Relic name | `defaults.unit` (`percentunit`, `decbytes`, `Bps`, `bps`, `ms`, `s`, `reqps`) | `units.unit` | EXACT | S | 54% of panels carry a unit; these 7 ids are 466 of 1,980 unit-bearing panels (24%), `none` another 366 |
| B16 | percent, IEC bytes, short, ops, other rates | `unit` (`percent`, `bytes`, `short`, `ops`, `pps`) | `PERCENTAGE` with `/100`, `BYTES`, `COUNT` | APPROXIMATE | S | 950 of 1,980 unit-bearing panels (48%): `short` 580, `bytes` 167, `percent` 126, `ops` 49, `pps` 28 |
| B17 | currency, length, temperature and other categories, booleans | `unit` | none | NONE | - | 41 USD plus 6 boolean panels |
| B18 | decimals | `defaults.decimals` | `data_format.precision` | APPROXIMATE | S | 325 panels (9%) |
| B19 | min and max | `defaults.min, max` | `y_axis_left_min/max` | EXACT | S | min on 514 panels (14%), max on 92 |
| B20 | soft min and max | `custom.axisSoftMin/Max` | `y_axis_left_zero` (semantics unverified) | APPROXIMATE | S | 57 time series |
| B21 | `noValue` text | `defaults.noValue` | none | NONE | - | 101 panels |
| B22 | null handling | `custom.spanNulls`, `nullPointMode` | `null_values.null_value` | APPROXIMATE | S | 143 time series; 1,100 graph panels |
| B23 | stacking normal | `custom.stacking.mode`, `stack` | `viz.area` / `viz.stacked-bar` (behaviour unverified) | APPROXIMATE | S | 155 time series, 183 graph |
| B24 | stacking percent | `stacking.mode = percent` | none | NONE | - | 3 graph |
| B25 | fill and area | `custom.fillOpacity`, `fill` | `viz.area` vs `viz.line` | APPROXIMATE | S | 803 time series, 958 graph |
| B26 | gradient fill | `custom.gradientMode` | `chart_styles.gradient` | APPROXIMATE | S | 256 time series |
| B27 | line interpolation | `custom.lineInterpolation`, `steppedLine` | `chart_styles.line_interpolation` | EXACT | S | 177 time series |
| B28 | bars or points draw style | `custom.drawStyle`, `bars`, `points` | none | NONE | - | 56 time series, 52 graph |
| B29 | line width, dash, point size | `custom.lineWidth, lineStyle, pointSize, showPoints` | none | NONE | - | n/a (defaults present on all) |
| B30 | axis label, log scale, centred zero, width | `custom.axisLabel, scaleDistribution, axisCenteredZero` | none | NONE | - | 80 labels, 3 log |
| B31 | legend visibility | `options.legend.showLegend, displayMode`, `legend.show` | `legend_enabled` | EXACT | S | 12 hidden |
| B32 | legend placement | `options.legend.placement` | none | NONE | - | 271 right |
| B33 | legend values (calcs) | `options.legend.calcs`, `legend.values` | companion table widget | DIFFERENT | M | 778 panels |
| B34 | tooltip mode | `options.tooltip.mode`, `tooltip.shared` | `tooltip.mode` | EXACT | S | 880 multi, 254 single |
| B35 | shared crosshair | `dashboard.graphTooltip` | none | NONE | - | 53 dashboards (29%) |
| B36 | static panel link | `panels[].links[]` | widget `link`, `billboard_settings.link` | EXACT | S | 49 panels (1.3%) |
| B37 | templated data or field link | `defaults.links[]` with `${__value.raw}` etc. | none | NONE | - | 63 panels; 34 `__url_time_range` uses |
| B38 | drilldown by facet | field links to another dashboard | `filter_current_dashboard`, `linked_entity_guids` | DIFFERENT | M | n/a |
| B39 | dashboard link of type link | `dashboard.links[type=link]` | markdown widget | DIFFERENT | S | 37 dashboards (21%) |
| B40 | dashboard link of type dashboards (by tag) | `links[type=dashboards]` | none | NONE | - | 29 links |
| B41 | built-in annotations | `annotations.list[builtIn=1]` | none | NONE | - | 152 |
| B42 | annotation queries | `annotations.list[] with datasource + expr` | change tracking markers (needs a producer and an entity GUID) | DIFFERENT | L | 26 queries |
| B43 | default time range | `dashboard.time` | `SINCE` in each widget query | APPROXIMATE | S | 180 of 180 |
| B44 | panel relative time | `panels[].timeFrom` | `SINCE` + `ignore_time_range` | EXACT | S | 15 panels |
| B45 | panel time shift | `panels[].timeShift` | `COMPARE WITH` or shifted `SINCE ... UNTIL` | APPROXIMATE | S | 0 |
| B46 | hide time info | `panels[].hideTimeOverride` | none | NONE | - | 14 panels |
| B47 | refresh interval | `dashboard.refresh`, `timepicker.refresh_intervals` | widget `refresh_rate` | APPROXIMATE | S | 93 dashboards (52%) |
| B48 | time zone, fiscal year, week start, now delay | `dashboard.timezone, fiscalYearStartMonth, weekStart`, `timepicker.nowDelay` | none | NONE | - | timezone set on 82 dashboards |
| B49 | max data points, interval | `panels[].maxDataPoints`, `interval` | `TIMESERIES AUTO`/`MAX`/fixed | APPROXIMATE | S | 478 and 221 panels |
| B50 | repeat panel | `panels[].repeat, repeatDirection, maxPerRow` | `FACET` on the variable label | APPROXIMATE | M | 43 panels, 9 dashboards |
| B51 | repeat row | `panels[type=row].repeat` | none | NONE | - | 27 rows, 20 dashboards (11%) |
| B52 | rows and collapsed rows | `panels[type=row].collapsed, panels[]` | markdown heading widget or page | DIFFERENT | S | 123 dashboards (68%) with rows; 42 collapsed |
| B53 | tabs | v2 `layout.kind = TabsLayout` | pages (25 max) | EXACT | S | n/a |
| B54 | conditional rendering | v2 `ConditionalRendering*` | none | NONE | - | n/a |
| B55 | grid position | `panels[].gridPos` | `layout column, row, width, height` | EXACT | S | all |
| V1 | query variable `label_values` | `templating.list[type=query].definition` | `nrql` variable `uniques()` | APPROXIMATE | M | 331 of 429 query variables |
| V2 | chained query variable | `query` referencing another variable | nested variable (plan-limited) | APPROXIMATE | M | 223 of 429 |
| V3 | `query_result()` and other query shapes | `definition` | none | NONE | - | 85 of 429 |
| V4 | variable `regex` | `variable.regex` | `WHERE attr RLIKE` | APPROXIMATE | S | 52 (8 with lookaround) |
| V5 | variable `refresh` | `variable.refresh` | `ignore_time_range` | APPROXIMATE | S | 241 on load, 167 on time change |
| V6 | variable `sort` | `variable.sort` | none | NONE | - | 252 of 429 query variables set a sort |
| V7 | custom variable | `type = custom` | `enum` | EXACT | S | 28 |
| V8 | textbox variable | `type = textbox` | `string` | EXACT | S | 8 |
| V9 | constant variable | `type = constant` | substitute | EXACT | S | 8 |
| V10 | datasource variable | `type = datasource` | none (resolve type, then drop) | NONE | - | 112 (18%) |
| V11 | interval variable | `type = interval, auto` | none; `TIMESERIES AUTO` | NONE | - | 35 |
| V12 | ad hoc filter | `type = adhoc` | two variables in `WHERE` | APPROXIMATE | M | 6 |
| V13 | group by variable | `type = groupby` | variable in `FACET` | EXACT | S | 0 (Grafana 13.1) |
| V14 | multi, include all, custom all value | `multi, includeAll, allValue` | `is_multi_selection`, `["*"]`, `excluded` | EXACT | S | 152, 219, 121 |
| V15 | hidden variable | `variable.hide` | variable bar hiding (not in provider) | APPROXIMATE | S | 97 (15%) |
| V16 | `$__interval`, `$__rate_interval`, `$__range` | query text | `TIMESERIES AUTO`, `rate()`, window | EXACT | S | 1,130 + 220 + 102 uses |
| V17 | `$__from`, `$__to`, `$__user`, `$__org`, `$__dashboard` | query or link text | none | NONE | - | 12 `__dashboard` |
| V18 | format `csv`, `singlequote`, `doublequote` | `${v:csv}` | `IN ({{v}})` | EXACT | S | rare |
| V19 | format `regex`, `pipe`, `glob` | `${v:regex}` | `IN` or `RLIKE` | APPROXIMATE | S | rare |
| V20 | format `lucene`, `sqlstring`, `json`, `queryparam` | `${v:lucene}` | none | NONE | - | 32 `queryparam` (links) |
| S1 | snapshots | `snapshot` | none | NONE | - | n/a |
| S2 | public dashboards | shared dashboard resources | live URL | DIFFERENT | S | n/a |
| S3 | reports | Enterprise reports | none | NONE | - | n/a |
| S4 | playlists | playlist resources | TV mode page cycling | DIFFERENT | M | n/a |
| P1 | time series, graph | `type = timeseries / graph` | `viz.line`, `viz.area` | APPROXIMATE | S | 64% of panels |
| P2 | stat, singlestat | `type = stat / singlestat` | `viz.billboard` | APPROXIMATE | S | 22% |
| P3 | gauge | `type = gauge` | billboard with thresholds, bullet | APPROXIMATE | S | 71 (2.0%) |
| P4 | bar gauge | `type = bargauge` | `viz.bullet`, `viz.bar` | APPROXIMATE | S | 36 (1.0%) |
| P5 | table, table-old | `type = table / table-old` | `viz.table` | APPROXIMATE | M | 153 (4.2%) |
| P6 | pie chart | `type = piechart / grafana-piechart-panel` | `viz.pie` | APPROXIMATE | S | 43 (1.2%) |
| P7 | bar chart | `type = barchart` | `viz.bar`, `viz.stacked-bar` | APPROXIMATE | S | 3 |
| P8 | histogram | `type = histogram` | `viz.histogram` | APPROXIMATE | M | 0 |
| P9 | heatmap | `type = heatmap` | `viz.heatmap` | DIFFERENT | M | 45 (1.2%) |
| P10 | state timeline, status history | `type = state-timeline / status-history` | none | NONE | - | 2 |
| P11 | text, markdown mode | `type = text, options.mode = markdown` | `viz.markdown` | EXACT | S | 103 of 122 |
| P12 | text, html or code mode | `options.mode = html / code` | none | NONE | - | 19 |
| P13 | logs, logs table | `type = logs / logstable` | `logger.log-table-widget` | APPROXIMATE | M | 12 |
| P14 | alert list, annotation list, news | `type = alertlist / annolist / news` | none | NONE | - | 9 |
| P15 | dashboard list | `type = dashlist` | markdown list | DIFFERENT | S | 0 |
| P16 | geomap, node graph, traces, flame graph, canvas, xy chart, candlestick, debug, live | `type = ...` | none | NONE | - | 4 |
| P17 | trend | `type = trend` | `viz.line` when x is time | APPROXIMATE | S | 0 |
| P18 | Polystat, Status Panel | plugin ids | billboard grid | DIFFERENT | M | 0 |
| P19 | Boom Table | plugin id (unverified) | table with thresholds | APPROXIMATE | M | 0 |
| P20 | Treemap plugin | `marcusolsson-treemap-panel` | treemap (UI only) | APPROXIMATE | M | 2 |
| P21 | Sankey, Plotly, Business Charts, Clock, Dynamic text | plugin ids | none | NONE | - | 1 |
| Q1 | Mixed data source | `panels[].datasource.uid = -- Mixed --` | several `nrql_query` blocks | APPROXIMATE | M | 127 panels (3.5%) |
| Q2 | Dashboard data source | `datasource.uid = -- Dashboard --` | copy NRQL | APPROXIMATE | S | 11 |
| Q3 | expressions math and reduce | `targets[].datasource.type = __expr__` | `SELECT` arithmetic, nested aggregation | APPROXIMATE | M | 0 |
| Q4 | expressions sql and classic conditions | `__expr__ type = sql` | none | NONE | - | 0 |
| Q5 | hidden queries | `targets[].hide` | omit | EXACT | S | 82 |
| Q6 | instant queries | `targets[].instant` | query without `TIMESERIES` | APPROXIMATE | S | 881 targets |
| Q7 | format as table or heatmap | `targets[].format` | table, `histogram()` | APPROXIMATE | S | 410 and 25 |
| Q8 | exemplars | `targets[].exemplar` | none | NONE | - | 636 |
| Q9 | Prometheus | `targets[].expr` | NRQL on `Metric` | APPROXIMATE | L | 86% of targets |
| Q10 | Loki | `expr` | NRQL on `Log` | APPROXIMATE | M | 79 targets |
| Q11 | CloudWatch | `namespace, metricName, dimensions` | `aws.*` metrics | APPROXIMATE | M | 421 targets (7%) |
| Q12 | Azure Monitor metrics | `azureMonitor{}` | `azure.*` metrics | APPROXIMATE | M | 0 (base: 300 targets in bundled dashboards) |
| Q13 | Google Cloud Monitoring | `metricType, filters` | `gcp.*` metrics | APPROXIMATE | S | 0 (base: 15) |
| Q14 | InfluxQL builder | `select[[]], groupBy[]` | `average() ... FACET ... TIMESERIES` | APPROXIMATE | M | 0 (base: 108) |
| Q15 | Elasticsearch / OpenSearch | `metrics[], bucketAggs[]` | `Log` or events if re-ingested | NONE | - | 0 (base: 80) |
| Q16 | Flux, SQL, Graphite, Zabbix, Splunk | raw query strings | none | NONE | - | 12 SQL targets (base: 162) |
| Q17 | traces and profiles (Tempo, Jaeger, Pyroscope) | `queryType traceql` | none | NONE | - | 0 (base: 9) |
| Q18 | Infinity and other HTTP sources | `type, url, parser` | none | NONE | - | 0 |
| E1 | classic, v1 and v2 JSON | file content | read | EXACT | S | all |
| E2 | `__inputs` placeholders | `__inputs[].pluginId` | resolve data source type | EXACT | S | 169 of 180 files (94%) |
| E3 | provisioning YAML | `providers[].options.path` | follow path | EXACT | S | n/a |
| E4 | sidecar ConfigMaps | label `grafana_dashboard` | parse `data` | EXACT | S | n/a |
| E5 | Grafana Operator `GrafanaDashboard` with `json` | `spec.json` | parse | EXACT | S | n/a |
| E6 | Terraform `grafana_dashboard` | `config_json` | HCL literal | APPROXIMATE | M | n/a |
| E7 | Helm templates, Jsonnet, Foundation SDK programs | source files | render first | NONE | - | n/a |

### 8.1 Counts

Counts over the matrix rows above (computed by a script over the verdict column of those rows only).

| verdict | rows | share |
|---|---|---|
| EXACT | 29 | 19% |
| APPROXIMATE | 61 | 41% |
| DIFFERENT | 13 | 9% |
| NONE | 46 | 31% |
| total | 149 | 100% |

By area:

| area | rows | EXACT | APPROXIMATE | DIFFERENT | NONE | buildable (EXACT or APPROXIMATE) |
|---|---|---|---|---|---|---|
| transformations (T) | 24 | 5 | 14 | 2 | 3 | 19 of 24 |
| field config, links, annotations, time, layout (B) | 55 | 10 | 16 | 6 | 23 | 26 of 55 |
| variables (V) | 20 | 7 | 7 | 0 | 6 | 14 of 20 |
| sharing (S) | 4 | 0 | 0 | 2 | 2 | 0 of 4 |
| panels (P) | 21 | 1 | 12 | 3 | 5 | 13 of 21 |
| queries and data sources (Q) | 18 | 1 | 11 | 0 | 6 | 12 of 18 |
| dashboards as code (E) | 7 | 5 | 1 | 0 | 1 | 6 of 7 |

Effort column: - 47, L 2, M 32, S 68 (S small, M medium, L large, - nothing to build).

Coverage meaning. Rows count features, not panels, so they say what can be built, not how much of a real estate converts. The panel-weighted view: of the corpus panels, 86% are time series or stat, both APPROXIMATE with the main losses being overrides (14.4%), mappings (12%), units that need scaling (about half of the unit-bearing 54%), and transformations (4.7%).

## 9. Prioritised build list

Ordered by (panels affected in the corpus) x (probability the converted panel is wrong or missing without it) / effort. "Gate" means the conversion must refuse and report rather than guess.

1. Resolve data sources through `__inputs` and datasource variables before judging a panel (E2, V10). 94% of corpus files use `__inputs`, 18% of variables are datasource variables, and the base note found 36 panels with an unresolved `${datasource}`. Effort S. Without it the planner counts a Prometheus panel as unresolved.
2. Unit mapping with scaling (B15, B16, B17). 54% of panels carry a unit; the `percent` -> `PERCENTAGE` and byte-family scaling are silent wrong-number bugs if skipped. Effort S plus one sandbox read-back to confirm the accepted `units.unit` strings. Gate: unit not in the 60-row table -> no unit, keep the number.
3. Variables model in the IR and emitter (V1, V2, V4, V5, V7, V8, V9, V14, V16, V18, V19). 95% of dashboards have variables; query `label_values`, custom, textbox, constant, multi/all with `excluded`, and rewriting `$__rate_interval`, `$__interval`, `$__range`. The IR already has `IRVariable` (REPO `dashboard_ir.py`); what is missing is the Grafana side (`variables.py` is 89 lines) and `{{var}}` insertion in the PromQL translator. Effort M. Gate: datasource, interval, query_result, `${v:lucene}` and friends -> manual with reason.
4. Overrides: convert `byName`/`byFrameRefID`/`byRegexp` over constant-alias series to `colors.series_overrides` and `units.series_overrides`; record the rest (B1 to B6). 14.4% of panels. Effort M. Gate: record the dropped override properties in the widget `reasons`, because they change what the panel shows.
5. Transformations that can be absorbed: `organize`, `renameByRegex` (static), `filterFieldsByName`, `sortBy`, `limit`, `merge` of same-shape queries, binary `calculateField` (T1 to T4, T10 to T12, T7). 77% of the 288 transformation uses are in this set; panels with these only convert. Effort M. Gate: every other id blocks the panel and names the id in the reason.
6. Thresholds and colours to `critical`/`warning` and line/table `threshold` with the colour-to-severity table (B10, B12, B14). 43% of panels have absolute thresholds. Effort S. Gate: percentage mode without min and max -> drop thresholds with a note.
7. Value mappings: detect and gate (B7 to B9). 12% of panels. Effort S for detect and downgrade; M for the `FACET CASES` rewrite (B8), which is rare. Do the detection first because it prevents confident wrong answers.
8. Layout and pagination: 150 widgets per page, 20 queries per widget, height up to 32, rows as markdown headings, tabs as pages (B52, B53, B55; section 5 limits). The IR currently caps `raw_series` at 50 and widgets at 300 per dashboard. Effort S. This is a correctness fix against the documented limits (N2).
9. Dashboard default time (`SINCE`), panel `timeFrom` with `ignore_time_range`, `refresh` to `refresh_rate` (B43, B44, B47). Needs the IR to allow a controlled `SINCE`. Effort S to M.
10. Legend values as a companion table, links as widget `link`, dashboard links as a markdown widget (B33, B36, B39). Effort M.
11. Panels: gauge (billboard fallback), bar gauge (bullet), pie, bar, heatmap and histogram (P3, P4, P6, P7, P8, P9). Together 5% of panels. Effort M. Needs the IR to add widget kinds (`viz.bullet`, `viz.bar`, `viz.heatmap`, `viz.histogram`, `viz.stacked-bar`) and confirm their `rawConfiguration` in the sandbox.
12. Data-source adapters: CloudWatch (7% of corpus targets, 56 panels in the base sample) first, then Google Cloud Monitoring, then Azure Monitor metrics and Loki (Q10 to Q13). Effort M each; CloudWatch and Google reuse existing translators.
13. Repeat panels as `FACET` (B50). 1.2% of panels. Effort M.
14. As-code intake: sidecar ConfigMaps, provisioning YAML, Grafana Operator `json`, Terraform literal `config_json` (E3 to E6). Effort S each; ask the owner first which forms exist.
15. Defer or decline: annotations (B42; needs a producer, effort L), Elasticsearch, SQL, Flux and other languages (Q15, Q16), plugin panels (P18 to P21), playlists, snapshots, reports.

Expected effect, stated as bounded estimates from the sections above, not as a promise: items 1 to 6 remove the main sources of silently wrong output for time series and stat panels (86% of panels); item 5 widens the share of panels allowed to convert (4.7% have transformations, 77% of their uses are absorbable). The base note's coverage tiers (22%, 39%, 47% by panel, 70% on Prometheus-heavy dashboards) are dominated by query translation (section 6), not by anything in this note.

## 10. Corpus and method

What. The 180 most downloaded dashboards of grafana.com, taken from the public catalogue API (`https://grafana.com/api/dashboards?orderBy=downloads&direction=desc&pageSize=60&page=1..3`, then `/api/dashboards/{id}/revisions/latest/download`), fetched with `curl` on 2026-11-09. All 180 parsed. They are the "export for sharing externally" form (`__inputs` on 169, `schemaVersion` mostly 14 to 42: 39 on 24 files, 38 on 19, 36 on 18, 16 on 15, 40 on 12). 3,633 non-row panels, 696 row panels, 5,912 targets. Panel types after counting legacy: `timeseries` 1,236, `graph` 1,100, `stat` 478, `singlestat` 316, `text` 122, `table` 115, `gauge` 71, `heatmap` 45, `table-old` 38, `piechart` 38, `bargauge` 36, `logs` 12, `alertlist` 9, `grafana-piechart-panel` 5, `nodeGraph` 3, `barchart` 3, `status-history` 2, `marcusolsson-treemap-panel` 2, `grafana-clock-panel` 1, `geomap` 1.

How. A Python script walked `panels[]` (including `panels[]` inside collapsed rows and legacy `rows[].panels[]`) and counted keys and values; a second script resolved target data sources through `__inputs` and datasource variables; others counted unit ids, override matchers, styling fields and variable properties. A feature is counted for a panel when it is present and non-empty (`mappings` non-empty, `transformations` non-empty, `repeat` set, and so on). Occurrences of `$__rate_interval` and the like are string counts over the raw files (a target and a panel title both count; `${var:fmt}` counts also matched unrelated colons, so only `queryparam`, `raw` and `text` are quoted).

Limits. (1) The ranking is by downloads, which favours Prometheus exporter dashboards (86% of targets). It under-represents SQL, Elasticsearch, Azure and private dashboards, where the base note's 69-dashboard sample (data-source-specific) is the better guide. (2) Most files are old: Angular `graph` and `singlestat` panels are 39% of panels and no file uses `AutoGridLayout`, tabs, or `GroupByVariable`; Grafana 12 and 13 features (v2 layouts, conditional rendering, switch variables) have a count of "n/a". (3) A private estate will have more overrides, mappings and transformations than public exports, because public authors avoid them for portability; treat the figures as a floor for those. (4) The corpus overlaps the base note's 69 dashboards in an unknown way; the two samples should not be pooled. (5) Counts of "uses" are not counts of panels where a panel has several.

## 11. Corrections to the base note and the IR

1. Base note 7.4 found no field for a default time range (marked UNVERIFIED). It can be carried as `SINCE` in each widget query, and the picker overrides it (N1 `sel-since`, `query-time-range`, N11). The IR forbids `SINCE` in raw queries on purpose; add a dashboard-level default instead.
2. Base note 4.3 says any dashboard with transformations should fail the widget except `organize`. Six ids cover 77% of the uses and can be absorbed (section 3).
3. IR limit `MAX_RAW_SERIES = 50` exceeds the documented 20 queries per widget (N2). IR `MAX_WIDGETS = 300` is not a New Relic number; the limits are 150 per page and 25 pages. IR height clamp 12 is stricter than 32 (N2); the provider validates only a minimum of 1 (T3).
4. The stacked bar widget id is `viz.stacked-bar` and the log table is `logger.log-table-widget` (T3); both were UNVERIFIED in the IR notes.
5. `viz.billboard` thresholds in the provider are two numbers (`critical`, `warning`), but the UI has five levels (N4); a richer billboard needs `rawConfiguration` through NerdGraph or `newrelic_one_dashboard_json`, whose field names are unconfirmed.

## 12. UNVERIFIED claims

Do not build on these without a check. Each has a sandbox read-back, a docs page or a code read that would settle it.

1. The strings New Relic accepts in `units.unit` for dashboard widgets. The chart library lists nine (charts) and seventeen (table cells) names (N20); the provider test uses `BYTES` and its docs example uses `ms` (T2, T3). One read-back of a hand-made dashboard settles it.
2. Whether `viz.area` stacks series; whether `viz.stacked-bar` has a time axis; whether `spanNulls` maps to `remove` (the line joins neighbouring points).
3. The ids of the gauge and treemap widgets (not in the provider, the NerdGraph widget page or any of the 7,384 docs files I scanned for `viz.gauge` and `viz.treemap`).
4. Whether column order of a New Relic table follows the select list.
5. Whether `if()` accepts aggregate results; whether `ORDER BY` after `FACET` can order by a column other than the first select term.
6. The mapping from Grafana colour names to New Relic severities (`success`, `warning`, `unavailable`, `severe`, `critical`) is my assumption.
7. `NrAiIssue` and the alert event names for an alert list widget (not found in the docs scanned).
8. Whether the NRQL query length limit is 4,096; the New Relic page I read does not state one.
9. That `y_axis_left_zero` is the right target for `axisSoftMin: 0`.
10. That the `steppedLine: true` legacy option migrates to `stepAfter`.
11. Plugin ids from memory: Boom Table (`yesoreyeram-boomtable-panel`), Sankey (`netsage-sankey-panel`), Plotly (`natel-plotly-panel`); alert list option names; `mixtool`; Grafana Operator fields `datasources[]`, `instanceSelector`, `resyncPeriod`; the `configFromData` mapping item keys; the existence and id of an Enterprise New Relic data source plugin.
12. The Grafana custom unit syntax (`suffix:`, `prefix:`, `time:`, `si:`).
13. That New Relic infrastructure integrations for MySQL, PostgreSQL and SQL Server produce events useful for SQL panels, and that Influx or Graphite data can be ingested; Flux and Argo CD repository layouts (no documentation read).
14. That the user's time zone is a profile setting in New Relic; that scheduled reports exist; that folders are not an entity concept.
15. Distributed-trace context on Prometheus exemplars (New Relic equivalent not found).
16. `nowDelay`, `liveNow` equivalents (none found; absence of evidence).
17. Row height in pixels of a New Relic widget row.
18. Whether a New Relic variable query returns values in a stable sorted order.
19. That `data_format type=duration` expects milliseconds.
20. The 7.8 million dashboard download claim and the 99.5% PromQL coverage figure are New Relic statements (N13), not measured here.

