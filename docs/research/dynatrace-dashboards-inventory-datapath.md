# Dynatrace: dashboards, SLOs, synthetic monitors, instrumentation inventory and data path

Research for the Dynatrace source of Conquest Importer. Written 2026-11-09 from public documentation,
this repository, and four real Dynatrace Platform dashboard documents saved at `/tmp/dt-dash/*.json`
(Dynatrace-authored templates from the owner's tenant, no secrets). No Dynatrace tenant and no New
Relic account was called. No `.env` was read.

Evidence marks on every claim:

- DOCS Dn / DOCS Nn: read in public Dynatrace or New Relic documentation (or the public New Relic
  Terraform provider repository) on 2026-11-09; the URL is in the Sources table at the end.
- LIVE-FILE name: read in one of the four dashboard files (`AWS` = `AWS_Cloud_Platform_Monitoring_-_AWS_Data.json`,
  `Azure` = `Azure_App_Service_Plan.json`, `Host` = `Host_vulnerability_findings_reduction.json`,
  `K8s` = `Kubernetes_monitoring_statistics.json`). Tile ids are the keys of `tiles`.
- REPO file: read in this repository.
- UNVERIFIED: from memory or inferred; not confirmed in a source. Do not treat as fact.

Terminology: the Dynatrace pages say "Latest Dynatrace" for the Grail/DQL platform and "Dynatrace
Classic" for the older UI and configuration APIs (DOCS D1, D5). Both exist in a migrating tenant.

## 1. Answers first

- The four real dashboards contain 93 tiles: 59 data tiles and 34 markdown tiles (LIVE-FILE, all four).
  Under a conservative rule set 10 of the 59 data tiles convert exactly, 22 convert only as a draft
  that a person must review, and 27 stay manual. All 34 markdown tiles convert to a markdown widget.
  The counting rules and a per-tile list are in section 2.9.
- Of the 10 exact conversions, none has a data source in New Relic. They read Dynatrace
  self-monitoring metrics (`dt.sfm.*`) or Grail security events. The tiles that do have a plausible
  New Relic counterpart are 11 Azure and AWS cloud-metric tiles, and all 11 are drafts (section 2.9).
  Dashboard conversion therefore depends on the data path (section 7) more than on the translator.
- Dashboard JSON is documented. A Platform dashboard is a document with four required properties
  (`version`, `variables`, `tiles`, `layouts`) and four tile types: markdown, data, code, slo (DOCS D1).
  The Document API stores content without inspecting it (DOCS D3), so the structure page (D1) and the
  real files are the schema.
- The importer's dashboard model (`src/importer/core/dashboard_ir.py`) cannot carry what Dynatrace
  tiles need: it has no table widget, no raw-NRQL series, no thresholds and no dashboard variables, and
  its series are `MetricSignal` objects (REPO). A Dynatrace dashboard translator needs a small IR
  extension first (section 2.10).
- New Relic limits that decide conversions: template variables are not accepted in `LIMIT`, `SINCE`,
  `TIMESERIES`, `COMPARE WITH`, `AS`, `OFFSET`, `SLIDE BY`, `UNTIL`, `WITH` (DOCS N3); a service level
  needs an existing entity `guid` and its rolling window is 1, 7 or 28 days only (DOCS N2); the
  dashboard provider has no honeycomb widget (DOCS N1, N23).
- Service levels can be generated for the common availability and latency SLOs once the service is
  instrumented with a New Relic agent, because `Transaction` and `TransactionError` are the documented
  valid and bad event sources (DOCS N17). Metric-only SLOs stay a plan (section 4).
- Synthetic monitors: single-request HTTP and single-URL browser monitors map to `SIMPLE` and
  `BROWSER` monitors; certificate-expiry rules map to a certificate-check monitor; multi-request HTTP,
  scripted pre/post processing, JavaScript and cookie events need a script a person finishes (section 5).
- The Entities API v2 carries a deprecation banner (Jun 2026) pointing to Smartscape on Grail
  (DOCS D19). Inventory should be read with DQL `smartscapeNodes` (scope `storage:smartscape:read`
  exists, DOCS D24; which scope the query endpoint actually checks is UNVERIFIED, section 6.2).
- No documented way sends OneAgent-collected data to New Relic. OneAgent's OTLP endpoint receives
  traces on localhost only (DOCS D29). The documented routes are: dual export from an
  OpenTelemetry Collector (DOCS D28, N11), OpenPipeline forwarding to cloud object storage
  (DOCS D31), pulling through the Metrics or Query APIs (DOCS D33, D23), and re-pointing cloud
  integrations at New Relic (DOCS N16, N13, N14). The converter needs a curated metric-name map file
  (section 7.3).

## 2. Platform dashboards (document type `dashboard`)

### 2.1 Where they live and how to list them

| Fact | Evidence |
|---|---|
| List: `GET https://<id>.apps.dynatrace.com/platform/document/v1/documents`, scope `document:documents:read`, `filter` field `type` (example `type in ('dashboard', 'notebook')`), `page-size` max 1000 (default 20), `page-key`, `add-fields` (`description`, `labels`, `shareInfo.isShared`, ...) | DOCS D4 |
| The default list returns metadata only; content is a separate call. The content path and response shape were not read | UNVERIFIED |
| The document store does not inspect content, so content can be schemaless; schema conformance is the writer's job | DOCS D3 |
| Documents are private to the owner unless shared; a list call sees own and shared documents. Reading everyone's dashboards needs `admin-access` and the extra scope `document:documents:admin` | DOCS D3, D4 |
| Classic Settings 2.0 schemas named `builtin:dashboards.*` are `general` (anonymous access, home dashboards), `presets` and `image-allowlist`. They do not hold dashboards | DOCS D7 |
| Dashboards also appear as documents of the Dashboards app; ready-made dashboards (the four files are Dynatrace-authored) are documents too | DOCS D2; LIVE-FILE (all four) |

A migration source should expect that most dashboards on a tenant are ready-made Dynatrace templates
(like the four) plus a few user dashboards; the importer should record `originAppId`, `owner` and
`isPrivate` from the list call (field names DOCS D4) so a report can separate the two.

### 2.2 Top-level structure

All four files have these keys (LIVE-FILE, all four): `version`, `variables`, `tiles`, `layouts`,
`importedWithCode`, `settings`, `annotations`.

| Key | Seen | Meaning | Evidence |
|---|---|---|---|
| `version` | `21` in all four | Schema version. "Use 21 for new dashboards" | DOCS D1; LIVE-FILE |
| `variables` | array, 2 to 7 per file | Dashboard variables (section 3) | DOCS D1; LIVE-FILE |
| `tiles` | object, tile id to tile | Ids are strings. Numeric strings in three files; the Azure file uses names (`kpi-cpu`, `chart-memory`) | DOCS D1; LIVE-FILE |
| `layouts` | object, same ids to `{x,y,w,h}` | 24-column grid | DOCS D1; LIVE-FILE |
| `settings` | `{}` in three, `{"defaultTimeframe":{"value":{"from":"now()-7d","to":"now()"},"enabled":true}}` in Host | Dashboard default timeframe | DOCS D1; LIVE-FILE Host |
| `annotations` | `[]` in all four | Optional | DOCS D1; LIVE-FILE |
| `importedWithCode` | `false` in all four | Not described in the structure page | LIVE-FILE; meaning UNVERIFIED |

Every tile id in `tiles` must have an entry in `layouts` (DOCS D1). Extra tile-level and document-level
keys the importer must tolerate are listed in section 2.4.

### 2.3 Tile types

DOCS D1 lists four: `markdown`, `data`, `code`, `slo`. The Dashboards app page names the user-facing
kinds as Explore, Query, Code and Markdown (DOCS D2); an Explore tile is stored as a `data` tile that
carries `queryConfig` (DOCS D1, LIVE-FILE K8s). The structure page also says a data tile with a
`davisCopilot` property is a Prompt tile (DOCS D1).

| Tile type | Count in the four files | Shape | Evidence |
|---|---|---|---|
| `markdown` | 34 (AWS 4, Azure 8, Host 15, K8s 7) | `{"type":"markdown","content":"..."}` and nothing else | LIVE-FILE |
| `data` | 59 (AWS 8, Azure 19, Host 22, K8s 10) | see 2.4 | LIVE-FILE |
| `code` | 0 | `{"type":"code","title","input":"<javascript>","visualization","visualizationSettings"}` | DOCS D1 |
| `slo` | 0 | `{"type":"slo","title","input":"<slo-id>"}` | DOCS D1 |

Markdown content in the four files: 4 tiles link into the Dynatrace UI with `/ui/...` intent or app
paths, 4 use `dt-url.net` short links, 2 are a single image from `dt-cdn.net`, and 4 are spacers (a
single space or `---`). The `/ui/` links do not resolve in New Relic and must be dropped or rewritten
(LIVE-FILE: Azure `header`, `footer`, `problems-header`; K8s 12).

### 2.4 The data tile

Keys seen on data tiles (LIVE-FILE, 59 tiles): `type`, `title`, `query`, `visualization`,
`visualizationSettings`, `querySettings`, `davis`, and sometimes `description`, `subType`,
`queryConfig`, `timeframe`, `segments`, `customLinkSettings`. DOCS D1 requires `title`, `query`,
`visualization`, `visualizationSettings`, `querySettings`; `description`, `timeframe` (an object, not a
string) and `segments` are optional.

| Key | What was seen | New Relic handling |
|---|---|---|
| `title` | present on all; empty string on 6 Host tiles | NR widget title is required (DOCS N1): use a generated title |
| `query` | DQL text, with `//` comments, variables as `$Name` | section 2.5 to 2.7 |
| `visualization` | `lineChart` 22, `table` 13, `singleValue` 19, `donutChart` 3, `categoricalBarChart` 2 | section 2.6 |
| `querySettings` | on all 59: `defaultScanLimitGbytes: 500`, `defaultSamplingRatio: 10`, `enableSampling: false`, `maxResultRecords` (1000 on 56, 50 on 2, 10 on 1), `maxResultMegaBytes` (1 on 40, 100 on 19) | none; drop. The record cap is the only part that changes what a user sees (`LIMIT` in NRQL) |
| `davis` | on all 59: `{"enabled": false, "davisVisualization": {"isAvailable": true}}` | none; drop |
| `subType` | `dql-builder-metrics` on 4 K8s tiles | Explore tile marker |
| `queryConfig` | on those 4: `{version: 20, subQueries:[{id, isEnabled, datatype:"metrics", metric:{key, aggregation}, by:[...], rate:"1m", convertToValue:"Avg", filter:"a = $V AND b != 2*"}], globalCommands:{limit, sort:{field,direction}}}` | a structured form of the same query. Use `query` (the compiled DQL) as the source of truth; `queryConfig` is a cross-check and gives the metric key and `by` fields without parsing |
| `timeframe` | `{tileTimeframe:{from:"now()-2h",to:"now()"}, tileTimeframeEnabled:false}` on 2 K8s tiles (both disabled) | ignore when disabled; when enabled, append `SINCE ... UNTIL ...` and set `ignore_time_range` (DOCS N1) |
| `segments` | `{tileSegments:[], tileSegmentsEnabled:false}` on 1 AWS tile | empty here. Segments are saved filters (DOCS D1 "tile-specific segment filter"); dashboard-level segments were not in any file |
| `customLinkSettings` | one custom link to a Dynatrace settings intent on K8s 15 | drop |

### 2.5 The DQL patterns seen

The first command of the 59 queries: `timeseries` 29, `fetch` 26 (22 `fetch security.events`, 4
`fetch dt.davis.*`), `smartscapeNodes` 4 (LIVE-FILE).

| DQL pattern (seen) | Example tile | NRQL equivalent | Verdict |
|---|---|---|---|
| `timeseries sum(m), by:{a,b}, filter:{...}` with a line chart | AWS 1; K8s 4, 5, 6; Azure charts | `SELECT sum(m) FROM Metric WHERE ... FACET a, b TIMESERIES AUTO`; `FACET` supports up to five attributes and the first aggregation chooses the facets (DOCS N4) | converts, once `m`, `a`, `b` are mapped to New Relic names |
| `sum(m, rate: 1m)` | K8s 1, 4, 5 | `rate(sum(m), 1 minute)` (DOCS N4, N1 example `rate(count(*), 1 minute)`) | converts |
| `value.A = avg(m, scalar: true)` with `sort value.A desc \| limit 10` | K8s 1, AWS 14 | `SELECT average(m) ... FACET ... LIMIT 10` (non-timeseries); a bar chart | converts |
| `filter: { matchesValue(f, $V) AND NOT matchesValue(status_code, "2*") }` | K8s 4, 5 | `f IN ({{V}}) AND status_code NOT LIKE '2%'`; `LIKE` exists (DOCS N4). Dynatrace wildcard and case rules for `matchesValue` were not checked | converts with a note (same caveat as REPO `src/importer/sources/dynatrace/dql.py` MEMBER_NOTE) |
| `filter(in(f, array($V)))` with multi-select variable | Azure, K8s | `f IN ({{V}})`; multi values need `IN` (DOCS N3) | converts |
| `($var == "*" or f == $var)` sentinel | AWS 1, 3, 4, 5, 6, 10, 12 | drop the sentinel; New Relic default value `["*"]` selects all (DOCS N1) | converts (variable side, section 3) |
| `timeseries val=avg(m) ... \| summarize x = avg(arrayAvg(val))` | Azure KPI tiles | `SELECT average(m)` without `TIMESERIES` | approximate: Dynatrace averages per-bucket averages, New Relic averages raw points. Note it |
| `\| sort arrayAvg(val) desc \| limit toLong($Limit)` | Azure charts | `FACET ... ORDER BY average(m) LIMIT n`; `FACET ... ORDER BY` exists (DOCS N4). `LIMIT` cannot take a variable (DOCS N3) | only with the limit frozen to the variable's default (`10`) and a note |
| `smartscape` id filter `in(dt.smartscape_source.id, iCollectArray(toSmartscapeId(array($SmartscapeId)[])))` | all Azure metric tiles | none: it selects Dynatrace entity ids chosen by other variables | clause dropped in a draft |
| `$PrimaryTag:noquote` used as a field name | Azure | variable with output format identifier is possible (DOCS N3) but the values come from `fieldsSnapshot` | manual |
| `smartscapeNodes X \| parse aws.object, "JSON:..." \| expand \| filter contains(...)` | AWS 10, 12; K8s 15 | none. This is topology and cloud-configuration inventory | manual |
| `join [ smartscapeNodes HOST ... ], kind:leftOuter, on:{...}` | Host (15 tiles) | NRQL has `JOIN` against a subquery with `ON` equality, subquery `LIMIT` default 10, max 5000 (DOCS N4), but the right side here is Smartscape | manual |
| `lookup [ timeseries ... \| fieldsAdd ... iCollectArray ... ]`, `arraySum`, `shift:-3m` | K8s 10, 15, 18, 19, 20 | none | manual |
| `fetch security.events \| filter ... \| summarize countDistinctExact(x)` | Host (22 tiles) | `uniqueCount(x)`; exact up to 256 distinct values, approximate above unless `precision` is raised to at most 50000 (DOCS N4) | the query converts; the data does not exist in New Relic |
| `summarize {Risk score=takeMax(a), ...}, by:{x,y}` then `sort {a, direction:"descending"}, {b, ...}` | Host 2, 58 | `max(a)`, one `FACET x, y`; NRQL `ORDER BY` takes one key per query | draft (second sort key lost) |
| `fetch dt.davis.problems`, `fetch dt.davis.events.snapshots` | Azure `problems-*` (4) | none; New Relic issues are a different model | manual |
| `fieldsAdd concat("[", id, "](/ui/intent/...)")` and `columnTypeOverrides: markdown` | K8s 8, 15 | none | link columns dropped in a draft |
| `timeseries ... data = min(m), interval:1m, shift:-2m \| fieldsAdd arraySum(data)/arraySize(data)` | K8s 10 | a per-cluster average of a 0/1 metric | manual (array math) |
| `fetch metric.series`, `fieldsSnapshot metrics`, `data record(...) \| append [...]` | variable queries only | n/a | section 3 |

Not seen in the four files, so not assessed: `makeTimeseries`, `fetch logs`, `fetch spans`,
`fetch bizevents`, `summarize ... by:{bin(timestamp, ...)}`. A converter needs patterns for the first
two at least, since the existing workflow converter already handles `fetch logs` (REPO
`src/importer/sources/dynatrace/dql.py`).

### 2.6 Visualization types and their settings

DOCS D1 names the visualization values: `lineChart`, `areaChart`, `barChart`, `bandChart`,
`categoricalBarChart`, `pieChart`, `donutChart`, `singleValue`, `meterBar`, `gauge`, `table`, `raw`,
`recordList`, `davis`, `histogram`, `honeycomb`, `choropleth`, `dotMap`, `connectionMap`, `bubbleMap`,
`heatmap`, `scatterplot`, `treemap`. Each needs particular result field types (time + numeric array +
interval for time-series charts; values + categories for categorical charts). The four files use five.

| Dynatrace `visualization` | Count | New Relic widget (`visualization.id`) | Notes |
|---|---|---|---|
| `lineChart` | 22 | `viz.line` with `TIMESERIES` | `widget_line` in the provider (DOCS N1); `viz.line` is already in the IR (REPO) |
| `areaChart`, `barChart` (time) | 0 | `viz.area`; time bars have no direct widget (`viz.stacked-bar` is categorical) | the GCP translator maps stacked bars to area for the same reason (REPO `src/importer/sources/gcp/dashboards.py` BAR_NOTE) |
| `singleValue` | 19 | `viz.billboard` | thresholds map to `critical` and `warning` (2.7); sparkline: UNVERIFIED |
| `table` | 13 | `viz.table` | confirmed as a viz id in the provider source (DOCS N23). Not in the IR |
| `donutChart`, `pieChart` | 3 | `viz.pie` | donut styling: UNVERIFIED |
| `categoricalBarChart` | 2 | `viz.bar` (a `FACET` bar chart) | `valueAxis` with two fields (Host 10 has `numberOfVulnerabilities` and `riskScore`) means two series; keep the first |
| `honeycomb`, `heatmap`, `histogram` | 0 | no honeycomb widget in the provider; `viz.heatmap` and `viz.histogram` exist (DOCS N1, N23) | honeycomb stays manual |
| `gauge`, `meterBar` | 0 | `viz.bullet` is the closest provider widget (needs a `limit`) | UNVERIFIED as an equivalent |
| `bandChart`, `scatterplot`, `treemap`, maps, `davis`, `raw`, `recordList` | 0 | none | manual |

`visualizationSettings` keys in the four files (counts are tiles, LIVE-FILE):

| Key (count) | Content | New Relic equivalent |
|---|---|---|
| `autoSelectVisualization` (35) | boolean | none |
| `chartSettings` (27) | `fieldMapping{timestamp, leftAxisValues[]}` (18), `leftYAxisSettings{label, isLabelVisible, min{mode:"custom",value}, max{...}}` (12), `legend{position \| hidden}` (5), `truncationMode`, `xAxisLabel`, `xAxisScaling` (8), `curve:"smooth"` (3), `colorPalette`, `categoryOverrides` (3), `categoricalBarChartSettings{categoryAxis, valueAxis, layout, scale}` (5), `circleChartSettings` (3) | y-axis min and max: `y_axis_left_min`, `y_axis_left_max`, `y_axis_left_zero` (DOCS N1). `curve: smooth` is `chart_styles { line_interpolation = "smooth" }` (DOCS N1). `legend.hidden` is `legend_enabled = false` (DOCS N1). Per-category colours are `colors { series_overrides }` (DOCS N1). The rest has no equivalent |
| `dataMapping.displayedFields` (16) | fields shown in the legend or tooltip | none; implied by `FACET` |
| `unitsOverrides` (34) | `{identifier, unitCategory, baseUnit, displayUnit, decimals, suffix, delimiter, added}`. Seen: `unspecified/none` 44, `percentage/percent` 7, `data/byte` 2, `unspecified/count` 1 | `units { unit, series_overrides }` (DOCS N1). The accepted unit strings were not read: UNVERIFIED |
| `singleValue` (19) | `label`, `labelMode`, `recordField`, `prefixIcon`, `trend{isVisible}`, `sparklineSettings{isVisible, record}`, `colorThresholdTarget` | billboard title and value; the rest dropped |
| `table` (11) | `hiddenColumns`, `columnWidths`, `columnOrder`, `sortBy[{columnId, direction}]`, `columnTypeOverrides` (e.g. `markdown`), `linewrapEnabled`, `hideColumnsForLargeResults` | `initial_sorting {name, direction}` (DOCS N1); order and hidden columns are the `SELECT` list; widths and type overrides have no equivalent |
| `thresholds`, `coloring` | section 2.7 | section 2.7 |

An `autoscale` setting does not occur in any of the four files (the word appears only inside one tile
description). Axis behaviour is `leftYAxisSettings.min/max` with `mode: "custom"`.

### 2.7 Thresholds and colouring

Three different forms occur (LIVE-FILE). All colours are theme tokens such as
`var(--dt-colors-charts-status-critical-default, #c62239)`; the fallback hex value after the comma is
usable.

| Form | Where | Shape | Comparators seen |
|---|---|---|---|
| `thresholds[]` | 24 data tiles carry the key (singleValue, table; one list is empty) | `{id, field, title, isEnabled, rules:[{id, color:{Default}, comparator, label, value}]}` | `=` 14, `>` 11, `≥` 9 |
| `coloring.colorRules[]` | 4 Azure KPI tiles | `{value, comparator, field, colorMode:"custom-color", customColor:{Default}}` | `>=`, `>` |
| `coloring.thresholdRules[]` | 3 K8s line charts | `{color, label:"Unhealthy", colorMode, mode:"range", position:"left", strokeOnly, min, max}` | range, `min` and optional `max` |

New Relic side (DOCS N1, N23): a billboard has `critical` and `warning` values, "above" semantics, and
the provider writes them as `thresholds:[{alertSeverity: CRITICAL|WARNING, value}]`; a line widget has
`threshold {name, from, to, severity}` with severity `warning|severe|critical|success|unavailable`; a
table widget has `threshold {column_name, from, to, severity}`.

Rule for the converter: ascending `>=`/`>` rules map to warning and critical on a billboard (the
`>` versus `>=` edge is lost; note it); an `=` rule (for example "= 0 is green") has no equivalent and
is dropped; K8s range rules map to a line threshold band with severity `warning`; table rules map to a
per-column threshold only when `field` names a column in the `SELECT`. The mapping from the theme token
to a severity is by name (`status-critical` to critical, `status-warning` to warning, `ideal` to
success): UNVERIFIED as a general rule, observed on the Azure KPI tiles only.

### 2.8 Layout

DOCS D1: 24 columns; `x`, `y` zero-indexed; `w`, `h` in grid units; typical widths 24, 12, 6; typical
heights 1 for a header, 6 to 8 for a chart. In the files: widths seen include 2, 4, 5, 6, 8, 12, 24
(Host KPI tiles are 2 wide at x = 0, 2, 4, 8, 10, 12, 16, 18, 20; Azure KPIs are 4, 5, 5, 5, 5 wide at
x = 0, 4, 9, 14, 19); heights 2 to 7 (LIVE-FILE).

New Relic: 12 columns, `row` and `column` start at 1, width 1 to 12, height documented as 1 to 12 in
the provider (DOCS N1); default width 4 and height 3. The repo IR allows height up to 100
(REPO `src/importer/core/dashboard_ir.py` `le=100`, and the GCP translator clamps to 100). Whether
NerdGraph enforces 12 is UNVERIFIED; if the provider rule is real, taller widgets fail on apply.

Conversion rule proposal: column edges `round(x/2)` and `round((x+w)/2)` (so neighbours still touch,
as `_mosaic` does for GCP, REPO), then width = right - left with a minimum of 1. A width-2 Host KPI
becomes 1 column wide. Row height: `max(1, round(h/2))` (UNVERIFIED choice; the Dynatrace row unit in
pixels and the New Relic row unit were not read). Run the existing `pack()` overlap fix afterwards
(REPO `src/importer/sources/gcp/dashboards.py`).

### 2.9 Coverage count on the four real dashboards

Rule set used (conservative). A data tile is exact when every clause maps to NRQL without changing the
result: one `timeseries` or `fetch ... summarize` source; plain aggregations; `by` on plain fields;
filters built from `==`, `!=`, `in()`, `matchesValue()`, `NOT`, `AND` with literals or dashboard
variables; at most one sort key; a literal `limit`; renames; a visualization from the table in 2.6; no
join, lookup, append, expand, parse, `smartscapeNodes`, array math, `shift`, link columns, or variable
in a `LIMIT` or field-name position. A draft is a tile that is exact except for one or more clauses that
only select which Dynatrace entities to show (the smartscape-id and primary-tag filters), a second sort
key, or link columns; the clause is dropped with a note and the tile is created only when a person
asks for needs-review output (same convention as the notification plans, REPO `docs/what-migrates.md`).
Everything else is manual. Markdown tiles are counted separately.

| File | Data tiles | Exact | Draft | Manual | Markdown |
|---|---|---|---|---|---|
| AWS | 8 | 1 (14) | 5 (1, 3, 4, 5, 6) | 2 (10, 12) | 4 |
| Azure | 19 | 0 | 14 (4 KPI + 10 charts) | 5 (`kpi-count`, 4 Davis problem tiles) | 8 |
| Host | 22 | 5 (5, 6, 10, 20, 49) | 2 (2, 58) | 15 (every tile with `join`) | 15 |
| K8s | 10 | 4 (1, 4, 5, 6) | 1 (8) | 5 (10, 15, 18, 19, 20) | 7 |
| Total | 59 | 10 | 22 | 27 | 34 |

Why each group is what it is:

- Exact, 10: AWS 14 and K8s 1, 4, 5, 6 read `dt.sfm.*` self-monitoring metrics; Host 5, 6, 10, 20, 49
  read `security.events`. The queries translate; the data does not exist in New Relic (section 7).
- Draft, 22: AWS 1 to 6 carry a `smartscapeNodes` subquery that keeps only Firehose streams that
  deliver to Dynatrace; dropping it widens the result to all streams. Azure KPI and chart tiles carry
  the smartscape-id and primary-tag filters and a `$Limit` variable. K8s 8 has link columns. Host 2 and
  58 have a two-key sort.
- Manual, 27: 15 Host tiles join `security.events` to Smartscape hosts and processes; AWS 10 and 12,
  Azure `kpi-count` and K8s 15 read Smartscape inventory; four Azure tiles read Davis problems and
  warnings; K8s 10, 18, 19, 20 use array math or an `in(lookup(...))` filter.
- Markdown, 34: all convert to `viz.markdown`; 4 contain Dynatrace UI links that must be removed, 2
  are an image from `dt-cdn.net`, 4 are spacers that should be dropped.

Data availability overlay (documented New Relic counterparts only):

| Tiles | Dynatrace metric | New Relic counterpart | Evidence |
|---|---|---|---|
| Azure KPI cpu, memory, disk queue, HTTP queue, and charts cpu, memory, disk queue, HTTP queue, bytes in, bytes out (10 tiles) | `cloud.azure.microsoft_web.serverfarms.{CpuPercentage, MemoryPercentage, DiskQueueLength, HttpQueueLength, BytesReceived, BytesSent}` | event type `AzureAppServicePlanSample`, attributes `cpuPercent`, `memoryPercent`, `diskQueueLength`, `httpQueueLength`, `bytesReceivedBytes`, `bytesSentBytes` | DOCS N14; LIVE-FILE Azure |
| Azure TCP established, TCP states, socket overview, socket outbound (4 tiles) | `...TcpEstablished`, `...SocketInboundAll`, etc. | none listed in the integration's metric table | DOCS N14 (absence in the table; the table may not be exhaustive) |
| AWS tile 1 | `cloud.aws.firehose.IncomingRecords.By.DeliveryStreamName` | `aws.firehose.IncomingRecords` (CloudWatch metric stream naming `aws.<namespace>.<Metric>`) | DOCS N13; LIVE-FILE AWS |
| AWS tiles 3, 4, 5, 6 | `ThrottledRecords`, `DeliveryToHttpEndpoint.{DataFreshness,Success,Records}` | not in the New Relic metric list for Firehose (the list shows `IncomingRecords`, `DeliveryToS3.*`, `PutRecord*`); a metric stream may still carry them | DOCS N13 (absence); UNVERIFIED whether they exist |
| AWS 14, K8s 1 to 10, 18 to 20, all Host tiles | `dt.sfm.*`, `dt.kubernetes.*`, `security.events` | none | no documented counterpart |

So at most 11 of the 59 data tiles (10 Azure, 1 AWS) have a documented counterpart, and all 11 are
drafts. Those Azure tiles also read `AzureAppServicePlanSample`, not `Metric` (the New Relic polling
integration reports an event type, DOCS N14), so the emitter must be able to choose the event type.

### 2.10 What the importer needs before it can translate these

- IR: a series that carries NRQL built from validated parts (current `Series` holds a
  `MetricSignal`, REPO `src/importer/core/dashboard_ir.py`), a table widget kind, `viz.table`,
  billboard thresholds, line thresholds, dashboard variables, and a per-widget event type. All NRQL
  must still go through `quote_string` and `identifier` (REPO `CLAUDE.md`). `viz.table` and
  `viz.billboard` thresholds are emitted through `rawConfiguration` (DOCS N23).
- A DQL reader for the exact patterns in 2.5 (same style as `dql.py`: anchored, length-bounded
  patterns, a fixed reason string on failure, never the query text, REPO).
- A metric-name map (section 7.3). Without it every `timeseries` tile is manual.

## 3. Variables and filters

### 3.1 Platform dashboard variables (LIVE-FILE, 18 variables in 4 files)

DOCS D1 defines three types, all with `key`, `type`, `visible`, `editable`, and in the real files
`version: 2`:

| Type | Extra fields | Seen |
|---|---|---|
| `query` | `input` (DQL returning exactly one field), `multiple`, `defaultValue` | 17 |
| `text` | `defaultValue` | 1 (Azure `Limit`, default `"10"`) |
| `csv` | `input` (comma list), `multiple`, `defaultValue` | 0 |

Reference syntax: single select `filter field == $V`; multi select `filter in(field, array($V))`;
modifiers `:noquote`, `:backtick`, `:triplequote` (DOCS D1). Variables can reference other variables;
circular dependencies are not allowed (DOCS D1).

The "select all" default is a UUID-plus-asterisk token (`3420b2ac-f1cf-4b24-b62d-61ba1ba8ed05*`), not a
literal `*` (DOCS D1; LIVE-FILE, 12 variables: Azure 5, Host 5, K8s 2). The AWS file instead builds a `*`
choice by hand: `data record(aws.region="*") | append [ fetch metric.series | ... ]` with
`($awsRegion == "*" or aws.region == $awsRegion)` in each tile (LIVE-FILE AWS).

Dependencies in the real files: Azure `SmartscapeId` depends on `Subscription`, `Location`,
`ResourceGroup`; Azure `PrimaryTagValues` depends on `PrimaryTag`; K8s `ActiveGateId` depends on
`Cluster`; AWS `deliveryStreamName` depends on `dynatraceEnvironment`. Sources of the lists: Smartscape
(`smartscapeNodes ...`: Azure 5, AWS 1 as a sub-query, Host 1 as a sub-query), `fetch security.events`
(Host 5), `timeseries` (K8s 2), `fetch metric.series` (AWS 3), `fetch dt.entity.environment` (AWS 1),
`fieldsSnapshot metrics` (Azure 1), none (Azure `Limit`, a text variable).

### 3.2 New Relic variables

| Item | New Relic | Evidence |
|---|---|---|
| Types | `enum` (fixed list with `item {title, value}`), `nrql` (query with `account_ids`), `string` | DOCS N1 |
| Fields | `name`, `title`, `type`, `default_values` (use `["*"]` to select all), `is_multi_selection`, `replacement_strategy` (`default`, `identifier`, `number`, `string`), `options {ignore_time_range, excluded, show_apply_action}` | DOCS N1 |
| Name rule | must start with a letter; letters, numbers, underscores | DOCS N3 |
| Use in NRQL | `{{name}}`; multi values need `IN`; allowed in `SELECT`, `FROM`, `FACET`, `ORDER BY`, `WHERE`; not allowed in `AS`, `COMPARE WITH`, `LIMIT`, `OFFSET`, `SINCE`, `SLIDE BY`, `TIMESERIES`, `UNTIL`, `WITH` | DOCS N3 |
| Field or event name from a variable | output format "identifier" (`replacement_strategy = identifier`) | DOCS N3 |
| Include toggle | a variable can be excluded so the query is not filtered by it | DOCS N3, N1 |
| Variable in markdown, widget title | supported | DOCS N3 |
| Maximum number of variables per dashboard; maximum values per variable (the page mentions a default 5000 for `uniques`) | not read | UNVERIFIED |
| Whether one variable's NRQL can use another variable | not read | UNVERIFIED |

### 3.3 Variable conversion on the four files

| Class | Count | Variables |
|---|---|---|
| Exact | 1 | K8s `Cluster` (`timeseries count(dt.kubernetes.nodes), by:{name=k8s.cluster.name} \| fields name \| ...`): a list of a dimension of one metric, becomes `SELECT uniques(<mapped dimension>) FROM Metric WHERE metricName = '<mapped metric>'` once the name map has the metric |
| Draft | 4 | AWS `awsAccountId`, `awsRegion`, `deliveryStreamName` (the `fetch metric.series` list with the Dynatrace-endpoint subquery dropped); K8s `ActiveGateId` (depends on `Cluster`) |
| Manual | 13 | AWS `dynatraceEnvironment`; Azure `SmartscapeId`, `Subscription`, `Location`, `ResourceGroup`, `PrimaryTag`, `PrimaryTagValues`, `Limit`; Host `Product`, `RiskLevel`, `CVE`, `Component`, `ProductStage` |

Notes: Azure `Subscription`, `Location`, `ResourceGroup` are simple distinct lists and become draft once
the Azure dimension names in New Relic are known (UNVERIFIED). `Limit` cannot be a variable in New Relic
(DOCS N3); the converter freezes it to the default value. The Host variables read security events with
no New Relic source.

### 3.4 Classic dashboard filters

Classic dashboards (Configuration API) carry `dashboardMetadata.dashboardFilter` with a default
`timeframe` and a `managementZone`, and `dynamicFilters.filters` from a fixed list: `OS_TYPE`,
`SERVICE_TYPE`, `DEPLOYMENT_TYPE`, `APPLICATION_INJECTION_TYPE`, `PAAS_VENDOR_TYPE`, `DATABASE_VENDOR`,
`HOST_VIRTUALIZATION_TYPE`, `HOST_MONITORING_MODE`, `KUBERNETES_CLUSTER`, `RELATED_CLOUD_APPLICATION`,
`RELATED_NAMESPACE`, and the tag forms `SERVICE_TAG_KEY:<name>`, `HOST_TAG_KEY:<name>`,
`APPLICATION_TAG_KEY:<name>`, `CUSTOM_DIMENSION:<key>`, `PROCESS_GROUP_TAG_KEY:<name>`,
`PROCESS_GROUP_INSTANCE_TAG_KEY:<name>`; plus `genericTagFilters` (DOCS D5). Each tile may carry a
`tileFilter` with its own `managementZone` and `timeframe` that overrides the dashboard (DOCS D5, D6).

New Relic has no management zone. The nearest equivalents are an enum or NRQL variable on a tag
attribute and account scoping, and both require the entities to carry the same tags in New Relic.
Converter rule: a management-zone filter is reported as manual with the zone name; tag-key filters
become a draft variable only when the tag attribute is known to exist in New Relic (a live read).

## 4. SLOs

### 4.1 Dynatrace shapes

Classic (`/api/v2/slo`, also the Settings schema `builtin:monitoring.slo`; DOCS D10, D11):

| Field | Meaning |
|---|---|
| `name`, `description`, `enabled` | |
| `metricName` | name used to create the SLO's func metric keys; cannot change after creation |
| `metricExpression` | percentage-based metric expression, for example a ratio of two built-in metrics |
| `evaluationType` | `AGGREGATE` only |
| `filter` | entity selector, at most 1,000 characters |
| `evaluationWindow` (settings) / `timeframe` (API) | for example `-1h`, `-1w`, `-2d to now`, `-1d/d to now/d` |
| `targetSuccess` / `target`, `targetWarning` / `warning` | percentages; warning is still "fulfilled" |
| `errorBudgetBurnRate{burnRateVisualizationEnabled, fastBurnThreshold}` | |
| deprecated: `useRateMetric`, `metricNumerator`, `metricDenominator`, `metricRate` | |

The classic API itself carries a banner "Switch to SLO Service Public API" (DOCS D10). Up to 10,000 custom SLOs
(DOCS D11).

Platform (`/platform/slo/v1/slos`, scope `slo:slos:read`/`slo:slos:write`; DOCS D8, D9):

| Field | Meaning |
|---|---|
| `name`, `description`, `tags[]`, `externalId` | |
| exactly one of `sliReference{templateId, variables[{name,value}]}` or `customSli{indicator, filterSegments[{id, variables[{name, values[]}]}]}` | both or neither is HTTP 400 |
| `criteria[{target, warning, timeframeFrom, timeframeTo}]` | `timeframeFrom` is a relative expression like `now-7d` or ISO 8601; `timeframeTo` defaults to now |
| response: `id`, `version` | |

Real sample (REPO `tests/fixtures/dynatrace/slo-custom-sli.json`): `criteria [{target 99.0, timeframeFrom
"now-7d", timeframeTo "now", warning 99.5}]` and
`customSli.indicator = "timeseries { total = sum(dt.service.request.count), failed =
sum(dt.service.request.failure_count) }, from:-7d | fieldsAdd sli = (total[] - failed[]) / total[] *
100"`. The indicator is a time series of a percentage called `sli`.

### 4.2 New Relic service level

Terraform `newrelic_service_level` (DOCS N2), also the model behind the NerdGraph create mutation (the
mutation's input type names were not read: UNVERIFIED):

| Field | Rule |
|---|---|
| `guid` | required: the entity the SLI relates to (APM service, browser app, workload, synthetic monitor); changing it recreates the resource |
| `name` (required), `description` | |
| `events.account_id` | required; account holding the data and the entity |
| `events.valid_events {from, where, select{attribute, function, threshold}}` | required; `function` is `COUNT`, `SUM`, `GET_FIELD` or `GET_CDF_COUNT` |
| `events.good_events` or `events.bad_events` | give one, never both |
| `events.facets` | optional, at most 2; with facets, valid and good/bad must use the same `from` and the same `function` (and attribute unless `COUNT`) |
| `objective.target` | 0 to 100, up to 5 decimals |
| `objective.time_window.rolling {count, unit}` | `count` is 1, 7 or 28; `unit` is `DAY` only |
| no warning level in the resource | DOCS N2 (absence) |
| permissions | needs a role allowed to create events-to-metric rules; only Full users can view SLIs/SLOs |

This confirms two assumptions that `src/importer/sources/dynatrace/service_levels.py` marks UNVERIFIED:
periods of 1, 7 and 28 days, and no warning level (REPO header of that file; DOCS N2). Calendar
windows are not available.

### 4.3 What maps

| Dynatrace | New Relic | Notes |
|---|---|---|
| `target` | `objective.target` | exact |
| `timeframeFrom: now-1d / now-7d / now-28d` (or `-1w`, `-4w`) | rolling 1, 7, 28 DAY | exact; `now-30d`, `-2w`, `-1M`, `now/d` calendar windows have no equivalent: manual, or 28 with a note |
| `warning` / `targetWarning` | none | dropped with a note (REPO already does this) |
| `errorBudgetBurnRate` | none in the resource | burn-rate alerting is a separate alert condition (UNVERIFIED mechanism; the New Relic docs list an "alerts on SLM" page, DOCS N17 links) |
| `filter` entity selector / `filterSegments` | `where` on the events plus the target `guid` | needs the entity to exist in New Relic |
| `sliReference` + `templateId` | none | the template body is Dynatrace-side; read it with the objective-templates API (`/platform/slo/v1/objective-templates`, DOCS D8 link list) and then treat it as a custom SLI. Not read |
| `customSli.indicator` over `dt.service.request.count` / `failure_count` | valid: `FROM Transaction WHERE entityGuid = '<guid>'`, bad: `FROM TransactionError WHERE entityGuid = '<guid>' AND error.expected != true` | the documented "service success" SLI for agent-instrumented services (DOCS N17). Not identical to Dynatrace failure detection |
| latency SLI (`response.time < X`) | good: `FROM Transaction WHERE entityGuid = '<guid>' AND transactionType = 'Web' AND duration < <seconds>` | DOCS N17; unit conversion from Dynatrace microseconds is on the converter (UNVERIFIED unit) |
| synthetic availability SLO | valid `FROM SyntheticCheck WHERE entityGuid = '<guid>'`, good `... AND result = 'SUCCESS'` | DOCS N2 example; requires the converted monitor |
| browser success / LCP SLOs | `PageView` / `JavaScriptError` / `PageViewTiming` | DOCS N17 |

What cannot map: an SLO whose indicator reads a metric that has no New Relic counterpart (for example
any `dt.sfm.*`, `dt.kubernetes.*`, or a log-derived custom metric): the importer keeps the plan with
the good and valid queries marked TODO, as `service_levels.py` does today (REPO). New Relic also
supports metric-based SLIs from `FROM Metric` with `GET_FIELD`/`GET_CDF_COUNT` (DOCS N2, N17), but the
Dynatrace metric must first arrive in New Relic (section 7).

Dependency to state in the report: every New Relic service level needs the target entity `guid`. A
converted SLO is therefore blocked until the service is instrumented and reporting (section 6), and
the converter must take a `service name -> entity guid` mapping from the user or a live read.

## 5. Synthetic monitors

### 5.1 Dynatrace shapes

Classic v1 (`/api/v1/synthetic/monitors`, access-token scopes `ReadSyntheticData`,
`ExternalSyntheticIntegration`, `DataExport`; DOCS D12, D14). Types: `HTTP`, `BROWSER`. Platform
(`/platform/synthetic/v1/monitors`, scope `synthetic:monitors:read`, marked Early Access): types `HTTP`,
`BROWSER`, `NETWORK_AVAILABILITY`; the list returns only `enabled`, `id`, `name`, `type`; selectors
include location, tags, enablement and security context (DOCS D15). The list responses carry only
names and ids; details need one call per monitor (DOCS D14).

Common monitor fields (DOCS D12, D13): `name`, `type`, `enabled`, `frequencyMin` (5, 10, 15, 30 or 60),
`locations[]` (public `GEOLOCATION-...`, private `SYNTHETIC_LOCATION-...`), `tags[]`, `managementZones[]`,
`manuallyAssignedApps[]`, `script{...}`, `anomalyDetection`:

- `outageHandling`: `globalOutage` + `globalOutagePolicy.consecutiveRuns`; `localOutage` +
  `localOutagePolicy{affectedLocations, consecutiveRuns}`; `retryOnError` (browser only).
- `loadingTimeThresholds{enabled, thresholds[{type: TOTAL|ACTION, valueMs, requestIndex, eventIndex}]}`.

HTTP script (DOCS D17): `{"version":"1.0","requests":[{description, url, method, requestBody,
validation{rules:[{type, passIfFound, value}], rulesChaining}, configuration{userAgent,
acceptAnyCertificate, followRedirects, requestHeaders[{name,value}]}, preProcessingScript,
postProcessingScript}]}`. Rule `type` is `patternConstraint`, `regexConstraint`, `httpStatusesList` or
`certificateExpiryDateConstraint`.

Browser script (DOCS D13, D18): `type` is `clickpath` or `availability` (single URL, exactly one
`navigate` event); `configuration.device{deviceName, orientation}`; `events[]` of kinds navigate, click
or tap (`interactionEvent`), JavaScript, select option, cookie, keystrokes; each event may carry
`validate[]`, `wait`, and `target.locators[{type: css, value}]`; `cookies[]` at the top.

### 5.2 New Relic targets

| New Relic monitor | Terraform resource and key fields | Evidence |
|---|---|---|
| Ping/simple (`SIMPLE`) | `newrelic_synthetics_monitor`: `name`, `status`, `period`, `uri`, `type`, `locations_public` or `locations_private`, `validation_string`, `verify_ssl`, `custom_header{name,value}`, `treat_redirect_as_failure`, `bypass_head_request`, `tag` | DOCS N5 |
| Simple browser (`BROWSER`) | same resource: `type = "BROWSER"`, `devices`, `browsers`, `runtime_type = CHROME_BROWSER`, `runtime_type_version`, `enable_screenshot_on_failure_and_script` | DOCS N5 |
| Scripted API or browser | `newrelic_synthetics_script_monitor`: `type` `SCRIPT_API` (runtime `NODE_API`) or `SCRIPT_BROWSER`, `script` | DOCS N6 |
| Step monitor | `newrelic_synthetics_step_monitor`: `steps{ordinal 0..100, type, values}`, types `ASSERT_ELEMENT`, `ASSERT_MODAL`, `ASSERT_TEXT`, `ASSERT_TITLE`, `CLICK_ELEMENT`, `DISMISS_MODAL`, `DOUBLE_CLICK_ELEMENT`, `HOVER_ELEMENT`, `NAVIGATE`, `SECURE_TEXT_ENTRY`, `SELECT_ELEMENT`, `TEXT_ENTRY` | DOCS N7 |
| Certificate check | `newrelic_synthetics_cert_check_monitor`: `domain`, `certificate_expiration` (days), `period`, locations | DOCS N8 |
| Broken links | `newrelic_synthetics_broken_links_monitor`: `uri`, locations, `period` | DOCS N9 |
| Periods | `EVERY_MINUTE`, `EVERY_5_MINUTES`, `EVERY_10_MINUTES`, `EVERY_15_MINUTES`, `EVERY_30_MINUTES`, `EVERY_HOUR`, `EVERY_6_HOURS`, `EVERY_12_HOURS`, `EVERY_DAY` | DOCS N5 |

The legacy synthetics runtime reached end of life on Aug 18, 2026 (the certificate-check page gives
Nov 18, 2026, nine days after this note); the provider v3.51.0 and later treats configs without the new runtime attributes as invalid.
Emit `runtime_type` and `runtime_type_version` on every monitor (DOCS N5, N8).

### 5.3 Mapping, and what can be created from JSON

| Dynatrace | New Relic | Created from JSON alone? |
|---|---|---|
| HTTP, one request, `GET`/`HEAD`, no pre/post script, at most one `patternConstraint` with `passIfFound: true` | `SIMPLE`: `uri`, `validation_string`, `verify_ssl = !acceptAnyCertificate`, `custom_header` from `requestHeaders`, `tag` | yes |
| `followRedirects` | `treat_redirect_as_failure` | semantics may be inverted (Dynatrace `false` reports a 3xx as success); mapping direction UNVERIFIED, emit with a note |
| `userAgent` | none in the simple monitor's argument list | dropped with a note (DOCS N5 absence) |
| `httpStatusesList` rule | none on `SIMPLE` | needs a `SCRIPT_API` script that asserts the status; manual |
| `regexConstraint`, `passIfFound: false`, several rules | none on `SIMPLE` | `SCRIPT_API`; manual |
| `certificateExpiryDateConstraint` | cert-check monitor on the host of the URL | yes |
| POST with body, several requests, chaining, `preProcessingScript`/`postProcessingScript` | `SCRIPT_API` | needs a script; the Dynatrace pre/post scripts use Dynatrace's own script API and do not port (UNVERIFIED API surface) |
| `NETWORK_AVAILABILITY` (ICMP, TCP, DNS) | no native monitor found | `SCRIPT_API` using Node networking: UNVERIFIED |
| Browser `availability` (one `navigate`) | `BROWSER` simple browser, `uri` | yes |
| Browser `clickpath` of navigate, click/tap, keystrokes, select option | `newrelic_synthetics_step_monitor` with `NAVIGATE`, `CLICK_ELEMENT`, `TEXT_ENTRY` or `SECURE_TEXT_ENTRY`, `SELECT_ELEMENT`; `validate` rules to `ASSERT_TEXT`/`ASSERT_ELEMENT` | partly: the shape of `values` for a click (selector syntax) was not read; Dynatrace carries several CSS locators per event. UNVERIFIED |
| Clickpath with JavaScript events, cookie events, credential vault references | `SCRIPT_BROWSER` | needs a script; manual |
| Device emulation (`deviceName`, `orientation`) | `devices` (`DESKTOP`, `MOBILE_PORTRAIT`, `MOBILE_LANDSCAPE`, `TABLET_PORTRAIT`, `TABLET_LANDSCAPE`) | yes (name mapping table needed) |
| `frequencyMin` 5/10/15/30/60 | `EVERY_5_MINUTES` ... `EVERY_HOUR` | yes |
| Availability thresholds (`outageHandling`: global and local outage, consecutive runs) | an alert condition on `SyntheticCheck` results, not part of the monitor | separate resource; the exact resource and its fields were not read: UNVERIFIED |
| `loadingTimeThresholds` (`valueMs`) | an alert condition on duration | same: UNVERIFIED |
| Locations | table below | |

### 5.4 Locations

New Relic public locations are cloud regions named like `AWS_US_EAST_1`, `AWS_AP_SOUTH_1`; the `AWS_`
prefix is optional through NerdGraph/Terraform (`US_EAST_1`), and the page lists regions with city names,
for example Frankfurt, DE (DOCS N10, N5). Dynatrace public locations have an `entityId`, a name, a
`cloudPlatform` (`AMAZON_EC2`, `AZURE`, `GOOGLE_CLOUD`, `ALIBABA`, `DYNATRACE_CLOUD`, `INTEROUTE`,
`OTHER`), a `type` (`PUBLIC`, `PRIVATE`, `CLUSTER`), a `stage` and a `status` (DOCS D16). The mapping
is by city or country to the nearest New Relic region, kept as a reviewed table in the repo with an
"unmapped location" outcome. Private locations (`SYNTHETIC_LOCATION-`) map to New Relic private
locations, which are separate resources and can take up to 10 minutes to become available (DOCS N7);
the minion install is manual. Which Dynatrace location names exist on the owner's tenant needs a live
read.

## 6. Instrumentation inventory

### 6.1 Purpose

The goal is a re-instrumentation plan: how many hosts, which services in which technology, which
Kubernetes clusters and which cloud accounts are monitored by Dynatrace, with counts per target New
Relic agent. This is read-only, and does not need the dashboards.

### 6.2 What can be listed, and how

| Item | Source | Endpoint and scope | Evidence |
|---|---|---|---|
| Entities by type, with properties | Entities API v2 (classic) | `GET /api/v2/entities?entitySelector=type("HOST")&fields=...&pageSize=...` (default 50), token scope `entities.read`; entity ids such as `SERVICE-...`; per-service technology list in `properties.serviceTechnologyTypes` (for example `["IIS app pool","ASP.NET","DotNet"]`, `["Java"]`). The docs page carries a banner: from version 1.334 classic entity queries and this API "should no longer be used"; use Smartscape on Grail | DOCS D19, D20 |
| Smartscape nodes and edges | DQL | `POST /platform/storage/query/v1/query:execute` with `smartscapeNodes "*" \| summarize count(), by:{type}` and `smartscapeEdges`. The query endpoint lists nine `storage:*:read` scopes and none is `smartscape` | DOCS D21, D22, D23 |
| Smartscape scope | IAM | `storage:smartscape:read` "read smartscape nodes and edges from Grail" exists in the IAM statement reference. Whether the query endpoint accepts it, or `storage:entities:read`, is not stated | DOCS D24; the gating is UNVERIFIED |
| Node types | semantic dictionary | `HOST`, `PROCESS`, `K8S_*`, `AWS_*`, `AZURE_*`, and synthetic types `BROWSER_MONITOR`, `HTTP_MONITOR`, `NETWORK_AVAILABILITY_MONITOR`, `SYNTHETIC_LOCATION`, `CREDENTIAL_VAULT_ENTRY` | DOCS D22; LIVE-FILE (`AZURE_MICROSOFT_WEB_SERVERFARMS`, `AWS_KINESISFIREHOSE_DELIVERYSTREAM`, `AWS_LOGS_LOGGROUP`, `K8S_CLUSTER`) |
| Edges | semantic dictionary | `runs_on`, `belongs_to`, `calls`, `is_part_of`, `monitors`, `is_assigned_to`, ... (stable and experimental) | DOCS D22 |
| Fields on real nodes | the dashboards | `HOST`: `id`, `name`, `ip[]`, `tags`; `PROCESS`: `references[runs_on.host]`, `tags`, `primary_tags.*`; `K8S_CLUSTER`: `name`, `dt.metadata[activegate_id]`, `dt.metadata[activegate_version]`; AWS nodes: `aws.account.id`, `aws.region`, `aws.arn`, `aws.object` (JSON); Azure nodes: `azure.subscription`, `azure.location`, `azure.resource.group`, `azure.resource.name`. A metric dimension `dt.smartscape_source.id` joins metrics to nodes | LIVE-FILE (Host, K8s, AWS, Azure) |
| Which metrics have data | DQL | `fieldsSnapshot metrics` (also `logs`, `spans`, `smartscape.nodes`); `fetch metric.series \| filter startsWith(metric.key, "...") \| summarize count(), by:{...}` | DOCS D21b; LIVE-FILE AWS, Azure |
| Which AWS Firehose streams and log groups send to Dynatrace | DQL on Smartscape | the AWS file lists streams whose `httpEndpointDestinationDescription.endpointConfiguration.url` contains `/api/aws/firehose/v1/logs` and log groups with subscription filters pointing at them | LIVE-FILE AWS tiles 10, 12 |
| Cost of a DQL inventory query | | each query consumes billable capacity; use `defaultScanLimitGbytes` and short windows. The query API "is not intended for large-scale data export" | DOCS D23 |

Property names for technologies on `PROCESS`/`SERVICE` Smartscape nodes were not found in the pages
read; the classic `serviceTechnologyTypes` is the only documented technology field (DOCS D20). A first
live read of one `SERVICE` and one `PROCESS` node (`fields` list) settles this.

### 6.3 Technology to New Relic mapping

| Dynatrace technology (OneAgent code module or supported tech) | New Relic target | Evidence |
|---|---|---|
| Java, .NET, Node.js, PHP, Go, Ruby, C++, Erlang/Elixir, Rust, NGINX (application software pages) and Python (runtime table) | see next rows | DOCS D25 |
| Java | APM Java agent | DOCS N19 |
| .NET | APM .NET agent | DOCS N19 |
| Node.js | APM Node.js agent | DOCS N19 |
| Python | APM Python agent | DOCS N19 |
| PHP | APM PHP agent | DOCS N19 |
| Ruby | APM Ruby agent | DOCS N19 |
| Go | APM Go agent (a library; code change vs OneAgent injection is the difference) | DOCS N19; the code-change statement is UNVERIFIED |
| C++, Rust, Erlang/Elixir | OpenTelemetry SDK to the New Relic OTLP endpoint | DOCS N11 (OTLP); the absence of a New Relic agent for these is from DOCS N19 not listing them |
| NGINX, Apache, IIS, databases, queues (extension and plugin technologies) | New Relic on-host integrations through the infrastructure agent | UNVERIFIED (not read) |
| Host (OneAgent full-stack, OS metrics, processes) | New Relic infrastructure agent (Linux, macOS, Windows) | DOCS N21 |
| Kubernetes (Dynatrace Operator, cluster monitoring) | New Relic Kubernetes integration | DOCS N20 |
| AWS / Azure / GCP integrations | CloudWatch Metric Streams (recommended over API polling), Azure integrations, GCP integration | DOCS N16, N14; GCP: REPO `src/importer/sources/gcp` |
| Synthetic locations and monitors | New Relic synthetics | section 5 |
| Real User Monitoring (web, mobile) | New Relic browser and mobile agents | UNVERIFIED (not read) |
| Serverless (Lambda) | New Relic Lambda layer | UNVERIFIED (not read) |

Counts to report per row come from: services by `serviceTechnologyTypes` (classic) or the Smartscape
equivalent; hosts by `HOST` node count and OS; Kubernetes by `K8S_CLUSTER` and `K8S_NODE`; cloud by
`AWS_*`/`AZURE_*` node counts per account/subscription. Dynatrace supports the CPU architectures and
OS lists on the technology support page, which also decides whether the New Relic infrastructure agent
covers the host (OS support on the New Relic side was not compared: UNVERIFIED).

### 6.4 What has no migration path

| Dynatrace capability | Path |
|---|---|
| OneAgent deep monitoring (automatic code-level instrumentation, process and service detection) | Replaced, not migrated: re-instrument with New Relic agents or OpenTelemetry. No export of OneAgent's collected data to another vendor is documented in the pages read (UNVERIFIED absence) |
| PurePath distributed traces | Not exportable as a format New Relic ingests. Spans can be forwarded to cloud object storage by OpenPipeline (`spans` scope is in the forwarding list; DOCS D31), but New Relic has no documented ingest from that storage for spans (UNVERIFIED). History is lost |
| Davis AI topology, root cause analysis, Davis problems | Not portable. Smartscape topology can be read for the inventory (DOCS D21), but the causal model is Dynatrace's. New Relic has its own issue correlation, which needs its own setup |
| Smartscape-driven filters and dashboards (`dt.smartscape_source.id`) | No equivalent; section 2 |
| Grail data and history (metrics, logs, spans, events, business events, security events) | Remains in Grail; forwarding to object storage is possible for logs, metrics, spans and events (DOCS D31) |
| Management zones, security context | No New Relic equivalent (tags and accounts) |
| Dynatrace Hub extensions (Extensions 2.0) | Replace with New Relic on-host integrations or OpenTelemetry receivers: UNVERIFIED |
| Application Security / vulnerability findings (`security.events`) | No New Relic source; the Host vulnerability dashboard has no target |

## 7. Data path: getting Dynatrace-collected data into New Relic

No route replays what OneAgent collected into New Relic with Dynatrace's metric names. Each route below
changes where the data is produced from, or moves a copy.

### 7.1 Routes

| Route | Signals | What results in New Relic | Limits and caveats | Evidence |
|---|---|---|---|---|
| OpenTelemetry Collector dual export | traces, metrics, logs that arrive at the collector over OTLP (any SDK or receiver) | OTLP data keeps its OpenTelemetry names; New Relic endpoint `https://otlp.nr-data.net` (US), `otlp.eu01.nr-data.net`, `otlp.jp.nr-data.net`, `gov-otlp.nr-data.net`, ports 443/4317/4318, header `api-key` = license key. Dynatrace's own docs show one collector exporting to Dynatrace and a second backend | Only data that goes through OTLP. Prefer delta temporality for counters and histograms (both vendors: Dynatrace works with delta values only, New Relic prefers delta). New Relic prunes attributes: 64 resource, 8 scope, 128 per record; string value 4095 chars; key 255; arrays 64 | DOCS D28, D27, D32, N11 |
| Application SDK exports OTLP directly to New Relic | same | same | The OTLP exporter must be changed on each app | DOCS N11 |
| OneAgent | n/a | not possible: OneAgent offers a local (127.0.0.1) OTLP endpoint for traces only (port 14499, needs the Extension Execution Controller) and sends data to Dynatrace. It receives OTLP; it does not forward it | OneAgent does not support `Content-Encoding` gzip on that endpoint | DOCS D29 |
| Pull Dynatrace metrics and re-ingest | metrics | whatever name the bridge writes (a custom bridge to a New Relic ingest API; the ingest API was not read) | Metrics API v2: `metrics.read` scope, at most 120 points by default per series, `resolution` configurable, CSV output available. The DQL query API "is not intended for large-scale data export". Poll cost, rate limit 429s (REPO `docs/research/dynatrace-native-coverage.md`) | DOCS D33, D23; ingest side UNVERIFIED |
| OpenPipeline forwarding to cloud object storage | logs, metrics, spans, events, Davis events and problems, business events, security events, system events, user events and sessions | NDJSON batches (ZSTD or GZIP) in S3, Azure Blob or GCS-class storage, partitioned by date; created through the Settings API schema `builtin:openpipeline.<scope>.data-forwarding` | Not a New Relic route by itself. For logs, New Relic documents an AWS Lambda `NewRelic-log-ingestion-s3` for S3 buckets; whether it parses Dynatrace's NDJSON shape is not documented | DOCS D31, N18; parse UNVERIFIED |
| Prometheus | metrics | If the Prometheus server is the source, add a `remote_write` to New Relic; names are the Prometheus names | Dynatrace ingests Prometheus (K8s annotations, Extensions 2.0, OpenTelemetry Collector with Target Allocator); a route that exposes Dynatrace-held metrics as a Prometheus scrape target was not found in the pages read | DOCS D30, N12; reverse route UNVERIFIED |
| Logs | logs | Replace the OneAgent log module with New Relic log forwarding (infra agent, Fluent Bit, S3 Lambda) | History stays in Grail | DOCS N18; others UNVERIFIED |
| AWS cloud integration re-pointed | CloudWatch metrics and logs | CloudWatch Metric Streams to New Relic: metric names `aws.<lowercase namespace without "AWS/">.<MetricName>` such as `aws.firehose.IncomingRecords`, `aws.ec2.CPUUtilization`; dimensions as `aws.<service>.<Dimension>` attributes; a few metrics carry a `.by<Dimension>` suffix kept for history | The Firehose stream feeding Dynatrace is itself a configuration the importer can list (LIVE-FILE AWS), and a stream's HTTP endpoint is what changes | DOCS N16, N13; LIVE-FILE |
| Azure cloud integration re-pointed | Azure Monitor metrics | New Relic Azure integration reports event types, for App Service plans `AzureAppServicePlanSample` with `cpuPercent`, `memoryPercent`, `diskQueueLength`, `httpQueueLength`, `bytesReceivedBytes`, `bytesSentBytes` | Only the metrics in the integration's table; TCP and socket counters are not listed | DOCS N14 |
| GCP | | the existing GCP source and link generator | | REPO |
| Kubernetes | cluster, node, pod metrics | New Relic Kubernetes integration (re-install); the metric and event names (`K8s*Sample`) were not read | Dynatrace names (`dt.kubernetes.*`) do not appear | DOCS N20; names UNVERIFIED |

### 7.2 Name differences already visible in the real files

| Dynatrace key (LIVE-FILE) | Dimension names (LIVE-FILE) | New Relic name (documented pattern) |
|---|---|---|
| `cloud.aws.firehose.IncomingRecords.By.DeliveryStreamName` | `DeliveryStreamName`, `aws.account.id`, `aws.region` | `aws.firehose.IncomingRecords` (DOCS N13), dimension and account attribute names UNVERIFIED |
| `cloud.azure.microsoft_web.serverfarms.CpuPercentage` | `azure.resource.name`, `azure.subscription`, `azure.resource.group`, `azure.location` | `AzureAppServicePlanSample.cpuPercent` (DOCS N14), attribute names UNVERIFIED |
| `dt.service.request.count`, `dt.service.request.failure_count` (REPO fixture) | | APM `Transaction` count, `TransactionError` (DOCS N17); no `Metric` equivalent needed for SLOs |
| `dt.host.cpu.usage` (DOCS D1 example) | `host.name` | infrastructure agent `SystemSample.cpuPercent` or OpenTelemetry `system.cpu.utilization`: UNVERIFIED |
| `dt.sfm.*`, `dt.kubernetes.*`, `dt.davis.*`, `security.events` | | no counterpart |

### 7.3 What a converter must emit so converted conditions have data

New Relic cannot receive Dynatrace names on any documented route, so the converter needs two things
beyond the NRQL: a name map it can trust, and a data check that tells the user what is missing. Proposal:

1. A map file, committed and reviewed like the golden files (REPO `CLAUDE.md`), one entry per
   Dynatrace metric key:
   - `dt_key`; `dt_dimensions` (names seen);
   - `nr`: `event` (`Metric` or a Sample event type), `select` (a metric name, or an attribute for a
     Sample event), `dimensions` (Dynatrace dimension to New Relic attribute), the aggregation word map
     (`avg` to `average`, and so on), and the units;
   - `status`: `documented` (DOCS), `verified` (confirmed by a live read in the customer's account),
     `unverified`, or `none` (no counterpart);
   - `source`: the URL or file.
   Only `verified` and `documented` entries produce an active condition or widget; `unverified` produces
   a draft; `none` produces a manual item with the reason "no New Relic counterpart".
2. A data check like `check-gcp-data` and `check-dynatrace-data` (REPO `docs/what-migrates.md`): for
   each emitted metric, ask New Relic whether it has data in the last hours and report converted
   items with no data. That needs a New Relic account and is therefore a customer-side live step.
3. A route recommendation per item in the report, taken from 7.1: for example "this tile reads
   `cloud.azure.microsoft_web.serverfarms.CpuPercentage`; enable the Azure integration" or "this tile
   reads Davis problems; no New Relic source".
4. For every logs-based item (the existing workflow conversion), the field mapping that the existing
   converter flags as unconfirmed (`loglevel` to `level`, REPO `docs/what-migrates.md`).

## 8. Prioritised build list

Effort: S is about a day, M a few days, L a week or more. Risk is the chance the result is wrong or
unusable without a live read.

| # | Item | Effort | Risk | Why now |
|---|---|---|---|---|
| 1 | Extract dashboards: add scope `document:documents:read` to the token check, list `type = 'dashboard'` documents, fetch each content, save JSON; also extract synthetic monitors (`/platform/synthetic/v1/monitors` and v1 for detail) and platform SLOs already present | S | low (content path UNVERIFIED) | everything else needs real input; the four files are only samples |
| 2 | Inventory report and re-instrumentation plan: Smartscape counts by type, services by technology, K8s clusters, Firehose streams to Dynatrace, AWS/Azure account counts; table of counts per New Relic agent (6.3) | M | medium (technology field names, query scope) | answers the first question a customer asks and needs no New Relic data |
| 3 | Metric name map file, loader, validator, and a report of "Dynatrace metrics used, with and without a New Relic name" across dashboards, SLOs and workflows | M | medium | the gate for items 4, 5 and 6; start with the Azure App Service plan and AWS Firehose entries from section 2.9 |
| 4 | Platform dashboard translator for the exact and draft classes: IR extension (raw-NRQL series, table, thresholds, variables, event type), DQL reader for the patterns in 2.5, layout 24 to 12, markdown tiles, variables 3.3, needs-review gating; golden tests from the four files | L | medium; the four files give exact tile shapes but only 11 tiles have a documented data target | the largest user-visible gain |
| 5 | SLO conversion for agent-instrumented services: `Transaction`/`TransactionError` and `SyntheticCheck` shapes, rolling window map, `guid` supplied by the user, create through Terraform | M | medium (guid dependency, failure semantics) | the plan generator exists (REPO); this fills the TODO queries for the common case |
| 6 | Synthetic conversion: `SIMPLE`, `BROWSER`, cert check from JSON; location table; manual entries with reasons for the rest; Terraform output with the new runtime attributes | M | low to medium | self-contained and creatable from JSON |
| 7 | Data check for Dynatrace dashboards and SLOs (extend `check-dynatrace-data`) | S | low | tells the user which converted items have no data |
| 8 | Step monitor from clickpath (navigate, click, keystrokes, select option, validate) | L | high (locator shape unread) | after a read of the New Relic step `values` format |
| 9 | Classic dashboards (Configuration API): `DATA_EXPLORER` queries to NRQL through the name map, `MARKDOWN`, `HEADER`; entity tiles, USQL, problem tiles manual | L | medium | only if the tenant still has classic dashboards |
| 10 | Dual-export collector config generator (Dynatrace + New Relic exporters, delta temporality) | S | low | a documented, mechanical route (DOCS D28, N11) |
| 11 | Metric bridge (pull from Metrics API v2, push to New Relic) | L | high; Dynatrace docs discourage bulk export | recommend against unless a customer needs history |

Not worth building: honeycomb, Davis problem tiles, security-event tiles, OneAgent or PurePath data
migration.

### Facts that need a live read of a customer Dynatrace tenant

1. The content endpoint path and response of a dashboard document, and the list of `type` values.
2. How many dashboards exist, how many are ready-made versus user-made, and whether any use `code`,
   `slo`, `segments` at dashboard level, dashboard `timeframe` overrides, or Explore (`queryConfig`) tiles.
3. Whether classic dashboards (`/api/config/v1/dashboards`) still exist and which token type reads them
   (`ReadConfig` is the classic scope, DOCS D5; platform-token access is UNVERIFIED).
4. Which DQL commands real user dashboards use beyond the four files (`makeTimeseries`, `fetch logs`,
   `fetch spans`, `fetch bizevents`, `bin()`).
5. The tenant's state (classic, hybrid, latest) and the Smartscape field names for technology on `SERVICE`
   and `PROCESS` nodes, and which scope `smartscapeNodes` needs through the query endpoint.
6. Which metric keys actually have data (`fieldsSnapshot metrics`), to size the name map.
7. SLOs: how many use `sliReference` (and which templates) versus `customSli`; classic SLO
   `metricExpression` forms; timeframes in use (to see how many are not 1, 7 or 28 days).
8. Synthetic monitors: counts by type, frequency, location names and cloud platform, use of pre/post
   scripts, multi-request, JavaScript and cookie events, credential vault references, and private
   locations.
9. Whether OpenTelemetry is already emitted by the applications (dual export is then cheap), and which
   AWS Firehose streams point at Dynatrace.
10. On the New Relic side (separate live read, not allowed in this research): whether `aws.*` metrics
    from a metric stream, `AzureAppServicePlanSample`, and the Kubernetes event types are present in the
    target account, and the attribute names of their dimensions.

## 9. UNVERIFIED list

- Content endpoint of a Platform dashboard document and its response shape (2.1).
- Meaning of `importedWithCode` (2.2).
- Mapping of theme colour tokens to New Relic severities as a general rule (2.7).
- Whether NerdGraph enforces widget height 12 as the provider doc says (2.8).
- Row-height conversion factor from Dynatrace grid units to New Relic rows (2.8).
- Accepted unit strings of the New Relic `units` block (2.6); donut styling; billboard sparkline; `viz.bullet`
  as a gauge equivalent (2.6).
- Whether one New Relic variable's NRQL can reference another variable; maximum variables per dashboard
  (3.2).
- Dynatrace `matchesValue()` and `in()` case and wildcard rules (2.5).
- NerdGraph service level mutation input names (4.2); calendar windows are not in the Terraform resource but
  the NerdGraph side was not read.
- Burn-rate alerting mechanism in New Relic (4.3); unit of Dynatrace response-time in a latency SLI (4.3).
- Contents of Dynatrace objective templates (4.3).
- `followRedirects` to `treat_redirect_as_failure` direction; Dynatrace pre/post script API portability;
  `NETWORK_AVAILABILITY` in New Relic; shape of step `values`; alert-condition resources for synthetics
  (5.3).
- Platform token scope that gates `smartscapeNodes`; Smartscape technology field names (6.2).
- New Relic equivalents for web servers, databases, RUM, Lambda, Hub extensions (6.3, 6.4); Go agent code-change
  statement.
- No documented export of OneAgent data, PurePath, or a Prometheus scrape of Dynatrace-held metrics (absence of
  evidence, 6.4, 7.1).
- New Relic ingest from object storage for spans or metrics; parsing of Dynatrace NDJSON by the S3 Lambda
  (7.1).
- New Relic attribute names for AWS and Azure dimensions, Kubernetes event and metric names, host metric names
  (7.2).
- `host.name`, `dt.host.cpu.usage` equivalents (7.2).

## Sources

Dynatrace (all read 2026-11-09):

| Id | URL |
|---|---|
| D1 | https://docs.dynatrace.com/docs/analyze-explore-automate/dashboards-and-notebooks/document-api/document-structure-dashboards |
| D2 | https://docs.dynatrace.com/docs/analyze-explore-automate/dashboards-and-notebooks/dashboards-new |
| D3 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-document-v1 |
| D4 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-document-v1/documents-get |
| D5 | https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/dashboards-api/post-dashboard (and `/get-dashboard`, `/get-all`) |
| D6 | https://docs.dynatrace.com/docs/dynatrace-api/configuration-api/dashboards-api/dashboards-api-tile-models |
| D7 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-dashboards-general |
| D8 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-slo-v1/slos-post (and the index `.../platform-slo-v1`) |
| D9 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-slo-v1/slos-get |
| D10 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/service-level-objectives/get-slo |
| D11 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/settings/schemas/builtin-monitoring-slo |
| D12 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-monitors/get-a-monitor |
| D13 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-monitors/post-a-monitor |
| D14 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-monitors/get-all-monitors |
| D15 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-synthetic-v1/monitors-get |
| D16 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/synthetic/synthetic-locations/get-all-locations |
| D17 | https://docs.dynatrace.com/docs/observe/digital-experience-classic/synthetic-monitoring-classic/http-monitors-classic/script-mode-for-http-monitor-configuration-classic |
| D18 | https://docs.dynatrace.com/docs/observe/digital-experience-classic/synthetic-monitoring-classic/browser-monitors/script-mode-for-browser-monitor-configuration |
| D19 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2 |
| D20 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/entity-v2/get-entities-list |
| D21 | https://docs.dynatrace.com/docs/discover-dynatrace/platform/grail/dynatrace-query-language/commands/smartscape-commands |
| D21b | https://docs.dynatrace.com/docs/discover-dynatrace/platform/grail/dynatrace-query-language/commands/data-source-commands |
| D22 | https://docs.dynatrace.com/docs/semantic-dictionary/model/smartscape |
| D23 | https://docs.dynatrace.com/docs/dynatrace-api/platform-api/platform-storage-query-v1/query-execute-post |
| D24 | https://docs.dynatrace.com/docs/manage/identity-access-management/permission-management/manage-user-permissions-policies/advanced/iam-policystatements |
| D25 | https://docs.dynatrace.com/docs/ingest-from/technology-support |
| D26 | https://docs.dynatrace.com/docs/ingest-from/technology-support/oneagent-platform-and-capability-support-matrix |
| D27 | https://docs.dynatrace.com/docs/ingest-from/opentelemetry/otlp-api |
| D28 | https://docs.dynatrace.com/docs/ingest-from/opentelemetry/collector-use-cases/multi-export |
| D29 | https://docs.dynatrace.com/docs/ingest-from/dynatrace-oneagent/oneagent-and-opentelemetry/oneagent-otel |
| D30 | https://docs.dynatrace.com/docs/ingest-from/extend-dynatrace/extend-metrics/ingestion-methods/prometheus |
| D31 | https://docs.dynatrace.com/docs/platform/openpipeline/concepts/forwarding |
| D32 | https://docs.dynatrace.com/docs/ingest-from/opentelemetry/getting-started/metrics/limitations |
| D33 | https://docs.dynatrace.com/docs/dynatrace-api/environment-api/metric-v2/get-data-points |

New Relic:

| Id | URL |
|---|---|
| N1 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/one_dashboard.html.markdown |
| N2 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/service_level.html.markdown |
| N3 | https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/ |
| N4 | https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/ |
| N5 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/synthetics_monitor.html.markdown |
| N6 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/synthetics_script_monitor.html.markdown |
| N7 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/synthetics_step_monitor.html.markdown |
| N8 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/synthetics_cert_check_monitor.html.markdown |
| N9 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/website/docs/r/synthetics_broken_links_monitor.html.markdown |
| N10 | https://docs.newrelic.com/docs/synthetics/synthetic-monitoring/administration/synthetic-public-minion-ips/ |
| N11 | https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/ |
| N12 | https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/ |
| N13 | https://docs.newrelic.com/docs/infrastructure/amazon-integrations/manage-aws-data/aws-integrations-metrics/ |
| N14 | https://docs.newrelic.com/docs/infrastructure/microsoft-azure-integrations/azure-integrations-list/azure-app-service-monitoring-integration/ |
| N15 | https://docs.newrelic.com/docs/infrastructure/amazon-integrations/aws-integrations-list/aws-kinesis-data-firehose-monitoring-integration/ |
| N16 | https://docs.newrelic.com/docs/infrastructure/amazon-integrations/aws-integrations-list/aws-metric-stream/ |
| N17 | https://docs.newrelic.com/docs/service-level-management/create-slm/ |
| N18 | https://docs.newrelic.com/docs/logs/forward-logs/aws-lambda-sending-logs-s3/ |
| N19 | https://docs.newrelic.com/docs/apm/agents/ |
| N20 | https://docs.newrelic.com/docs/kubernetes-pixie/kubernetes-integration/get-started/introduction-kubernetes-integration/ |
| N21 | https://docs.newrelic.com/docs/infrastructure/infrastructure-monitoring/get-started/get-started-infrastructure-monitoring/ |
| N23 | https://raw.githubusercontent.com/newrelic/terraform-provider-newrelic/main/newrelic/structures_newrelic_one_dashboard.go (provider source: viz ids, billboard `alertSeverity`) |

Repository files: `src/importer/core/dashboard_ir.py`, `src/importer/core/emit/dashboard.py`,
`src/importer/core/emit/nrql.py`, `src/importer/sources/gcp/dashboards.py`,
`src/importer/sources/dynatrace/{dql,extract,service_levels}.py`,
`tests/fixtures/dynatrace/slo-custom-sli.json`, `docs/what-migrates.md`,
`docs/research/dynatrace-native-coverage.md`.
