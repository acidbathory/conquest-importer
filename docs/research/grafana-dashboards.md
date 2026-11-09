# Grafana dashboards as a migration source: model, read APIs, query and widget mapping, coverage

Research for a future `--source grafana` dashboard converter in Conquest Importer. Public documentation and public source only. No Grafana or New Relic instance was called, no `.env` was read, no cloud command was run. Written 2026-11-09 against grafana/grafana `main` and Grafana docs "latest" (Grafana 13.x era).

## 0. How to read this file

Every claim carries one of these tags.

- DOCS: read on a vendor documentation page; the source id points to the index below.
- CODE: read in a file of github.com/grafana/grafana (or the New Relic docs-website / Terraform provider repository); the path is given.
- REPO: read in this repository.
- MEASURED: a count or ratio I computed over the public files listed in section 8. Method and limits are in section 8; a different classifier gives different numbers.
- UNVERIFIED: from memory, from a second-hand note, or an inference. Do not build on it without a check. They are collected in section 11.

Source index (all fetched with curl GET on 2026-11-09).

| id | source |
|---|---|
| D1 | https://grafana.com/docs/grafana/latest/developers/http_api/dashboard/ (new `/apis` dashboard API) |
| D2 | https://grafana.com/docs/grafana/v11.6/developers/http_api/dashboard/ (legacy `/api/dashboards/*`) |
| D3 | https://grafana.com/docs/grafana/latest/developers/http_api/folder_dashboard_search/ (`/api/search`) |
| D4 | https://grafana.com/docs/grafana/latest/developers/http_api/folder/ (folders) |
| D5 | https://grafana.com/docs/grafana/latest/developers/http_api/library_element/ (library panels) |
| D6 | https://grafana.com/docs/grafana/latest/administration/roles-and-permissions/access-control/rbac-fixed-basic-role-definitions/ |
| D7 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/build-dashboards/view-dashboard-json-model/ |
| D8 | https://grafana.com/docs/grafana/latest/administration/provisioning/ |
| D9 | https://grafana.com/docs/grafana/latest/visualizations/dashboards/variables/variable-syntax/ |
| D10 | https://grafana.com/docs/grafana/latest/datasources/prometheus/template-variables/ |
| D11 | https://grafana.com/docs/grafana/latest/developers/http_api/ (legacy API index, deprecation note) |
| D12 | https://grafana.com/docs/grafana/latest/administration/service-accounts/ |
| N1 | https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/ |
| N2 | https://docs.newrelic.com/docs/query-your-data/explore-query-data/use-charts/chart-types/ |
| N3 | https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/ |
| N4 | newrelic/docs-website `src/content/docs/query-your-data/explore-query-data/dashboards/nested-variables.mdx` |
| N5 | newrelic/docs-website `src/content/docs/apis/nerdgraph/examples/create-widgets-dashboards-api.mdx` |
| N6 | newrelic/terraform-provider-newrelic `website/docs/r/one_dashboard.html.markdown` |

Other session's note (untracked, read-only): `docs/research/grafana-native-coverage.md` in the main checkout. Where I rely on it without re-reading the primary source I say so and tag the claim UNVERIFIED.

## 1. Answers first

1. Grafana dashboards come in three JSON shapes today: classic (a flat object with `panels[]`), the v1 resource (the classic object under `spec` in a `dashboard.grafana.app/v1*` envelope) and the v2 resource (`elements` + `layout`, `dashboard.grafana.app/v2*`). A reader must handle all three and must still handle classic dashboards with `schemaVersion` from 12 to 42 (DOCS D7; CODE C1 below).
2. The New Relic target is small: line, area, bar, billboard, pie, table, markdown are in the repository's IR today (REPO `src/importer/core/dashboard_ir.py`); heatmap, histogram, bullet, json, funnel, stacked bar, log table, event feed exist in New Relic but not in the IR (DOCS N5, N6). There is no dashboard-variable model in the IR or the emitter (REPO `dashboard_ir.py`, `emit/dashboard.py`).
3. Of 1,404 panels in 69 public community dashboards, about 22% convert under a rule set no wider than the alert translator, about 39% when `rate`/`increase`/`histogram_quantile`/`topk` are added, and about 47% when simple arithmetic is added (MEASURED, section 8). On Prometheus-heavy dashboards alone the three numbers are 32%, 57% and 70%. On dashboards using other data sources the number is 5% to 8%.
4. The two biggest levers are data-source breadth (CloudWatch, Elasticsearch, SQL, InfluxDB, Azure Monitor, Google Cloud Monitoring, all unconverted) and PromQL shapes with binary operators, `on()/group_left`, `label_replace`, nested aggregation and comparisons.
5. 89% of targets that carry an `expr` (Prometheus and Loki) reference a dashboard variable (`$job`, `$instance`, ...) (MEASURED). Dashboards cannot be converted well without a variable model; this is the first thing the IR needs.
6. A read-only service account with the fixed role `fixed:folders:reader` (or `dashboards:read` plus `folders:read`, plus `library.panels:read`) is enough to read everything section 3 needs (DOCS D6, D1, D5).

## 2. The dashboard JSON model

### 2.1 Three shapes and how to tell them apart

DOCS D7 names three models: V2 Resource (current, Kubernetes-kind modelling, "advanced layouts and conditional rendering"), V1 Resource (the classic schema formatted as a Kubernetes-style resource; default for API communication between Grafana 12.2.0 and 13.0.0), and Classic (non-Kubernetes JSON; default before 12.2.0; the format of grafana.com/dashboards).

Discrimination rule (derived from DOCS D1, D7 and the files in section 8; tested on the 69 + 162 files):

| test, in order | shape |
|---|---|
| top level has `apiVersion` and `kind == "Dashboard"` and `spec` | Kubernetes envelope. `apiVersion` is `dashboard.grafana.app/<ver>` (DOCS D1, D8). Go on to the next row with `spec` as the root |
| root has `elements` (object) and `layout` (object with `kind`) | v2 (CODE C2: `DashboardSpec.elements`, `layout`) |
| root has `panels` (array) or `rows` (array) | classic (rows only in old schemas) |
| root has `dashboard` (object) and maybe `meta`, `folderUid`, `overwrite` | wrapper used by the legacy GET response and by provisioning files; unwrap and re-test (DOCS D2, D8) |
| root is an array | a list of dashboards (search result or hand-made); treat each item |
| `items` array and `metadata.continue` | a list response from `/apis/...` (DOCS D1) |

Version strings seen in the schema folder: `v0alpha1`, `v1beta1`, `v2`, `v2alpha1`, `v2beta1` (CODE C2: `apps/dashboard/kinds/{v0alpha1,v1beta1,v2,v2alpha1,v2beta1}`). The public docs example uses `dashboard.grafana.app/v1` (DOCS D1) and `v2beta1` / `v2` appear in the schema folder; the exact set a given stack serves is UNVERIFIED. In `grafana/grafana` main, 157 of 162 JSON files under `devenv/dev-dashboards` are `dashboard.grafana.app/v2` resources and 5 are classic (MEASURED, CODE C5).

Classic v. v2 inside one envelope: the v1 resource has `spec.panels`, `spec.schemaVersion`; the v2 resource has `spec.elements`, `spec.layout` (CODE C2). Do not rely on `apiVersion` alone: a v2 object may be served as v1 after server-side conversion (CODE `apps/dashboard/pkg/migration/conversion/v2_to_v1beta1.go`, `v1beta1_to_v2.go` exist), and the conversion code has a data-loss detector comparing panel and query counts (CODE `conversion_data_loss_detection.go`).

### 2.2 Classic schema: fields

Top level (DOCS D1 example, D7; CODE C1 migration code uses these names):

- identity and metadata: `id` (numeric, instance-local, null in exports), `uid` (max 40 characters, DOCS D2), `title`, `description`, `tags[]`, `version`, `editable`, `style`, `timezone`, `graphTooltip` (0, 1, 2), `fiscalYearStartMonth`, `weekStart`, `liveNow`, `preload`.
- time: `time{from,to}`, `timepicker{refresh_intervals, hidden, nowDelay, time_options (removed by schema 41, CODE C1 v41.go)}`, `refresh` (string, or boolean/number/null in old files; schema 40 normalises it to a string, CODE C1 v40.go).
- `links[]`: `{type: "dashboards"|"link", title, url, tags, asDropdown, includeVars, keepTime, targetBlank, icon, tooltip}` (DOCS D1 example).
- `annotations.list[]`: built-in entry `{builtIn:1, datasource:{type:"grafana", uid:"-- Grafana --"}, enable, hide, iconColor, name, type:"dashboard"}` and user entries with `datasource`, `expr`/`query`/`target`, `iconColor`, `enable`, `hide` (DOCS D1; entry shape for non-builtin entries is in section 7.1).
- `templating.list[]`: variables (section 6).
- `panels[]`: panel objects, with row panels `type:"row"` that hold `panels[]` when `collapsed:true` and are followed by their members in the flat list when `collapsed:false`.
- legacy: top-level `rows[]` with `panels[]` and `span` (before schema 16), `pulldowns`, `nav`, `services` (CODE C1 v2.go, v6.go, v7.go, v16.go).
- export-only: `__inputs[]`, `__requires[]`, `__elements{}` (the "export for sharing externally" form; CODE `apps/dashboard/pkg/migration/conversion/export_inputs.go` documents `__inputs` and notes `__elements` for library panels).

Panel (DOCS D1 example; CODE C1):

- `id`, `type`, `title`, `description`, `transparent`, `gridPos{x,y,w,h}` on a 24-column grid, one height unit is 30 px (CODE C1 `v16.go`: `gridColumnCount = 24`, `gridCellHeight = 30`).
- `datasource`: object `{type, uid}` (schema 33/36 migrate string names to this, CODE C1 v33.go, v36.go) or a string, or absent (default data source) or `null`.
- `targets[]`: query objects (section 5).
- `fieldConfig{defaults{unit, decimals, min, max, mappings[], thresholds{mode, steps[]}, color{mode, fixedColor}, noValue, links[], custom{...}}, overrides[{matcher{id,options}, properties[{id,value}]}]}` (DOCS D1; CODE C2 `FieldConfig`).
- `options{...}`: per-plugin (`legend`, `tooltip`, `reduceOptions`, `colorMode`, `graphMode`, `displayMode`, `content`, `mode`, ...).
- `transformations[{id, options}]`, `links[]`, `repeat`, `repeatDirection`, `maxPerRow`, `repeatPanelId`, `repeatIteration`, `libraryPanel{uid,name}`, `interval`, `maxDataPoints`, `timeFrom`, `timeShift`, `hideTimeOverride`, `pluginVersion`, `autoMigrateFrom`.
- legacy angular panels (`graph`, `singlestat`, `table-old`, old piechart): own fields such as `lines`, `bars`, `stack`, `fill`, `yaxes[]`, `aliasColors`, `seriesOverrides`, `valueMaps`, `thresholds` (string in singlestat, array in graph), `colors`, `sparkline`, `gauge`, `styles[]`, `transform` (CODE C1 v9.go, v10.go, v18.go, v22.go, v24.go, v30.go describe their migrations).

### 2.3 v2 schema

From CODE C2 `apps/dashboard/kinds/v2beta1/dashboard_spec.cue` (1,131 lines; `v2alpha1` and `v2` differ slightly, see below).

`spec` keys: `title`, `description`, `tags`, `editable`, `cursorSync` (`Off|Crosshair|Tooltip`), `liveNow`, `preload`, `revision`, `links[]`, `timeSettings{timezone, from, to, autoRefresh, autoRefreshIntervals, quickRanges, hideTimepicker, weekStart, fiscalYearStartMonth, nowDelay}`, `annotations[]` (`AnnotationQuery` kinds), `variables[]`, `elements{name: Panel|LibraryPanel}`, `layout`.

- Element `Panel`: `spec{id, title, description, subtitle, links[], transparent, data (QueryGroup{queries[PanelQuery{refId, hidden, query: DataQuery{kind:"DataQuery", group, version, datasource{name}, labels, spec{...}}}], transformations[{kind: <transformation id>, spec}], queryOptions{timeFrom, timeShift, maxDataPoints, interval, cacheTimeout, hideTimeOverride, timeCompare, queryCachingTTL}}), vizConfig{kind:"VizConfig", group:<plugin id>, version, spec{options, fieldConfig}}}`.
- Element `LibraryPanel`: `spec{id, title, libraryPanel{uid,name}}`.
- Layout kinds: `GridLayout` (`items[GridLayoutItem{x,y,width,height,element:{kind:"ElementReference",name}, repeat}]`), `RowsLayout` (`rows[RowsLayoutRow{title, collapse, hideHeader, fillScreen, conditionalRendering, repeat, layout, variables, annotations}]`), `TabsLayout` (`tabs[TabsLayoutTab{title, layout, conditionalRendering, repeat, variables, annotations}]`), `AutoGridLayout` (`maxColumnCount, columnWidthMode, columnWidth, rowHeightMode, rowHeight, fillScreen, items[AutoGridLayoutItem{element, repeat, conditionalRendering}]`). Rows, tabs and nested layouts can contain each other (`RowsLayoutRowSpec.layout` is `GridLayout|AutoGridLayout|TabsLayout|RowsLayout`). Conditional rendering kinds: `ConditionalRenderingGroup`, `ConditionalRenderingVariable`, `ConditionalRenderingData`, `ConditionalRenderingTimeRangeSize`.
- Variables (kinds): `QueryVariable`, `TextVariable`, `ConstantVariable`, `DatasourceVariable`, `IntervalVariable`, `CustomVariable`, `SwitchVariable`, `GroupByVariable`, `AdhocVariable`. `VariableHide` is `dontHide|hideLabel|hideVariable|inControlsMenu`, `VariableRefresh` is `never|onDashboardLoad|onTimeRangeChanged`, `VariableSort` has nine names (CODE C2). In v2 variables can also sit on a row or a tab (`RowsLayoutRowSpec.variables`, `TabsLayoutTabSpec.variables`, v2beta1 only).
- Repeat: `RepeatOptions{mode:"variable", value, direction, maxPerRow}` on grid items, rows, tabs and auto-grid items.
- The grid in `GridLayoutItem` uses `x,y,width,height` (not `gridPos` with `w/h`); the column count is not stated in the CUE file I read. UNVERIFIED that it is 24; the devenv examples were not checked for max `x+width`.
- Differences by version (CODE C2, diff of v2alpha1 and v2beta1): in v2alpha1 `DataQuery.kind` is the data-source type and there is a `datasource{type,uid}` reference on queries and annotations, in v2beta1 `DataQuery.kind` is the literal `"DataQuery"` with `group` (the plugin id) and `version`, and the data source is `datasource{name}` (a uid-like name). `VizConfig.kind` is the plugin id in v2alpha1 and the literal `"VizConfig"` with `group` in v2beta1. v2beta1 adds `placement` on annotation queries, `origin` on links and variables, `variables` and `annotations` inside rows and tabs, and `timeTo` in `queryOptions`. Which of these the owner's stack serves is UNVERIFIED.
- Data-source `group` values seen in the 157 v2 devenv dashboards: `grafana-testdata-datasource` (1,913 queries), `datasource` (151; the Grafana, Mixed and Dashboard pseudo-sources), `__expr__` (39), `elasticsearch` (28), `loki` (17), `grafana` (3) (MEASURED, CODE C5). Prometheus appears as `group: "prometheus"` in the documentation-style examples (the fixture in section 9 uses it) but no v2 devenv file queries it; UNVERIFIED for a live export.
- Panel types in the 157 v2 devenv dashboards (MEASURED, CODE C5): timeseries 587, geomap 163, table 162, text 147, gauge 80, stat 65, bargauge 56, barchart 46, histogram 42, xychart 32, state-timeline 28, heatmap 20, status-history 16, logs 12, candlestick 12, piechart 11, canvas 9, dashlist 8, trend 8, live 6, logstable 3, debug 2, news 1, alertlist 1, flamegraph 1, annolist 1, plus 2 library panels. Layouts: GridLayout 130, RowsLayout 23, TabsLayout 4; no AutoGridLayout at the top level (it appears in `dashboard-auto-grid/`, nested use not counted).

### 2.4 Schema migrations: what changes field names

`schemaVersion` is an integer; the backend has `MIN_VERSION = 0`, `LATEST_VERSION = 42`, one migration function per version (CODE C1 `apps/dashboard/pkg/migration/schemaversion/migrations.go`, `v2.go` to `v42.go`). The server runs these when it serves a dashboard through `/apis` (the migrations package exists in the apps/dashboard backend, so an `/apis` read yields a dashboard already at 42 for classic sources; UNVERIFIED that every read path migrates, and what a stack on an older release does). A reader that takes the raw legacy `/api/dashboards/uid` output or a file on disk gets the stored version.

Migrations that change names or shapes an importer reads (CODE C1, each from the doc comment of the file):

| schemaVersion | change |
|---|---|
| 2 | `services.filter.time` -> `time`, `services.filter.list` -> `templating.list` |
| 6 | `pulldowns` array -> `annotations`, variable shape updates |
| 7 | `nav` -> `timepicker`; every target gets a `refId` |
| 8 | InfluxDB queries: `fields/tags/groupBy` -> `select` form |
| 9, 10 | singlestat and table `thresholds`: drops the first (unused) value |
| 12 | variable `refresh: true/false` -> 1/0; `hideVariable` -> `hide: 2`; `hideLabel` -> `hide: 1` |
| 14 | `sharedCrosshair` (boolean) -> `graphTooltip` (0/1/2) |
| 16 | row layout with `span` (12 columns) -> `gridPos` (24 columns); `rows[]` become `panels[]` with row panels |
| 17 | `minSpan` -> `maxPerRow` |
| 18 | gauge options (`options-gauge`) -> `options` with `reduceOptions` |
| 19 | panel `links` normalised to url form |
| 20, 21 | data-link variables `__series_name` -> `__series.name`; `__series.labels` -> `__field.labels` |
| 22 | table styles: `align = "auto"` |
| 23 | `current` shape follows `multi` (array vs scalar) |
| 24 | adds `autoMigrateFrom: "table-old"` for tables that have `styles` |
| 26 | `text2` -> `text`; removes `angular` option |
| 27 | removes saved repeat clones (`repeatPanelId`, `repeatByRow`); `constant` variables become `textbox` |
| 28, 29 | removes variable tags fields; query variable `refresh` forced to 1 when not 1 or 2, options cleared |
| 30 | value mappings: legacy numeric types (1 = value to text, 2 = range) -> `type: "value"|"range"|"special"|"regex"` with `options`; `tooltipOptions` -> `tooltip` |
| 31 | adds a `merge` transformation after `labelsToFields` |
| 33, 36 | panel/target/variable data source string names -> `{type, uid}` (and `apiVersion`) |
| 34 | CloudWatch queries with several statistics split into one query each |
| 37 | legend: `displayMode: "hidden"` -> `showLegend: false` |
| 38 | table `displayMode` -> `cellOptions{type,...}` |
| 39 | `timeSeriesTable` transformation: `refIdToStat` -> per-query options |
| 40 | `refresh` always a string |
| 41 | removes `timepicker.time_options` |
| 42 | `hideFrom.viz = true` override also sets `hideFrom.tooltip = true` |

The frontend maps legacy angular panels when it opens a dashboard: `graph` -> `timeseries`, `table-old` -> `table`, `singlestat` -> `stat`, `grafana-singlestat-panel` -> `stat`, `grafana-piechart-panel` -> `piechart`, `grafana-worldmap-panel` -> `geomap`, `natel-discrete-panel` -> `state-timeline` (CODE `public/app/features/dashboard/state/PanelModel.ts`, `autoMigrateAngular`). 40% of the panels in the 69 community dashboards are `graph` (568 of 1,404), 13% `singlestat` (176) (MEASURED), so the importer needs its own mapping for them or a migration step; it cannot assume `type` is modern.

### 2.5 Fields an importer must tolerate

A reader should tolerate, not reject, each of these. They occur in the public files or in the migration code.

1. Shape: a wrapper (`dashboard`/`meta`), a resource envelope, `rows[]` without `panels[]`, and a v2 body. `annotations` is an object `{list: []}` in classic files and an array in v2 (my own script crashed when it unwrapped a v2 file as if it were classic), and `templating` may be a list in damaged files. `templating.list[]` items and `panels[]` items may be non-objects. `targets` may be missing or not an array.
2. `schemaVersion` missing, a string, or lower than 12. `version`, `id` null.
3. `datasource` as object, string, null, absent, a template (`"${DS_PROMETHEUS}"`, `"$datasource"`), `{"type":"datasource","uid":"grafana"}`, `{"uid":"-- Mixed --"}`, `{"uid":"-- Dashboard --"}`, `{"uid":"-- Grafana --"}`, or the legacy `-100`/`__expr__` for expressions (REPO `grafana/translate.py` `EXPRESSION_UIDS`).
4. `gridPos` missing, negative, zero, fractional, or wider than 24; panels overlapping; duplicate panel `id`s; collapsed rows whose `panels[]` carries the members; non-collapsed rows whose members follow in the flat list; row `repeat`; repeat clones saved in the file (`repeatPanelId`, `repeatIteration`).
5. `thresholds.steps[0].value` is `null` (base), `mode` absolute or percentage, `colors` as named, hex, `rgba(...)` or `#RRGGBBAA` strings (CODE C2 `Threshold`, `FieldColor`).
6. Value mappings in the post-30 structured form and in the legacy numeric form (CODE C1 v30.go), and `null`/`nan`/`empty` special matches (CODE C2 `SpecialValueMatch`).
7. `unit` strings from the Grafana unit list (`short`, `percent`, `percentunit`, `bytes`, `decbytes`, `s`, `ms`, `reqps`, `none`, `dateTimeAsIso`, `currencyUSD`, ...); the list is large and extensible, so unknown units must pass through as "no format".
8. Panel `title`, `description`, `text` content and variable names and values are untrusted text of any length and with any characters (section 9.3).
9. Dashboard-level `time.from` and `time.to` are relative expressions (`now-6h`, `now/d`, `now-1M/M`), absolute ISO times or epoch milliseconds, and `timepicker` values can be nonsense.
10. A library panel appears as `libraryPanel{uid,name}` plus at most a stub of fields; in an external export the model sits in `__elements` (CODE `export_inputs.go` comment). Without a second call (`/api/library-elements/{uid}`) the query is not known.
11. Query objects with only `refId` and `datasource` (the Grafana data source and `-- Dashboard --` reuse), hidden targets (`hide: true`), and targets of an unknown data source type (section 5).
12. Unknown panel types (plugins: `grafana-polystat-panel`, `marcusolsson-json-datasource`, ...) and panel `type` values that were renamed by migrations.

## 3. Read APIs

### 3.1 Endpoints

| purpose | endpoint | notes |
|---|---|---|
| list dashboards and folders | `GET /api/search?type=dash-db&limit=5000&page=N` | `type` is `dash-folder` or `dash-db`; `limit` max 5000 default 1000; `page` starts at 1; `folderUIDs`, `dashboardUIDs`, `tag`, `query`, `starred` filters; RBAC filters results to what the caller can see (DOCS D3). Response items carry `uid`, `title`, `folderUid`, `folderTitle`, `tags`, `url`, `type`. The folder fields are not in the excerpt I read; UNVERIFIED |
| one dashboard, legacy | `GET /api/dashboards/uid/{uid}` | `{dashboard:{...}, meta:{isStarred, url, folderId, folderUid, slug, ...}}`; permission `dashboards:read` scope `dashboards:*` (DOCS D2). Legacy API: "from Grafana 13, `/api` endpoints are being deprecated in favor of `/apis`... will no longer be updated; not being disabled" (DOCS D11) |
| list dashboards, new | `GET /apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards?limit=N&continue=TOKEN` | resource list; token in `metadata.continue`; permission `dashboards:read` scope `dashboards:*` (DOCS D1). The doc points to `dashboard.grafana.app/v2` in Swagger for the up-to-date list and says the page "may not contain the latest version of the API" (DOCS D1) |
| one dashboard, new | `GET /apis/dashboard.grafana.app/v1/namespaces/{namespace}/dashboards/{uid}` and `.../{uid}/dto` (adds access information) | DOCS D1. `metadata.name` is the uid; the folder is `metadata.annotations["grafana.app/folder"]`; created-by is `grafana.app/createdBy` (DOCS D1 example) |
| versions of the same resource | `.../v2beta1/...`, `.../v2/...`, `.../v1beta1/...`, `.../v0alpha1/...` | exist in the schema folder (CODE C2); `v2` has `/dashboards/search` and `/dashboards/{name}/dto` per the other session's note (UNVERIFIED, not re-read) |
| folders | `GET /apis/folder.grafana.app/v1/namespaces/{namespace}/folders?limit&continue` | DOCS D4. Legacy `GET /api/folders` exists (legacy API index lists the folder APIs, DOCS D11); nested folders are UNVERIFIED in the pages read |
| library panels | `GET /api/library-elements?kind=1&perPage=100&page=N`; `GET /api/library-elements/{uid}`; `GET /api/library-elements/{uid}/connections` | `kind=1` is panels; default `perPage` 100; list response `{result:{totalCount, page, perPage, elements[]}}`; the element has `uid`, `name`, `type`, `model` (the panel JSON) (DOCS D5). No `/apis` replacement is documented in the page read |
| dashboard versions | `GET /api/dashboards/uid/{uid}/versions` | path and `limit`/`start` parameters in https://grafana.com/docs/grafana/latest/developers/http_api/dashboard_versions/ (DOCS); only needed to detect "last edited" |
| data sources | `GET /api/datasources` | needed to resolve data source uids and types; needs `datasources:read` (permission name from memory, UNVERIFIED) |
| annotations | `GET /api/annotations` | legacy; `/apis/annotation.grafana.app/...` per the other session's note (UNVERIFIED) |
| permissions on a dashboard | `GET /api/access-control/...`, Dashboard Permissions API | not needed for conversion |

Namespace: `default` on a single-org install; on Grafana Cloud the namespace is `stacks-<stack id>` (the v1 example in DOCS D7 shows `stacks-521104`). Whether other namespaces appear for other orgs is UNVERIFIED.

### 3.2 Permissions for a read-only service account

DOCS D6 (fixed roles): `fixed:dashboards:reader` has `dashboards:read` (all dashboards); `fixed:folders:reader` has `folders:read` and `dashboards:read`; `fixed:library.panels:reader` has `library.panels:read`; `fixed:annotations:reader` has `annotations:read`; the Viewer basic role already includes `fixed:library.panels:general.reader` (root-level library panels only), `fixed:folders.general:reader` and `fixed:annotations:reader`. A service account with the Viewer role therefore reads dashboards it can view, but library panels outside the root folder need `fixed:library.panels:reader`. Whether custom role assignment is available on the owner's stack tier is UNVERIFIED; the Viewer basic role plus library panel access is the fallback. Service account tokens inherit the account's permissions and by default do not expire (DOCS D12).

The existing extractor reads alert rules with `GRAFANA_SA_TOKEN` (REPO `docs/what-migrates.md`, Grafana section). The same token is enough for dashboards only if its role covers dashboards; a token created with the narrow alerting reader roles is not (inference from DOCS D6; check on the live stack).

### 3.3 Pagination, limits, rate limits

- `/api/search`: `limit` (max 5000, default 1000, invalid values fall back to 1000) and `page` (from 1) (DOCS D3).
- `/apis` lists: `limit` and `continue` (DOCS D1, D4). The continue token is opaque; the example token decodes to an offset and a resource version (DOCS D1 example). A page with `limit` unset returns an unspecified default; use an explicit limit.
- `/api/library-elements`: `perPage` default 100, `page` from 1; `totalCount` in the response (DOCS D5).
- Rate limits: I found no documented rate limit for the dashboard read endpoints in the pages read. UNVERIFIED. Treat HTTP 429 as possible; read sequentially with a small delay, honor `Retry-After`, and cap total requests (the 13 dashboards on the owner's stack make this academic).

### 3.4 What a provisioning-only export misses

"Provisioning" is the file mechanism (YAML provider files pointing at a folder of dashboard JSON; DOCS D8). Dashboard files may be classic JSON, a `{dashboard:{...}}` wrapper, or Kubernetes-format JSON, and "you must use the Kubernetes resource format to provision dashboards v2" (DOCS D8). A folder of such files, or a Git repository, differs from a live read in these ways:

- no `meta` (folder uid and title, provisioned flag, permissions, created and updated times, starred); the folder comes from the provider's `folder` or `foldersFromFilesStructure` setting, not from the file (DOCS D8: "installs dashboards at the root level if you don't set the folder field").
- `uid` and `id`: files may omit `uid`; Grafana then generates one (DOCS D2), so keys are not stable across extracts. A file's `id` is meaningless.
- library panels: only the reference; the model is not in the file (DOCS D5 for the API that has it; CODE `export_inputs.go` for `__elements` in external exports).
- `__inputs` placeholders (`${DS_PROMETHEUS}`) that a live read has already resolved to a data source `uid`; the migration drops `__inputs` when it canonicalises a dashboard to v1 (CODE `export_inputs.go` comment, lines 7 to 21).
- the schema version is whatever the author saved; no server migration has run.
- data source uids are those of the stack that exported them; types may be absent for string references (schema 33/36 would have filled them; CODE C1).

## 4. Panel types to New Relic widgets

New Relic widgets available (DOCS N5, N6, N2): `viz.area`, `viz.bar`, `viz.billboard`, `viz.line`, `viz.markdown`, `viz.pie`, `viz.table` (typed); `viz.bullet`, `viz.event-feed`, `viz.funnel`, `viz.heatmap`, `viz.histogram`, `viz.json`, plus inventory, service map and metric-based line/area (untyped, `rawConfiguration` required). Terraform adds `widget_stacked_bar` and `widget_log_table` (DOCS N6). The chart types page lists a Gauge chart (DOCS N2), but neither the NerdGraph widget page nor the Terraform resource shows its id or block, so the gauge widget id is UNVERIFIED. Repository IR today: line, area, bar, pie, billboard, markdown (REPO `dashboard_ir.py`, `Visualization`), emitted through `rawConfiguration.nrqlQueries` only (REPO `emit/dashboard.py`).

Grid: New Relic allows at most 12 columns (DOCS N5); Grafana uses 24 (CODE C1 `v16.go`), so `column = x // 2 + 1`, `width = max(1, round(w / 2))` after clamping. Height: the CloudWatch converter uses `height // 2` (REPO `sources/cloudwatch/dashboards.py` line 56); a Grafana unit is 30 px, a New Relic row height in pixels is not in the pages I read, so the factor is UNVERIFIED and should be calibrated by looking at one converted dashboard.

### 4.1 Panel type table

"Today" means the repository IR can express the widget now. Every row's data comes from NRQL, so each also needs a convertible query (section 5).

| Grafana panel | New Relic widget | today | notes |
|---|---|---|---|
| timeseries (and legacy graph) | `viz.line`; `viz.area` when fill and stacking are set; stacked bars become `viz.area` as in the GCP converter | yes | `drawStyle: bars` -> `viz.bar` with `TIMESERIES` is not a bar-over-time chart in New Relic (a repository comment says bar charts have no time axis, REPO `gcp/dashboards.py` `BAR_NOTE`); points-only style: lost |
| stat (and legacy singlestat) | `viz.billboard` | yes | `graphMode: area` sparkline -> billboard with `TIMESERIES` is UNVERIFIED; `colorMode`, text size: lost; thresholds -> billboard `critical`/`warning` (DOCS N6) |
| gauge | Gauge chart (DOCS N2) or billboard | no (billboard yes) | gauge widget id not documented in sources read: UNVERIFIED; billboard with thresholds is the safe fallback |
| bargauge | `viz.bullet` (needs `limit`, DOCS N6) or `viz.bar` with `FACET` | no | bar gauge shows one bar per series, same idea as `FACET` bar |
| table | `viz.table` | yes | column order, hidden columns, cell display mode lost; `format: table` queries return a table already; `initial_sorting`, per-column `threshold` and `data_format` exist (DOCS N6) |
| piechart | `viz.pie` | yes | donut vs pie lost |
| barchart | `viz.bar` or stacked bar (Terraform `widget_stacked_bar`, DOCS N6) | bar yes | category axis from `FACET`; stacked bar id UNVERIFIED |
| heatmap | `viz.heatmap` | no | needs `histogram(attr)` with `FACET` (DOCS N1: "Use histogram() with a FACET clause to generate a heatmap chart"); Prometheus heatmaps over `_bucket` use `histogram(metric_bucket, ...)` (DOCS N1) |
| histogram | `viz.histogram` | no | `histogram(attr, width:, buckets:, start:)`, buckets 1 to 500 (DOCS N1) |
| state-timeline | none | no | no widget with state bands in the pages read; fallback `viz.table` or `viz.line` of a 0/1 series (UNVERIFIED that none exists) |
| status-history | none | no | as above |
| logs | log table (`widget_log_table`, DOCS N6) | no | LogQL must become NRQL on `Log` |
| logstable | log table or `viz.table` | no | |
| text | `viz.markdown` | yes | `mode: html` has no equivalent: strip or escape; content is untrusted (REPO escaping rule) |
| news | none | no | external RSS feed; not migratable |
| alertlist | none | no | New Relic has no alert-list widget in the pages read (event feed `viz.event-feed` is for entity events, UNVERIFIED for alerts) |
| dashlist | none | no | list of dashboards or a link list; a `viz.markdown` with links is the nearest, UNVERIFIED |
| geomap | none (map widgets exist for browser data, not for arbitrary lat/lon) | no | UNVERIFIED |
| nodeGraph | none | no | service map is entity-based, not query-based (DOCS N5 lists "Service map" as untyped; it needs entity guids) |
| traces | none | no | trace views are not dashboard widgets from NRQL |
| flamegraph | none | no | |
| canvas | none | no | free-form layout |
| xychart | none (no scatter chart in the chart list, DOCS N2 list read for names only) | no | UNVERIFIED |
| candlestick | none | no | |
| trend | `viz.line` | possible | trend panel plots against a non-time x field; only valid when x is time |
| annolist, debug, live, polystat and other plugins | none | no | |

### 4.2 Options mapping

| Grafana option | New Relic | notes |
|---|---|---|
| `unit` | `units.unit` (Terraform `units { unit }`, DOCS N6) and `data_format` on billboard/table (`decimal`, `humanized`, `duration`, ...; DOCS N6) | the accepted unit strings for New Relic are not enumerated in the pages read, so a unit mapping table (`bytes`, `percent`, `ms`, `s`, `reqps`, ...) needs one sandbox check. UNVERIFIED which strings are valid |
| `decimals` | `data_format` type `decimal` (billboard, table) | not for line charts |
| `min`, `max` | `y_axis_left_min`, `y_axis_left_max` on line/area (DOCS N6) | other chart types: none |
| `thresholds.steps` | billboard `critical`/`warning` (one number each); line and table `threshold {name, from, to, severity}` with severity in `warning, severe, critical, success, unavailable` (DOCS N6) | Grafana step colors are free (`green`, `#FF0000`); map by color family: green -> success, yellow/orange -> warning, red -> critical. A step with `value: null` is the base. Percentage mode has no equivalent |
| `mappings` (value mappings) | none | New Relic has no value-to-text mapping on widgets in the pages read (UNVERIFIED); `if(condition, a, b)` or `FACET CASES` in NRQL can reproduce a few (DOCS N1 lists `if()`; `FACET CASES` is named in the N1 text) |
| `overrides` | `colors.series_overrides`, `units.series_overrides`, `null_values.series_overrides`, `y_axis_right_series` (DOCS N6) | only color, unit, null handling and right axis by series name; `byRegexp`, `byFrameRefID`, `custom.*` properties: lost |
| `custom.stacking.mode` | area/stacked bar | `normal` -> `viz.area` stacked? whether `viz.area` stacks is UNVERIFIED; `percent` stacking: lost |
| `custom.fillOpacity` | `viz.line` vs `viz.area` | 0 -> line; above 0 -> area is a guess |
| `custom.lineInterpolation` | `chart_styles.line_interpolation` (`linear, smooth, stepBefore, stepAfter`; line, area, billboard, DOCS N6) | direct |
| legend `showLegend`, placement, calcs | `legend_enabled` (DOCS N6) | placement and calcs (mean, max, last in the legend table) are lost; many community dashboards use the legend as a table with calcs (not counted) |
| tooltip mode | `tooltip.mode` `all|single|hidden` (DOCS N6) | direct for area, line, stacked bar |
| `custom.axisPlacement`, log scale | none (right axis by series only) | lost |
| `spanNulls`, `nullValueMode` | `null_values.null_value` `default|remove|preserve|zero` (DOCS N6) | approximate |
| `color.mode`, fixed color | `colors.color`, `series_overrides` (DOCS N6) | palette modes: lost (New Relic palette) |
| `links` (panel links) | widget `link` (one URL, http/https, 2,048 chars; DOCS N5) | Grafana panel links often use `${__url_time_range}` or `var-` parameters that are meaningless in New Relic: drop with a note |
| `description` | widget `description` (tooltip text; DOCS N5) | direct |
| `transformations` | none | see 4.3 |
| `repeat` (panels) | none | see 4.4 |
| `transparent`, `title` alignment, `pluginVersion`, `interval`, `maxDataPoints` | none | `interval` / `maxDataPoints` guide `TIMESERIES` (section 5.4) |
| `timeFrom`, `timeShift`, `hideTimeOverride` (panel time override) | `SINCE ... UNTIL` / `COMPARE WITH` in the query, or `ignore_time_range` (DOCS N6) | `timeShift` -> `COMPARE WITH`/`SINCE` offset; fixed relative range -> `SINCE x ago` with `ignore_time_range = true`; UNVERIFIED how the two interact |

### 4.3 Transformations that exist in NRQL

Grafana transformations run in the browser on the returned frames (DOCS D13, page read). A transformation cannot be reproduced after the fact; it has to be re-expressed in the query or dropped.

| transformation id | NRQL |
|---|---|
| `organize` (hide, rename, reorder fields) | rename with `AS`; hiding columns: select only what is needed; order is the SELECT order |
| `merge` | one query with several `SELECT` items; NRQL has no UNION that I found (UNVERIFIED); otherwise separate widgets |
| `filterByValue`, `filterFieldsByName` | `WHERE`, `SELECT` list; value filters after aggregation: `HAVING` is not in NRQL (UNVERIFIED), use a subquery `FROM (SELECT ...) WHERE` (subqueries exist, DOCS N1: "joined subquery... default LIMIT 10, max 5,000") |
| `reduce`, `calculateField` | `SELECT` arithmetic; `FACET` instead of reduce rows to columns |
| `seriesToRows`, `labelsToFields`, `joinByField`, `groupBy`, `sortBy`, `limit` | `FACET`, `ORDER BY`, `LIMIT`; join: NRQL `JOIN` exists (DOCS N1 mentions subquery JOIN) but is not suited to a mechanical conversion |
| `timeSeriesTable`, `partitionByValues`, `prepareTimeSeries`, `convertFieldType`, `extractFields`, `renameByRegex`, `rowsToFields`, `histogram`, `config from query`, ... | none; manual |

Rule for the converter: any panel with a non-empty `transformations` list is a failed widget with a reason, except `organize` whose only effect is renames and hidden columns, which maps to `AS` and column selection. In the sample 50 of 1,404 panels have transformations (MEASURED).

### 4.4 Repeating panels, rows

`repeat` on a panel (or a row) draws one copy per selected value of a multi-value variable (the Grafana docs page on repeats was not read; `maxPerRow` and the removal of saved clones come from CODE C1 v17.go and v27.go). New Relic has no repeat. Options: convert one widget with `FACET` on the variable's label (so every selected value is a series), or expand into N widgets for the current default values (stale as soon as values change). Prefer the first, mark `approximate`. Saved clones (`repeatPanelId`, `repeatIteration`) must be dropped (CODE C1 v27.go). 5 of 69 sampled dashboards and 30 of 1,404 panels repeat (MEASURED).


## 5. Queries

### 5.1 What a target looks like, per data source

Field frequencies below are MEASURED over every `targets[]` entry in the public dashboards of section 8: the 69 community dashboards (plus the duplicate Node Exporter Full, grafana.com 12486, which is in the key counts but not in the coverage numbers) and the 27 data-source dashboards that ship in grafana/grafana (24 Azure Monitor, 2 Graphite, 1 test data). The counts are of targets, not panels. The data source is taken from the target's own `datasource`, else the panel's, else guessed from the target's keys. The key lists are what occurs, not a schema.

| data source (`type`) | targets | keys seen (count in the sample) | converts to NRQL |
|---|---|---|---|
| prometheus | 2,050 | `expr` 2050, `refId` 2050, `legendFormat` 1920, `format` 1289 (`time_series` 949, `table` 131, `heatmap` 4, absent 770), `intervalFactor` 1254, `step` 1162, `interval` 1118, `editorMode` 548 (`builder`/`code`), `hide` 520, `range` 474, `instant` 364, `exemplar` 332, `datasource` 432 | partly, section 5.2 |
| loki | 39 | `expr`, `refId`, `legendFormat`, `instant`, `range`, `queryType`, `direction`, `maxLines`, `editorMode` | partly, section 5.3 |
| cloudwatch | 117 | `namespace`, `metricName`, `dimensions{}`, `statistic` (or `statistics[]` in old files), `period`, `region`, `alias`, `expression`, `id`, `returnData`, `matchExact`, `metricQueryType`, `metricEditorMode`, `label` | possible by reuse of the CloudWatch alert path, section 5.6 |
| elasticsearch | 80 | `query` (Lucene string), `metrics[{type,field,id}]`, `bucketAggs[{type,field,id,settings}]`, `timeField`, `alias` | no (different data model; Lucene) |
| influxdb | 108 | `measurement`, `select[[{type,params}]]`, `groupBy`, `tags`, `policy`, `resultFormat`, `orderByTime`, `alias`, or `rawQuery:true` with `query` (InfluxQL) | no |
| mysql, postgres, mssql, `sql` | 162 | `rawSql`, `format` (`time_series|table`), `rawQuery`, `editorMode`; builder fields `table`, `timeColumn`, `metricColumn`, `select`, `where`, `group` | no (SQL against a database that is not in New Relic) |
| grafana-azure-monitor-datasource | 300 | `queryType`, `subscription(s)`, `azureMonitor{resourceUri, metricNamespace, metricName, aggregation, dimensionFilters, timeGrain}`, `azureLogAnalytics{query}` (KQL), `appInsights`, `azureResourceGraph`, `insightsAnalytics` | no |
| stackdriver (Google Cloud Monitoring) | 15 | `metricType`, `filters[]`, `groupBys[]`, `perSeriesAligner`, `crossSeriesReducer`, `alignmentPeriod`, `projectName`, `aliasBy`, `metricKind`, `valueType` | possible by reuse of the GCP path, section 5.6 |
| tempo | 9 | `queryType` (`traceql`, `nativeSearch`, ...), `query`, `filters`, `limit`, `tableType` | no |
| graphite | 140 | `target` (a function string), `textEditor`, `refCount` | no |
| grafana-testdata-datasource | 15 | `scenarioId`, `stringInput`, ... | no (test data; skip, report) |
| `-- Grafana --` (`grafana`) | | `queryType` (`randomWalk`, `live`, `list`, `snapshot`, `timeRegions`, annotations) | no |
| `-- Dashboard --` (`dashboard`) | 82 | `panelId`, `refId` only: reuses another panel's result | convertible only if the source panel converts; then copy its NRQL |
| `-- Mixed --` | | panel `datasource.uid = "-- Mixed --"`; each target has its own `datasource` | per target; a widget holds queries from one NRQL space, so convert only when every visible target converts (New Relic allows several `nrqlQueries` in one widget, DOCS N5) |
| `__expr__` (server-side expressions) | 5 | `type` (`math`, `reduce`, `resample`, `classic_conditions`, `threshold`, `sql`), `expression`, `reducer`, `settings` | math over converted refs can become an NRQL expression only when both refs are one event and one facet set; otherwise no. REPO `grafana/translate.py` has `EXPRESSION_UIDS` and a vocabulary for these on the alert side |

The `hide` key appears on 520 Prometheus targets (its value was not counted). A hidden target (`hide: true`) can be an input to an `__expr__` expression or a Mixed panel and must not be drawn as a series.

Where the v2 schema carries a query, the same fields sit under `spec` of the `DataQuery` (CODE C2; devenv shows `spec: {scenarioId, seriesCount, ...}` for test data). The fixture `v2.json` in section 9.5 shows a Prometheus query as `spec: {expr, instant, legendFormat}`; that is how the plugin keeps its own model in v2 (the CUE says `spec: [string]: _`), but a live export is needed to confirm (UNVERIFIED).

### 5.2 Prometheus: rules, extending the alert translator

REPO `src/importer/sources/grafana/translate.py` today accepts an instant selector with matchers and one optional `avg|sum|min|max (by ...)`, rejects names ending `_total`, `_count`, `_sum`, `_bucket` (counters), and rejects `offset`, subqueries, vector matching, `topk`, `histogram_*`, `absent`, `*_over_time`, `rate`/`increase` and all binary operators with a fixed reason each (`PROM_FEATURES`). It builds a `MetricSignal` with `terms`, `aggregation`, `scope` (tag filters) and `group_by` (REPO `core/ir.py`), and `core/emit/nrql.py` turns that into `SELECT <agg>(<attr>) FROM Metric WHERE ... FACET ...`, with the `AS` alias and `TIMESERIES <n> SECONDS` for dashboards (`widget_nrql`). Reuse as is: `_skeleton`, `_outer`, `_matchers`, `_tag_filter`, `_attribute`, `identifier`, `quote_string`, the counter-suffix check and the `PROM_FEATURES` rejections for what stays manual.

Rules to add for dashboards, in order of how much of the sample they unlock (the tiers T1 to T3 are the ones used in section 8):

| PromQL shape | NRQL | evidence | tier |
|---|---|---|---|
| `metric{...}` gauge selector, optional `avg|sum|min|max (by (l))`, range query | `SELECT average|sum|min|max(metric) FROM Metric WHERE ... FACET l TIMESERIES <bucket>` | REPO (alert translator and `widget_nrql`) | T1 |
| `sum by (l) (rate(counter[w]))`, `irate`, `increase` | `SELECT rate(sum(counter), 1 second) FROM Metric WHERE ... FACET l TIMESERIES AUTO`; `increase(c[w])` -> `rate(sum(c), w)`, or `sum(c)` without TIMESERIES when `w` is `$__range` | NRQL `rate(function, interval)` is documented (DOCS N1). The statement that New Relic stores Prometheus counters as deltas, so `sum(c)` is the increase, comes from the other session's note and was not re-read: UNVERIFIED. The example `sum(rate(http_requests_total[1m]))` -> `rate(sum(http_request_total), 1 second) TIMESERIES 1 minute` is from the same note (UNVERIFIED; note the metric name in it differs, `_total` vs none) | T2 |
| `histogram_quantile(q, sum by (le [, l]) (rate(x_bucket[w])))` | `SELECT bucketPercentile(x_bucket, <100*q>) FROM Metric FACET l`, with `TIMESERIES` for charts | DOCS N1: `bucketPercentile` is "the NRQL equivalent of the histogram_quantile function", "returns the percentile, which is the quantile * 100", takes the bucket metric name ending `_bucket`, up to several percentiles per call | T2 |
| `topk(n, <T1/T2 expression>)` | same query plus `LIMIT n` after the `FACET` | DOCS N1: the first aggregation orders the facet and `LIMIT` applies (default 10, `LIMIT MAX` exists). Ranking is over the whole range, not per step: approximate | T2 |
| arithmetic between convertible terms and numbers (`1 - avg(rate(idle)) ...`, `a / b`, `* 100`, `* 8`) | `SELECT (1 - rate(average(idle), 1 second)) ...` / ratio of two aggregations on the same event and facets | REPO `MetricSignal.expression` with `Num`, `Ref` and `+ - * /` (exists); division by zero gives null instead of NaN | T3 |
| matcher `=~"a|b"`, `=~"api.*"` | `IN ('a','b')`, `LIKE 'api%'` | REPO (alert translator) | T1 |
| any other matcher regex, `=~"^k8s_.*"`, `=~"ext.*|xfs"`, `=~".+"` | `attr RLIKE 'k8s_.*'` (RE2, anchored by default, same as Prometheus) ; `.+` -> `attr IS NOT NULL` | DOCS N1 (`RLIKE`, RE2, "Regex defaults to full-string matching"). The alert translator's remediation text suggests RLIKE for hand fixes and the grafana-phase0 proposal asks to test it first (REPO `remediation_rules.py`, `docs/improvement/proposals/2026-11-09-grafana-phase0.md` line 104). Prometheus regexes are RE2 and anchored: UNVERIFIED here (not fetched) but widely known | T1 (63 of 969 regex matchers in the sample, 43 of 630 convertible panels, are not plain words) |
| `=~"$var"` with a multi-value variable | `attr IN ({{var}})`, variable of type `nrql` or `enum` with `isMultiSelection` | DOCS N3 (multi-value requires `IN`) | T1 |
| `count(x)`, `count by (l) (x)` | `uniqueCount`? not equivalent: counts series. `count(*)` of samples is wrong | none | T3 or manual |
| nested aggregation `avg(sum by (i,c) (rate(..))) by (i)` | NRQL subquery `FROM (SELECT ...)` | subqueries exist (DOCS N1) but I have no evidence for the exact form on `Metric` with `TIMESERIES` | manual |
| `... == 1`, `> 0`, `and`, `or`, `unless`, `bool` | none for series filtering (a comparison filters series; NRQL has no post-aggregation filter that I found) | UNVERIFIED | manual |
| `on()/ignoring()/group_left` | none | | manual |
| `label_replace`, `label_join` | `capture()` in `FACET` can derive a value, but this is a rewrite | DOCS N1 (`capture()` in FACET) | manual |
| `offset 1d` | `COMPARE WITH 1 day ago` or `SINCE/UNTIL` | DOCS N1 | manual |
| `predict_linear`, `deriv` | `predictLinear(attr, interval)`, `derivative(attr, interval)` | DOCS N1 | later |
| `*_over_time` | `average/max/min/sum` per `TIMESERIES` bucket, optionally `SLIDE BY` | DOCS N1 (`SLIDE BY` exists; it cannot take a variable, DOCS N3) | later |
| `absent`, `delta`, `changes`, `resets`, `time()`, `round`, `clamp_*`, `sort*`, `scalar`, `vector` | `clamp_max/clamp_min` exist in NRQL (DOCS N1 line "clamp_max() ..."); others none | | manual |
| raw counter selector without `rate`, e.g. `node_network_receive_bytes_total` | New Relic counters are stored differently (see above); a raw cumulative graph has no equal | REPO keeps these manual | manual (25 panels in the sample) |

Rules that matter for correctness (all tested only on paper, none against New Relic):

- Metric and label names: the alert translator passes names through `identifier()` and notes that the New Relic names and attribute names are not confirmed (REPO `N_METRIC`). The same applies here; dashboards need the same data check that GCP got (REPO `core/data_check.py`).
- The default `FACET` limit is 10 (DOCS N1); Prometheus shows every series. Emit `LIMIT MAX` on faceted widgets (DOCS N1 shows `LIMIT MAX` syntax; its numeric ceiling for charts is UNVERIFIED here, the other session's note says 5,000). A `FACET` takes at most 5 attributes (DOCS N1), so `by (a,b,c,d,e,f)` fails.
- Instant queries (`instant: true` or `format: table`): no `TIMESERIES`; billboard, pie, table take one value per series (REPO `TIMESERIES_VISUALIZATIONS` already encodes this).
- `$__range` inside `[...]` of an instant stat: `increase(x[$__range])` becomes `SELECT sum(x)` with no `TIMESERIES`, because billboards use the dashboard time picker (DOCS N3 on the time picker; the equivalence itself is UNVERIFIED).
- Prometheus ranges and stepping: a panel `interval` / `maxDataPoints` / target `step` shape the step; section 5.4 for the bucket.

### 5.3 Loki and LogQL

REPO accepts `count_over_time({k="v"} |= "text" [5m])` optionally in `sum by (...)`, mapping to `count(*) FROM Log WHERE ... AND message LIKE '%text%'` (REPO `_loki`, `LOKI_COUNT`). For dashboards add:

- `rate({...} |= "x" [w])` -> `rate(count(*), 1 second)` (DOCS N1 `rate(function, interval)`; `count(*)` as the argument is in the N1 example `rate(count(*), 10 minute)`), `TIMESERIES AUTO`.
- `sum by (l) (...)` -> `FACET l` (label names must exist as `Log` attributes; the owner's shipper decides; UNVERIFIED).
- A logs panel (raw lines) -> a log table or `viz.table`: `SELECT * FROM Log WHERE ... LIMIT 200`. `maxLines` -> `LIMIT`; `direction` -> `ORDER BY timestamp`. Log widgets exist in Terraform as `widget_log_table` (DOCS N6).
- Parser stages (`| json`, `| logfmt`, `| pattern`, `| regexp`, `| line_format`, `unwrap`): 4 of 39 sampled targets use them. `aparse()` and `capture()` exist in NRQL (DOCS N1) and can replace some `| pattern`/`| regexp`; treat as later work, manual now.
- Line filters `|=`, `!=`, `|~`, `!~` -> `LIKE`/`NOT LIKE`/`RLIKE`/`NOT RLIKE` (REPO for the first two; RLIKE per DOCS N1).
- Of 39 Loki targets in the sample 15 do not fit the count/rate shape ("loki shape" in the measurement).

### 5.4 Time series bucketing: `TIMESERIES AUTO` or fixed

- NRQL: `TIMESERIES <n> <unit>`, `TIMESERIES AUTO` (a week is split into 6-hour buckets, a day into 30-minute buckets in the example), `TIMESERIES MAX` (the largest allowed number of buckets, 366) (DOCS N1). The variable list in New Relic dashboards does not accept variables in `TIMESERIES` or `SLIDE BY` (DOCS N3).
- Grafana: `$__interval` is derived from the time range and the panel width (about one point per pixel, bounded by `maxDataPoints`, floored by the `interval`/min step setting); `$__rate_interval` is `max($__interval + scrape_interval, 4 * scrape_interval)` (DOCS D10); `$__range` is the dashboard range (DOCS D10).
- Rule: use `TIMESERIES AUTO` unless the target or panel sets a fixed `interval` (for example `1m`, from `target.interval` or `panel.interval`) and no `$__` placeholder; then emit that fixed bucket (rounded to a unit New Relic accepts). `intervalFactor` and `step` (present in 1,254 and 1,162 Prometheus targets, almost always legacy defaults) are ignored. A fixed bucket is also what the IR carries today: `Series.period_seconds` is an integer from 1 to 86,400 and `widget_nrql` always writes `TIMESERIES <n> SECONDS` (REPO `dashboard_ir.py`, `emit/nrql.py`). AUTO needs a nullable period.
- `rate(x[$__rate_interval])` keeps its per-second meaning when written as `rate(sum(x), 1 second)`; the smoothing window of Prometheus (4 scrapes) becomes the New Relic bucket size, which is a visible difference on spiky counters: approximate.
- 366 buckets is the TIMESERIES ceiling (DOCS N1); a month at 1-minute buckets does not fit. Fixed small buckets must be raised by the emitter, not left to fail.

### 5.5 Legend format to `AS`

`legendFormat` is a template with `{{label}}` placeholders (and sometimes `$var`). MEASURED over 1,854 Prometheus and Loki targets that have an `expr`:

| form | count | NRQL |
|---|---|---|
| empty or absent | 288 (16%) | none; New Relic labels a series by its facet value or the select expression |
| static text, no `{{` | 908 (49%) | `AS 'text'`; the repository already writes `AS` with the label cut to 100 characters through `quote_string` (REPO `emit/nrql.py`, `MAX_ALIAS`). Variables are not allowed in `AS` (DOCS N3) |
| exactly one `{{label}}` | 304 (16%) | drop; the facet attribute is the legend |
| text plus `{{labels}}` | 349 (19%) | no equivalent; keep the facet and lose the text, note it |
| contains `$var` | 5 (under 1%) | drop the variable |

When one query has several series (several targets, each a refId), each target keeps its own `AS`; Grafana's `refId` letters are not shown.

### 5.6 CloudWatch and Google Cloud Monitoring: bridge to the existing sources

The repository already converts CloudWatch dashboards and Google Cloud Monitoring dashboards into the same IR (REPO `sources/cloudwatch/dashboards.py`, `sources/gcp/dashboards.py`). A Grafana panel on those data sources carries the same facts under other names:

- CloudWatch: `namespace`, `metricName`, `dimensions` (map), `statistic`, `period`, `region`. The CloudWatch dashboard translator calls `_resolve_term` of the alert path with these to pick the New Relic attribute (REPO `cloudwatch/dashboards.py` header comment). An adapter from a Grafana target to that input is the cheapest way to cover 56 panels of the sample (4%). Metric math (`expression`; 91 of 117 targets carry `expression` and `id` keys, their values were not inspected) stays manual. Whether the data is in New Relic at all (Metric Streams or the polling integration) is the owner's setup, not a conversion matter.
- Google Cloud Monitoring: `metricType`, `filters` (flat list of key, operator, value, condition), `groupBys`, `perSeriesAligner`, `crossSeriesReducer`, `alignmentPeriod`. The GCP translator takes a `timeSeriesFilter` body with `filter` and `aggregation{alignmentPeriod, perSeriesAligner, crossSeriesReducer, groupByFields}` and has shared helpers (`metric_scope`, `check_aligner`, `metric_series`) (REPO `gcp/dashboards.py`). The two shapes are close; an adapter is S to M. The GCP path also raises the period to 300 seconds because New Relic polls every 5 minutes (REPO `POLL_NOTE`).
- Azure Monitor: no source exists in the repository; 44 panels in the sample plus 281 in the bundled Azure dashboards.

## 6. Variables

Counts in this section are MEASURED over 227 variables in the 69 community dashboards (query 159, datasource 28, interval 18, custom 13, textbox 6, constant 3); 47 are multi-value (21%), 71 have include-all (31%), 26 have a regex filter (11%). Another 241 variables are in the 27 bundled Azure Monitor dashboards (query 187, datasource 31, custom 22, constant 1).

New Relic dashboard variables (DOCS N3, N6, N5):

- types `nrql` (a query returning a list, using `uniques()` or `keyset()`; may run against several accounts; option "ignore time picker"), `enum` (fixed items with a title and a value), `string` (a text field; no multi-value);
- fields `name`, `title`, `isMultiSelection`, `defaultValues` (`["*"]` selects all), `replacementStrategy` (`default`, `identifier`, `number`, `string`), `options{excluded, ignoreTimeRange, showApplyAction}` and `hiddenOnVariablesBar` (DOCS N3, N6);
- use `{{name}}` in a widget query inside `SELECT`, `FROM`, `FACET`, `ORDER BY`, `WHERE`; not allowed in `AS`, `COMPARE WITH`, `LIMIT`, `OFFSET`, `SINCE`, `SLIDE BY`, `TIMESERIES`, `UNTIL`, `WITH`; multi-value requires `IN`; the title and description of a widget can show the chosen value; the PDF export does not support widgets with variables; variables work only on custom dashboards (DOCS N3);
- nested variables (a variable query that uses another variable) exist for Pro, Enterprise and Core Compute customers only, with the limitations that the parent needs selected values and deleting the parent breaks the child (DOCS N4).

Mapping:

| Grafana variable | New Relic | notes |
|---|---|---|
| `query` with Prometheus `label_values(metric, label)` | `nrql` variable: `FROM Metric SELECT uniques(label) WHERE metricName = 'metric'` | the `metricName` form is from the other session's note (UNVERIFIED); needs the data check |
| `query` with `label_values(label)`, `label_names()`, `metrics(..)`, `query_result(..)` | `uniques(label)` without `metricName`; `keyset()` for `label_names`; `query_result` and `metrics` manual | |
| `query` that references another variable (`label_values(kube_pod_info{service=~"$service"}, pod)`) | a nested variable (DOCS N4), else manual | chained variables are common in Kubernetes dashboards |
| `query` with `regex` (JS regex, `/.../flags`) | `WHERE attr RLIKE '...'` in the variable query | JS and RE2 differ (lookahead such as `/^(?!canary-).*/` is not RE2): manual when it has `(?!`, `(?<`, backreferences |
| `query` against Loki, CloudWatch (`dimension_values(...)`), Elasticsearch, SQL | manual or drop | |
| `custom` (`a,b,c`, or `a : 1, b : 2`, or JSON in v2 with `valuesFormat`) | `enum` with items | direct |
| `constant` (and schema 27 turns it into `textbox`) | substitute the value into the queries, no variable | hidden constants (`hide: 2`) are used for fixed prefixes |
| `textbox` | `string` variable | no multi-value |
| `interval` (with `auto`, `auto_count`, `auto_min`) | drop; pick `TIMESERIES AUTO`, or the variable's current value as a fixed bucket | variables cannot be used in `TIMESERIES` (DOCS N3) |
| `datasource` | drop; all panels use the one New Relic account (or `accountIds`, DOCS N5) | the variable's `query` (`"prometheus"`) tells the type of every panel that uses `${datasource}` (CODE C2 `DatasourceVariableSpec.pluginId`); 28 of 227 variables in the sample are datasource variables, so type resolution through the variable is needed |
| `adhoc` (and v2 `GroupByVariable`) | no ad hoc filter in the pages read; a group-by can become a `string` variable with `identifier` strategy used in `FACET {{x}}` (DOCS N3 shows a variable after `FACET`) | UNVERIFIED for ad hoc |
| `switch` (v2) | `enum` with two items | |
| multi-value, include-all | `isMultiSelection = true`, `defaultValues = ["*"]` (DOCS N6); queries use `IN ({{v}})` | `allValue` (a custom regex such as `.*`) has no use: when all values are selected, the filter should be dropped; the variable option `excluded` drops the condition (DOCS N6 `excluded`) |
| `refresh` 1 (on load) / 2 (on time range change) | `ignoreTimeRange` true / false (DOCS N3 "Ignore time picker") | approximate |
| `sort`, `skipUrlSync`, `hide`, `label`, `description` | `title`; `hide: 2` -> `hiddenOnVariablesBar` (DOCS N3); others dropped | |

Interpolation syntax (DOCS D9, D10):

| Grafana | meaning | New Relic |
|---|---|---|
| `$name`, `${name}` | value, single or multi | `{{name}}` |
| `${name:format}` | formats `csv`, `pipe`, `regex`, `glob`, `json`, `queryparam`, `doublequote`, `singlequote`, `sqlstring`, `raw`, `text`, `distributed`, `percentencode`, `lucene`, `join:<sep>` (the page lists these; I read csv, distributed, doublequote, glob, join, json in the part I read) | the format decides quoting; New Relic quoting comes from `replacementStrategy` and `IN (...)`; manual when a format other than the default is used |
| `[[name]]` | deprecated syntax (DOCS D9) | same as `$name` |
| `$__interval`, `$__interval_ms`, `$__rate_interval`, `$__range`, `$__range_s`, `$__range_ms` | built-ins (DOCS D10) | no variable; section 5.4 |
| `$__from`, `$__to`, `$__timeFilter(col)`, `$__dashboard`, `$__org`, `$__user` | built-ins / SQL macros | UNVERIFIED (not read); drop. Only meaningful in links and SQL |
| `${__url_time_range}`, `${__value.raw}`, `${__field.name}`, `${__series.name}` | data-link variables | drop with the link |
| `$var` in a Prometheus label matcher | regex interpolation: multi-value becomes `(a|b)` automatically for Prometheus (DOCS D9: "variables in InfluxDB or Prometheus regular expression queries are automatically escaped") | `IN ({{var}})` |

Observed in the sample (MEASURED, targets with `expr`, n = 1,854): `$__rate_interval` 229, `[$custom_interval]` such as `$duration`, `$interval` 211, `$__interval` 30, `$__range` 6, any other `$var` 1,659 (89%), `[[var]]` 0, `${var:fmt}` 0.

Limits: no variable in `AS`, `TIMESERIES`, `SINCE`, `LIMIT`; so a legend with a variable loses the variable, a panel that sets a window from a variable gets a fixed one, and all dashboards that have a `$interval` variable change meaning. The maximum number of variables and the allowed name characters are not in the pages read (UNVERIFIED); sanitise names to letters, digits and underscore.

## 7. Dashboard-level objects

### 7.1 Annotations

Classic: `annotations.list[]`; entries other than the built-in one hold a `datasource`, a query (`expr` for Prometheus and Loki, `target` for the Grafana source, `query` for SQL or Elasticsearch), `iconColor`, `enable`, `hide`, `name`, optional `titleFormat`, `textFormat`, `tagKeys`, `step` (the legacy sample entry in section 9.4 shows the Prometheus form). v2: `annotations[]` of kind `AnnotationQuery` with `query: DataQuery`, `mappings`, `filter`, `placement` (CODE C2). In the sample 7 of 69 dashboards have a non-built-in annotation query (MEASURED).

New Relic: no dashboard annotation layer in the pages read (DOCS N5, N6, N2). Drop all annotation queries and list them in the report (name, data source type, enabled). Do not carry the query text into a report cell without `md_cell`. Deploy markers can be reproduced as a `FACET` or by the change-tracking feature of New Relic, which I did not research (UNVERIFIED).

### 7.2 Links

`links[]` (dashboard links): `dashboards` (by tag) and `link` (URL) types with `includeVars`, `keepTime`, `asDropdown` (DOCS D1 example). New Relic has a per-widget `link` (one http or https URL, up to 2,048 characters, DOCS N5) and markdown widgets; there is no dashboard link bar in the pages read. Convert URL links to a markdown widget at the top (escaped), or to the widget `link`; drop `dashboards` links by tag (they target other Grafana dashboards, which will not exist under the same name). Panel `links` and data links: widget `link` only when the URL has no `${__...}` macros and no `var-` parameters; 15 of 1,404 panels have panel links in the sample (MEASURED).

### 7.3 Rows, collapsing, tabs

- A row is a panel of `type: "row"` with `collapsed` and, when collapsed, the member panels inside `panels[]` (CODE C1 v16.go builds them); a row can repeat on a variable. In v2, `RowsLayout` rows have `collapse`, `hideHeader`, `repeat`, `variables`, `annotations`; `TabsLayout` tabs have `title`, `repeat`, `variables`, `annotations` (CODE C2).
- New Relic: a dashboard has `pages[]`, each with `widgets[]` (DOCS N5, N6); there is no collapsible group. Options: (a) one page, a markdown heading widget (full width) for every row title, members below it; collapsed rows are expanded because the state cannot be kept; (b) one page per top-level row or tab. Choose (a) for classic rows (they are visual sections) and (b) for v2 tabs (a tab is a page), and pack rows by vertical order. The GCP converter has a `pack()` that moves overlapping widgets down, which is reusable (REPO `gcp/dashboards.py` `pack`). The widget cap per dashboard is `MAX_WIDGETS = 200` there (REPO `gcp/dashboard_models.py`); New Relic's own limit is not in the pages read (UNVERIFIED). In the sample, 36 of 69 dashboards have rows (174 rows, 50 collapsed) (MEASURED); nested rows do not exist in the classic model, but nested layouts do in v2 (RowsLayout in TabsLayout in RowsLayout...) so the flattening must be depth-bounded.
- Row-level `repeat` (a row per value) -> not supported; expand once with the default values or fold into `FACET`; mark it.

### 7.4 Time range, refresh, time zone

- `time{from,to}` (default range; `now-6h`), `timepicker` (hidden, allowed intervals, `nowDelay`), `refresh` (`30s`), `timezone`, `fiscalYearStartMonth`, `weekStart`. New Relic widgets have a per-widget `refresh_rate` (values in the Terraform doc: `auto`, `0`, `5000`, `30000`, `60000`, `300000`, `1800000`, `3600000`, and more, in milliseconds; DOCS N6), and `ignore_time_range` for a widget fixed to its own `SINCE` (DOCS N6). The dashboard default time range: I found no field for it in the NerdGraph/Terraform pages read, so a default of `now-6h` cannot be stored (UNVERIFIED); put the original range into the dashboard description. Map `refresh` to the nearest allowed `refresh_rate` and record the change.
- Time zone: no field; drop.
- `timeFrom`/`timeShift` on a panel: section 4.2.
- Dashboard `tags` and `description`: put in the description. `editable`, `graphTooltip`, `liveNow`, `preload`: drop. Permissions: created private as for the other sources (REPO `emit/dashboard.py`: `permissions: "PRIVATE"`).


## 8. Coverage estimate

### 8.1 Files used

All fetched by `curl` GET, nothing else. The analysis scripts were run from a scratch folder outside the repository and are not committed (only one new file is allowed for this task); the method is described so it can be re-run.

- Set A, Prometheus-heavy community dashboards, 39 downloaded, 38 analysed (grafana.com ids 2, 315, 405, 893, 1229, 1471, 1860, 3590, 3662, 6417, 6671, 7249, 7362, 9614, 9628, 10000, 11074, 11159, 11600, 11835, 12006, 12708, 12740, 13105, 13332, 13407, 13639, 13770, 14055, 14282, 14584, 15757, 15759, 15760, 16611, 575, 650, 8919; id 12486 is a second copy of Node Exporter Full and is left out of the coverage numbers), via `https://grafana.com/api/dashboards/<id>/revisions/latest/download`. I picked well-known ids from memory and checked the titles after download (8.4); ids 575 and 650 are CloudWatch dashboards and 7362/9628 are database dashboards that use Prometheus exporters. The selection is therefore skewed toward Prometheus exporters and Kubernetes, which is also the likely shape of the owner's stack, but it is not a random sample of all dashboards.
- Set B, data-source diversity, 31 dashboards: the top three per data source from `https://grafana.com/api/dashboards?orderBy=downloads&direction=desc&dataSourceSlugIn=<ds>&pageSize=3` for influxdb, elasticsearch, mysql, postgres, cloudwatch, loki, stackdriver, grafana-azure-monitor-datasource, graphite, tempo and mssql (the API filter returned dashboards whose declared data source matches; some turned out to be Loki dashboards under a graphite tag). Ids are in the table in 8.4.
- Set C, 27 dashboards bundled in `public/app/plugins/datasource/*/dashboards/` of grafana/grafana main (24 Azure Monitor, 2 Graphite, 1 test data), and Set D, the 162 JSON files under `devenv/dev-dashboards` (157 of them v2 resources, 5 classic). Set C and D are used for panel-type and shape statistics only (CODE C5, C7). Set D is test data and says nothing about convertibility: 1,913 of its 2,110 queries are test data.

### 8.2 Method and the three tiers

A heuristic script (about 300 lines of Python: JSON loader with unwrapping, panel flattening including legacy `rows[]` and row-nested panels, data source resolution through `__inputs`, target keys and the panel, and a regex-based PromQL/LogQL classifier) assigns each panel one verdict. A panel converts at a tier when all of these hold: its type is in the convertible set (timeseries/graph, stat/singlestat, table/table-old, piechart, text); it is not a library panel; it has no transformations; and every non-hidden target is Prometheus or Loki and passes the tier's query rule. Text panels convert with no query.

- T1 (conservative): the alert translator's reach. A plain selector with matchers, an optional one-level `avg|sum|min|max (by ...)`, no counters without `rate`, instant or range.
- T2: T1 plus `rate`/`irate`/`increase` inside one aggregation, `histogram_quantile` over `sum by (le ...)(rate(..._bucket))` or `rate(..._bucket)`, `topk`, and Loki `count_over_time`/`rate` with line filters.
- T3: T2 plus arithmetic between convertible terms and numbers, and `count` aggregations.

Tiers measure whether a translation can be written in principle. They do not measure whether the metric exists in New Relic, whether names match, or whether the result looks the same.

### 8.3 Results

Panels (excluding rows):

| set | dashboards | panels | T1 | T2 | T3 | T3 with extra widget types (gauge, bargauge, barchart, heatmap, logs) |
|---|---|---|---|---|---|---|
| A, Prometheus-heavy | 38 | 899 | 283 (31.5%) | 516 (57.4%) | 625 (69.5%) | 644 (71.6%) |
| B, other data sources | 31 | 505 | 23 (4.6%) | 30 (5.9%) | 37 (7.3%) | 41 (8.1%) |
| A + B | 69 | 1,404 | 306 (21.8%) | 546 (38.9%) | 662 (47.2%) | 685 (48.8%) |
| C, bundled Azure Monitor and others | 27 | 573 | 43 (7.5%) | 43 (7.5%) | 43 (7.5%) | |

Excluding the 39 text panels that always pass, A + B is 19.6%, 37.1% and 45.6%. Per dashboard (mean of per-dashboard shares, A + B): T1 18.5%, T2 33.7%, T3 40.8%; median 12.5%, 27.3%, 39.4%. Only 5 of 69 dashboards convert completely at T3, 1 at T2, and 24 dashboards (all in set A, none in set B) have half or more of their panels converting at T2. A concrete conservative claim: at T2, about half of the panels of a typical Prometheus exporter or Kubernetes community dashboard convert, and about 1 in 20 panels of dashboards on any other data source.

The same applies after regex matchers are considered: in the A + B panels that pass T3 on query shape, 43 of 630 contain at least one `=~`/`!~` matcher that is not plain words; with RLIKE (section 5.2) they convert, without it they do not. This is already included in the counts above (they were counted as convertible), so the numbers assume RLIKE works; without RLIKE subtract about 7% of the converting panels.

Why panels do not convert, first cause per panel, A + B (1,404 panels):

| cause | panels | share |
|---|---|---|
| converts at T3 | 662 | 47.2% |
| data source not supported (Elasticsearch, SQL, CloudWatch, InfluxDB, Azure Monitor, Google Cloud Monitoring, Tempo, unresolved `${datasource}`) | 353 | 25.1% |
| PromQL shape or function (comparisons and `and/or`, complex arithmetic, `on()/group_left`, nested aggregation, `time()`, `*_over_time`, `label_replace`, raw counters without `rate`, `offset`, ...) | 225 | 16.0% |
| panel type outside the convertible set (gauge, bargauge, barchart, heatmap, logs, geomap, alertlist, nodeGraph) | 97 | 6.9% |
| transformations | 46 | 3.3% |
| LogQL or other query shape | 21 | 1.5% |

By panel type (A + B), converting at T1/T2/T3:

| panel type | panels | T1 | T2 | T3 |
|---|---|---|---|---|
| graph (legacy) | 568 | 98 | 252 | 303 |
| timeseries | 311 | 58 | 137 | 170 |
| singlestat (legacy) | 176 | 80 | 84 | 109 |
| stat | 107 | 24 | 26 | 32 |
| table | 78 | 5 | 6 | 6 |
| text | 39 | 39 | 39 | 39 |
| bargauge | 36 | 0 | 0 | 0 |
| gauge | 30 | 0 | 0 | 0 |
| table-old | 13 | 2 | 2 | 3 |
| barchart | 13 | 0 | 0 | 0 |
| logs | 9 | 0 | 0 | 0 |
| grafana-piechart-panel | 8 | 0 | 0 | 0 |
| piechart | 7 | 0 | 0 | 0 |
| heatmap | 5 | 0 | 0 | 0 |
| geomap, alertlist, nodeGraph | 4 | 0 | 0 | 0 |

By main data source of the panel (A + B): prometheus 915 panels (268/506/622 at T1/T2/T3), mssql 82, elasticsearch 62, cloudwatch 56, influxdb 53, azure monitor 44, loki 39 (0/2/2), unresolved datasource variable 36, generic SQL 30, postgres 26, no data source 25 (text panels), stackdriver 15, tempo 9, mysql 8, graphite 2, expression 2. For SQL, MySQL and graphite rows the few converting panels are text panels.

Other counts over A + B (MEASURED): 227 variables (section 6); 36 dashboards use rows (174 rows, 50 collapsed); 5 dashboards and 30 panels repeat; 18 dashboards have links; 7 have non-built-in annotation queries; 177 panels have field overrides, 51 have value mappings, 15 have panel links, 50 have transformations; 0 library panels (the downloaded copies come from grafana.com, which only accepts classic JSON, so library panels are inlined; the owner's dashboards may differ, UNVERIFIED).

### 8.4 Per-dashboard table (sets A and B)

`T1`, `T2`, `T3` are the number of converting panels at each tier; `panels` excludes rows. The "main datasource" is the most frequent first data source among the panels.


| grafana.com id | title | schemaVersion | panels | main datasource | T1 | T2 | T3 |
|---|---|---|---|---|---|---|---|
| 2 | Prometheus Stats | 12 | 11 | prometheus | 8 | 10 | 10 |
| 315 | Kubernetes cluster monitoring (via Prometheu | 12 | 21 | prometheus | 9 | 18 | 21 |
| 405 | Node Exporter Server Metrics | 12 | 15 | prometheus | 4 | 5 | 6 |
| 575 | AWS S3 | 40 | 7 | cloudwatch | 1 | 1 | 1 |
| 650 | AWS ELB Application Load Balancer | 40 | 10 | cloudwatch | 1 | 1 | 1 |
| 893 | Docker and system monitoring | 13 | 20 | prometheus | 4 | 8 | 15 |
| 1229 | Docker Engine Metrics | 14 | 9 | prometheus | 2 | 5 | 5 |
| 1471 | Kubernetes App Metrics | 14 | 13 | prometheus | 1 | 3 | 7 |
| 1860 | Node Exporter Full | 41 | 124 | prometheus | 47 | 91 | 103 |
| 3590 | Grafana Internals | 14 | 18 | prometheus | 8 | 10 | 15 |
| 3662 | Prometheus 2.0 Overview | 14 | 30 | prometheus | 9 | 15 | 17 |
| 6417 | Kubernetes Cluster (Prometheus) | 16 | 29 | prometheus | 23 | 23 | 28 |
| 6671 | Go Processes | 16 | 8 | prometheus | 4 | 5 | 5 |
| 7249 | Kubernetes Cluster | 16 | 15 | prometheus | 1 | 6 | 7 |
| 7362 | MySQL Overview | 16 | 36 | prometheus | 6 | 7 | 8 |
| 8919 | Node Exporter Dashboard 20240520 TenSunS???? | 39 | 21 | prometheus | 1 | 6 | 11 |
| 9614 | NGINX Ingress controller | 16 | 12 | prometheus | 2 | 5 | 7 |
| 9628 | PostgreSQL Database | 19 | 32 | prometheus | 15 | 21 | 23 |
| 10000 | Cluster Monitoring for Kubernetes | 16 | 13 | prometheus | 6 | 10 | 13 |
| 11074 | Node Exporter Dashboard EN 20201010-StarsL.c | 26 | 24 | prometheus | 3 | 9 | 15 |
| 11159 | NodeJS Application Dashboard | 18 | 9 | prometheus | 6 | 6 | 7 |
| 11600 | Docker Container | 21 | 9 | prometheus | 3 | 6 | 9 |
| 11835 | Redis Dashboard for Prometheus Redis Exporte | 21 | 12 | prometheus | 4 | 9 | 11 |
| 12006 | Kubernetes apiserver | 22 | 6 | prometheus | 0 | 5 | 5 |
| 12708 | NGINX exporter | 18 | 4 | prometheus | 2 | 4 | 4 |
| 12740 | Kubernetes Monitoring Dashboard | 22 | 21 | prometheus | 9 | 18 | 21 |
| 13105 | K8S Dashboard CN 20240513 StarsL.cn | 39 | 33 | prometheus | 3 | 5 | 13 |
| 13332 | kube-state-metrics-v2 | 26 | 42 | prometheus | 26 | 26 | 31 |
| 13407 | Loki2.0 Global Metrics | 26 | 39 | prometheus | 8 | 31 | 31 |
| 13639 | Logs / App | 26 | 2 | loki | 0 | 1 | 1 |
| 13770 | 1 Kubernetes All-in-one Cluster Monitoring K | 25 | 24 | prometheus | 4 | 10 | 12 |
| 14055 | Loki stack monitoring (Promtail, Loki) | 26 | 18 | prometheus | 3 | 9 | 11 |
| 14282 | Cadvisor exporter | 27 | 6 | prometheus | 2 | 4 | 5 |
| 14584 | ArgoCD | 30 | 41 | prometheus | 17 | 32 | 32 |
| 15757 | Kubernetes / Views / Global | 39 | 26 | prometheus | 3 | 8 | 9 |
| 15759 | Kubernetes / Views / Nodes | 39 | 35 | prometheus | 10 | 23 | 28 |
| 15760 | Kubernetes / Views / Pods | 42 | 25 | prometheus | 6 | 12 | 12 |
| 16611 | Cilium v1.12 Agent Metrics | 25 | 79 | prometheus | 22 | 48 | 65 |
| 609 | Docker Swarm & Container Overview | 14 | 20 | prometheus | 2 | 3 | 6 |
| 623 | AWS EBS | 40 | 7 | cloudwatch | 1 | 1 | 1 |
| 878 | Elasticsearch Dashboard | 16 | 55 | elasticsearch | 0 | 0 | 0 |
| 969 | AWS ElastiCache Redis | 40 | 12 | cloudwatch | 1 | 1 | 1 |
| 4164 | Monitoring PostgreSQL Server, Postgres repli | 14 | 26 | influxdb | 0 | 0 | 0 |
| 7991 | 2MySQL  Simple Dashboard | 30 | 18 | sql | 4 | 4 | 4 |
| 8159 | VMware vSphere  - Overview | 40 | 22 | influxdb | 1 | 1 | 1 |
| 8168 | VMware vSphere  - VMs | 39 | 8 | influxdb | 1 | 1 | 1 |
| 9689 | SCCM - Updates | 16 | 12 | mssql | 0 | 0 | 0 |
| 9948 | PostgreSQL Infrastructure | 18 | 16 | postgres | 0 | 0 | 0 |
| 10534 | Azure Redis | 18 | 14 | var-ds | 0 | 0 | 0 |
| 10956 | Azure / Monitor for Containers - Metrics | 37 | 24 | grafana-azure-monitor-datasource | 0 | 0 | 0 |
| 11265 | Amazon EC2 | 21 | 15 | cloudwatch | 2 | 2 | 2 |
| 11679 | 2MySQL  Simple Dashboard 4 MariaDB | 22 | 18 | sql | 4 | 4 | 4 |
| 11720 | Octopus Deploy | 22 | 16 | mssql | 1 | 1 | 1 |
| 12180 | AKS Container Insights | 41 | 3 | grafana-azure-monitor-datasource | 0 | 0 | 0 |
| 12559 | Loki NGINX Service Mesh - JSON version | 38 | 19 | loki | 1 | 1 | 1 |
| 14208 | GCP Stackdriver | 22 | 15 | stackdriver | 0 | 0 | 0 |
| 14361 | AWS ALB Cloudwatch Metrics | 27 | 10 | cloudwatch | 0 | 0 | 0 |
| 14448 | Airflow monitoring | 27 | 6 | postgres | 0 | 0 | 0 |
| 15722 | Google Cloud Spanner - Database dashboard | 33 | 8 | var-ds | 1 | 1 | 1 |
| 16311 | Azure MySQL Flexible Server Monitor | 36 | 19 | grafana-azure-monitor-datasource | 0 | 0 | 0 |
| 16627 | Grafana Composer | 31 | 13 | var-ds | 0 | 0 | 0 |
| 16696 | Kubernetes Node Monitoring | 36 | 7 | elasticsearch | 0 | 0 | 0 |
| 16966 | Container Log Dashboard | 25 | 9 | loki | 0 | 0 | 0 |
| 17175 | Spring Boot Observability | 38 | 11 | prometheus | 0 | 3 | 5 |
| 18042 | Logging Dashboard via Loki v2 | 25 | 10 | loki | 1 | 1 | 1 |
| 20600 | Guardrails Starter Dashboard | 39 | 6 | tempo | 0 | 0 | 0 |
| 21378 | Microsoft SQL Server Dashboard | 40 | 60 | mssql | 1 | 1 | 1 |
| 21587 | OpenTelemetry for HTTP services | 41 | 7 | prometheus | 0 | 2 | 2 |
| 22784 | Lightweight APM for OpenTelemetry | 41 | 19 | prometheus | 2 | 3 | 5 |


### 8.5 Limits of this estimate

- The classifier is regex-based, not a PromQL parser. I read samples of its rejected expressions (section 5.2 table comes from them) and fixed false rejections (spaces before `(`, uppercase aggregators, `$duration` in ranges, a leading minus, `histogram_quantile(q, rate(...))` without `sum`). Residual error is probably a few percentage points either way; a real parser (or the NR PromQL translator when it is tried in the sandbox) would settle it.
- Set A is chosen, not sampled. Public community dashboards skew to older, exporter-style dashboards (40% legacy `graph`; 28 of the 38 in set A have schemaVersion 26 or lower). A modern stack with custom dashboards may use more recording rules, SLO panels, `label_replace`, comparisons and Mimir-specific functions, or fewer.
- The numbers say nothing about whether the metrics exist in New Relic. The alert side has the same caveat (REPO `N_METRIC`).
- Panels that convert may still look different: legends, stacking, units, thresholds, axis settings and colors are partly lost (section 4.2), and the bucket and smoothing behavior of `rate` differs (section 5.4).
- Fidelity is not scored. Under the repository's confidence scale, almost all dashboard widgets would be `approximate`, not `exact` (REPO `core/ir.py` `Confidence`).

## 9. Fixtures to commit later under tests/fixtures/grafana-dashboards/

Five small files. All are hand-written for this task from the public structure documented above; none is exported from a real stack and none contains personal data, hostnames, tokens, e-mail addresses or real project names. Sources of structure: DOCS D1 (envelope and panel example), CODE C2 (v2 CUE), CODE C1 (legacy fields), and the community dashboards listed in section 8 (field names and typical expressions, in the style of Node Exporter Full, grafana.com 1860, and Kubernetes dashboards; no text copied beyond metric names). Each file parses as JSON (checked). The `hostile.json` file is meant to be read by the loader, not by a strict JSON schema: it contains wrong types on purpose. Together they should be paired with a README stating that they are synthetic (the existing `tests/fixtures/grafana/README.md` is the model).

Suggested layout and expected outcomes (to become golden files only after human review, per the project rules):

| file | shape | purpose | expected |
|---|---|---|---|
| `easy-node-overview.json` | classic, schemaVersion 39 | the happy path: datasource variable, multi-value query variable, stat, two timeseries (a `rate` and a gauge), table, text | 5 of 5 widgets convert (T2); variables become one `nrql` variable (`job`), datasource variable dropped with a note |
| `hard-service-mixed.json` | classic, schemaVersion 41 | everything that should fail or degrade: repeated row, collapsed row, library panel, Mixed data source with `__expr__`, `histogram_quantile`, `label_replace`, `topk`, transformations, value mappings, overrides, panel links with macros, CloudWatch, Loki with `| json`, `state-timeline`, `alertlist`, interval/adhoc/constant/textbox variables, Loki annotation | converts: request-rate query A, latency p95/p99, retries. Fails with reasons: query B (label_replace), error ratio (Mixed plus expression), slow endpoints (transformations), logs (parser stage), CloudWatch (until bridged), library panel, state timeline, alert list |
| `hostile.json` | classic, wrong types everywhere | the loader must not crash or leak: injection attempts in titles, tags, variable names, expressions and legends; wrong types; duplicate ids; negative and huge `gridPos`; row inside row inside row; a library panel that does not exist; `null` and array entries in `panels` | loader reports and skips; every emitted string passes `quote_string`, `hcl_string`, `md_cell`; no widget from broken entries |
| `legacy-rows-v12.json` | classic, schemaVersion 12, `rows[]` with `span` | the old layout: `rows`, `span`, singlestat with `valueMaps`, graph with `yaxes`/`seriesOverrides`/`aliasColors`, table with `styles`, `__inputs`, string data source names, repeated row | `rows` are read; singlestat and graph convert (T2); table with `styles` and `transform` fails or degrades; row `repeat` noted |
| `v2-small.json` | `dashboard.grafana.app/v2beta1` resource | the v2 reader: `elements`, `RowsLayout` with `GridLayout` and `AutoGridLayout`, `QueryVariable`, `CustomVariable`, `LibraryPanel` element | 2 widgets convert; layout flattened to one page; library element reported |

Generated, not committed (the tests build them in memory):

- many panels: 10,000 timeseries panels in one dashboard (checks the widget cap, REPO `MAX_WIDGETS`) and 500 dashboards in one extract directory;
- deep nesting: 200 nested `row`/`panels` levels and 200 nested v2 layouts (checks the depth limit that the existing loader applies to rule files, REPO `docs/what-migrates.md` "A file nested too deeply is refused");
- huge strings: a 1 MB title, a 10,000-character `expr` (the alert translator caps `MAX_EXPRESSION` at 10,000, REPO), a 1 MB markdown body;
- many variables (500) and many targets per panel (200);
- numeric edge cases: `gridPos` with 1e308, NaN-like strings, negative `schemaVersion`.

### 9.1 easy-node-overview.json

Derived from the structure of community node dashboards (grafana.com 1860 and others); synthetic.

```json
{
  "uid": "easy-node-overview",
  "title": "Node overview (easy)",
  "tags": ["fixture", "prometheus"],
  "schemaVersion": 39,
  "version": 3,
  "editable": true,
  "graphTooltip": 0,
  "time": { "from": "now-6h", "to": "now" },
  "refresh": "1m",
  "timezone": "browser",
  "annotations": { "list": [ { "builtIn": 1, "datasource": { "type": "grafana", "uid": "-- Grafana --" }, "enable": true, "hide": true, "iconColor": "rgba(0, 211, 255, 1)", "name": "Annotations & Alerts", "type": "dashboard" } ] },
  "links": [],
  "templating": {
    "list": [
      { "name": "datasource", "type": "datasource", "query": "prometheus", "current": { "text": "default", "value": "default" }, "hide": 0, "label": "Data source" },
      { "name": "job", "type": "query", "datasource": { "type": "prometheus", "uid": "${datasource}" }, "query": { "query": "label_values(node_uname_info, job)", "refId": "A" }, "definition": "label_values(node_uname_info, job)", "refresh": 1, "multi": true, "includeAll": true, "current": { "text": ["All"], "value": ["$__all"] }, "sort": 1 }
    ]
  },
  "panels": [
    { "id": 1, "type": "stat", "title": "Load 1m (average)", "gridPos": { "x": 0, "y": 0, "w": 6, "h": 4 },
      "datasource": { "type": "prometheus", "uid": "${datasource}" },
      "fieldConfig": { "defaults": { "unit": "short", "decimals": 2, "thresholds": { "mode": "absolute", "steps": [ { "color": "green", "value": null }, { "color": "orange", "value": 4 }, { "color": "red", "value": 8 } ] }, "mappings": [] }, "overrides": [] },
      "options": { "reduceOptions": { "calcs": ["lastNotNull"], "fields": "", "values": false }, "colorMode": "value", "graphMode": "area" },
      "targets": [ { "refId": "A", "expr": "avg(node_load1{job=~\"$job\"})", "instant": false, "range": true, "legendFormat": "load" } ] },
    { "id": 2, "type": "timeseries", "title": "CPU busy by instance", "description": "Seconds of CPU per second, all non-idle modes.", "gridPos": { "x": 6, "y": 0, "w": 18, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${datasource}" },
      "fieldConfig": { "defaults": { "unit": "short", "custom": { "drawStyle": "line", "fillOpacity": 10, "stacking": { "mode": "none", "group": "A" }, "lineWidth": 1 } }, "overrides": [] },
      "options": { "legend": { "displayMode": "list", "placement": "bottom", "showLegend": true }, "tooltip": { "mode": "multi" } },
      "targets": [ { "refId": "A", "expr": "sum by (instance) (rate(node_cpu_seconds_total{job=~\"$job\",mode!=\"idle\"}[$__rate_interval]))", "legendFormat": "{{instance}}", "range": true } ] },
    { "id": 3, "type": "timeseries", "title": "Memory available", "gridPos": { "x": 0, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${datasource}" },
      "fieldConfig": { "defaults": { "unit": "bytes", "min": 0, "custom": { "drawStyle": "line", "fillOpacity": 0 } }, "overrides": [] },
      "targets": [ { "refId": "A", "expr": "node_memory_MemAvailable_bytes{job=~\"$job\"}", "legendFormat": "{{instance}}", "range": true } ] },
    { "id": 4, "type": "table", "title": "Load by instance", "gridPos": { "x": 12, "y": 8, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "${datasource}" },
      "fieldConfig": { "defaults": {}, "overrides": [] },
      "targets": [ { "refId": "A", "expr": "avg by (instance) (node_load5{job=~\"$job\"})", "format": "table", "instant": true, "range": false } ] },
    { "id": 5, "type": "text", "title": "About", "gridPos": { "x": 0, "y": 16, "w": 24, "h": 3 },
      "options": { "mode": "markdown", "content": "Fixture derived from the structure of community node dashboards. Not a real system." } }
  ]
}
```

### 9.2 hard-service-mixed.json

Synthetic; combines features seen in Kubernetes and service dashboards.

```json
{
  "uid": "hard-service-mixed",
  "title": "Service health (hard)",
  "schemaVersion": 41,
  "version": 12,
  "tags": ["fixture"],
  "time": { "from": "now-24h", "to": "now" },
  "refresh": "30s",
  "graphTooltip": 1,
  "annotations": { "list": [
    { "builtIn": 1, "datasource": { "type": "grafana", "uid": "-- Grafana --" }, "enable": true, "hide": true, "name": "Annotations & Alerts", "type": "dashboard" },
    { "name": "Deploys", "enable": true, "iconColor": "blue", "datasource": { "type": "loki", "uid": "loki-main" }, "expr": "{app=\"deployer\"} |= \"finished\"" } ] },
  "links": [ { "type": "dashboards", "title": "Related", "tags": ["fixture"], "asDropdown": true, "includeVars": true, "keepTime": true } ],
  "templating": { "list": [
    { "name": "env", "type": "custom", "query": "prod,stage", "multi": false, "current": { "text": "prod", "value": "prod" }, "options": [] },
    { "name": "service", "type": "query", "datasource": { "type": "prometheus", "uid": "prom-main" }, "query": "label_values(http_requests_total{env=\"$env\"}, service)", "regex": "/^(?!canary-).*/", "multi": true, "includeAll": true, "allValue": ".*", "refresh": 2, "current": {} },
    { "name": "pod", "type": "query", "datasource": { "type": "prometheus", "uid": "prom-main" }, "query": "label_values(kube_pod_info{service=~\"$service\"}, pod)", "multi": true, "includeAll": true, "refresh": 2, "current": {} },
    { "name": "window", "type": "interval", "query": "1m,5m,15m", "auto": true, "auto_count": 30, "auto_min": "10s", "current": { "text": "auto", "value": "$__auto_interval_window" } },
    { "name": "filters", "type": "adhoc", "datasource": { "type": "prometheus", "uid": "prom-main" } },
    { "name": "owner", "type": "constant", "query": "team-a", "hide": 2 },
    { "name": "search", "type": "textbox", "query": "", "current": { "text": "", "value": "" } } ] },
  "panels": [
    { "id": 1, "type": "row", "title": "Traffic for $service", "collapsed": false, "gridPos": { "x": 0, "y": 0, "w": 24, "h": 1 }, "panels": [], "repeat": "service" },
    { "id": 2, "type": "timeseries", "title": "Request rate", "gridPos": { "x": 0, "y": 1, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prom-main" },
      "fieldConfig": { "defaults": { "unit": "reqps", "custom": { "stacking": { "mode": "normal" }, "fillOpacity": 30 } },
        "overrides": [ { "matcher": { "id": "byName", "options": "5xx" }, "properties": [ { "id": "color", "value": { "mode": "fixed", "fixedColor": "red" } }, { "id": "custom.axisPlacement", "value": "right" } ] } ] },
      "targets": [
        { "refId": "A", "expr": "sum by (code) (rate(http_requests_total{service=~\"$service\",env=\"$env\"}[$__rate_interval]))", "legendFormat": "{{code}}" },
        { "refId": "B", "expr": "label_replace(sum(rate(http_requests_total{code=~\"5..\"}[$__rate_interval])), \"series\", \"5xx\", \"\", \"\")", "legendFormat": "5xx" } ] },
    { "id": 3, "type": "timeseries", "title": "Latency p95 / p99", "gridPos": { "x": 12, "y": 1, "w": 12, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prom-main" },
      "fieldConfig": { "defaults": { "unit": "s", "decimals": 3 }, "overrides": [] },
      "targets": [
        { "refId": "A", "expr": "histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket{service=~\"$service\"}[$__rate_interval])))", "legendFormat": "p95" },
        { "refId": "B", "expr": "histogram_quantile(0.99, sum by (le) (rate(http_request_duration_seconds_bucket{service=~\"$service\"}[$__rate_interval])))", "legendFormat": "p99", "hide": false } ] },
    { "id": 4, "type": "stat", "title": "Error ratio", "gridPos": { "x": 0, "y": 9, "w": 6, "h": 4 },
      "datasource": { "type": "-- Mixed --" },
      "fieldConfig": { "defaults": { "unit": "percentunit", "thresholds": { "mode": "percentage", "steps": [ { "color": "green", "value": null }, { "color": "red", "value": 5 } ] },
        "mappings": [ { "type": "special", "options": { "match": "null", "result": { "text": "no data" } } }, { "type": "range", "options": { "from": 0, "to": 0.001, "result": { "text": "ok", "color": "green" } } } ] }, "overrides": [] },
      "targets": [
        { "refId": "A", "datasource": { "type": "prometheus", "uid": "prom-main" }, "expr": "sum(rate(http_requests_total{code=~\"5..\"}[5m])) / sum(rate(http_requests_total[5m]))", "hide": true },
        { "refId": "B", "datasource": { "type": "__expr__", "uid": "__expr__" }, "type": "math", "expression": "$A * 100" } ] },
    { "id": 5, "type": "table", "title": "Slow endpoints", "gridPos": { "x": 6, "y": 9, "w": 18, "h": 8 },
      "datasource": { "type": "prometheus", "uid": "prom-main" },
      "transformations": [ { "id": "merge", "options": {} }, { "id": "organize", "options": { "excludeByName": { "Time": true }, "renameByName": { "Value #A": "p95 (s)" } } } ],
      "targets": [ { "refId": "A", "expr": "topk(10, histogram_quantile(0.95, sum by (le, handler) (rate(http_request_duration_seconds_bucket[$__range]))))", "format": "table", "instant": true } ] },
    { "id": 6, "type": "logs", "title": "Errors", "gridPos": { "x": 0, "y": 17, "w": 24, "h": 8 },
      "datasource": { "type": "loki", "uid": "loki-main" },
      "targets": [ { "refId": "A", "expr": "{app=~\"$service\"} |= \"error\" | json | level=\"error\"", "queryType": "range", "maxLines": 200 } ] },
    { "id": 7, "type": "timeseries", "title": "Queue depth (CloudWatch)", "gridPos": { "x": 0, "y": 25, "w": 12, "h": 8 },
      "datasource": { "type": "cloudwatch", "uid": "cw-main" },
      "targets": [ { "refId": "A", "namespace": "AWS/SQS", "metricName": "ApproximateNumberOfMessagesVisible", "dimensions": { "QueueName": "jobs" }, "statistic": "Maximum", "period": "300", "region": "default", "alias": "{{QueueName}}" } ] },
    { "id": 8, "type": "timeseries", "libraryPanel": { "uid": "lib-cpu-1", "name": "CPU by pod" }, "title": "CPU by pod (library)", "gridPos": { "x": 12, "y": 25, "w": 12, "h": 8 } },
    { "id": 9, "type": "state-timeline", "title": "Pod phase", "gridPos": { "x": 0, "y": 33, "w": 12, "h": 6 },
      "datasource": { "type": "prometheus", "uid": "prom-main" },
      "targets": [ { "refId": "A", "expr": "kube_pod_status_phase{pod=~\"$pod\"} == 1", "legendFormat": "{{pod}} {{phase}}" } ] },
    { "id": 10, "type": "row", "title": "Details (collapsed)", "collapsed": true, "gridPos": { "x": 0, "y": 39, "w": 24, "h": 1 },
      "panels": [
        { "id": 11, "type": "timeseries", "title": "Retries", "gridPos": { "x": 0, "y": 40, "w": 12, "h": 8 }, "datasource": { "type": "prometheus", "uid": "prom-main" },
          "links": [ { "title": "Runbook", "url": "https://example.invalid/runbook?from=${__from}&to=${__to}&var-env=${env}", "targetBlank": true } ],
          "targets": [ { "refId": "A", "expr": "increase(retries_total{service=~\"$service\"}[$__interval])", "interval": "1m", "legendFormat": "retries" } ] } ] },
    { "id": 12, "type": "alertlist", "title": "Firing alerts", "gridPos": { "x": 12, "y": 33, "w": 12, "h": 6 }, "options": { "stateFilter": { "firing": true } } }
  ]
}
```

### 9.3 hostile.json

Synthetic. It is built from the way the loader and the emitters could be attacked: text that reaches NRQL (`'`, `--`, backtick), HCL (`"`, `${}`, a closing brace and a new `resource`), Markdown and HTML, terminal escapes (`\u001b[31m`, a right-to-left override `?`, a NUL), path-like uid, and shape violations. My own analysis script crashed on this file (a non-string `expr`, 12345), which is the point: the loader must treat every nested value as untyped. Note that `"A..."` stands for a 200-character title; the 1 MB version is generated in tests.

```json
{
  "uid": "../../etc/passwd",
  "title": "\"; resource \"null_resource\" \"pwn\" { provisioner \"local-exec\" { command = \"id\" } } #",
  "tags": ["${var.secret}", "<script>alert(1)</script>", "x\u0000y", 7, null],
  "schemaVersion": "forty-one",
  "refresh": { "not": "a string" },
  "time": { "from": "now-9999999999y", "to": "yesterday" },
  "templating": { "list": [
    "not an object",
    { "name": "x' OR 1=1 --", "type": "query", "query": 42, "current": "oops", "regex": "(a+)+$" },
    { "name": "dup", "type": "custom", "query": "a,b" },
    { "name": "dup", "type": "custom", "query": "c,d" } ] },
  "annotations": [ { "name": "list instead of object" } ],
  "panels": [
    { "id": 1, "type": "timeseries", "title": "'} DROP TABLE Metric; SELECT * FROM Metric WHERE x = '", "gridPos": { "x": -5, "y": -1, "w": 99999, "h": 0 },
      "datasource": "${DS_EVIL}",
      "targets": [ { "refId": "A", "expr": "up{job=\"a\\\"} or vector(1) # \n\"}", "legendFormat": "{{instance}}' AS evil, `x`" },
                   "not a target",
                   { "refId": "A", "expr": 12345 },
                   { "refId": "B", "expr": "sum(rate(a[5m])) by (le, `x`, \"y\")" } ] },
    { "id": 1, "type": "text", "title": "\u202eevil\u202c \u001b[31mred\u001b[0m", "gridPos": { "x": 0, "y": 0, "w": 12, "h": 3 },
      "options": { "mode": "html", "content": "<img src=x onerror=alert(1)>\n[click](javascript:alert(1))\n{{7*7}} ${jndi:ldap://x} $(id)" } },
    { "id": 3, "type": "row", "title": "outer", "collapsed": true, "panels": [
      { "id": 4, "type": "row", "title": "inner row inside a row", "collapsed": true, "panels": [
        { "id": 5, "type": "row", "title": "and again", "panels": [ { "id": 6, "type": "stat", "title": "deep", "targets": [] } ] } ] } ] },
    { "id": 7, "type": "piechart", "title": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA", "libraryPanel": { "uid": "missing", "name": "does not exist" } },
    { "id": 8, "type": "unknown-plugin-panel", "title": 12345, "targets": "nope" },
    null,
    [ "array instead of panel" ]
  ]
}
```

### 9.4 legacy-rows-v12.json

Synthetic, in the style of Grafana 4.x exports (schemaVersion 12, `rows`, `span`, `__inputs`).

```json
{
  "__inputs": [ { "name": "DS_PROMETHEUS", "label": "Prometheus", "type": "datasource", "pluginId": "prometheus", "pluginName": "Prometheus" } ],
  "__requires": [ { "type": "grafana", "id": "grafana", "name": "Grafana", "version": "4.6.3" }, { "type": "panel", "id": "singlestat", "name": "Singlestat", "version": "" } ],
  "id": null,
  "title": "Legacy rows layout (schemaVersion 12)",
  "tags": ["fixture", "legacy"],
  "style": "dark",
  "timezone": "browser",
  "editable": true,
  "hideControls": false,
  "sharedCrosshair": true,
  "time": { "from": "now-1h", "to": "now" },
  "timepicker": { "refresh_intervals": ["5s", "1m"], "time_options": ["5m", "1h"] },
  "refresh": false,
  "schemaVersion": 12,
  "version": 2,
  "annotations": { "list": [ { "name": "restarts", "datasource": "${DS_PROMETHEUS}", "enable": true, "iconColor": "rgba(255, 96, 96, 1)", "expr": "changes(process_start_time_seconds[1m]) > 0", "titleFormat": "restart", "step": "60s" } ] },
  "templating": { "list": [
    { "name": "instance", "type": "query", "datasource": "${DS_PROMETHEUS}", "query": "label_values(up, instance)", "refresh": 1, "multi": true, "includeAll": true, "allValue": null, "current": { "text": "All", "value": "$__all" }, "options": [], "regex": "", "tags": [], "tagsQuery": "", "useTags": false },
    { "name": "interval", "type": "interval", "query": "1m,5m,10m", "auto": false, "current": { "text": "5m", "value": "5m" }, "options": [] } ] },
  "rows": [
    { "title": "Health", "collapse": false, "editable": true, "height": "250px", "showTitle": true, "repeat": null,
      "panels": [
        { "id": 1, "type": "singlestat", "title": "Instances up", "span": 3, "datasource": "${DS_PROMETHEUS}", "valueName": "current", "format": "none", "decimals": 0, "prefix": "", "postfix": " up",
          "colorBackground": false, "colorValue": true, "thresholds": "1,2", "colors": ["#d44a3a", "rgba(237, 129, 40, 0.89)", "#299c46"],
          "valueMaps": [ { "value": "null", "op": "=", "text": "N/A" } ], "mappingType": 1,
          "sparkline": { "show": true, "full": false }, "gauge": { "show": false, "minValue": 0, "maxValue": 100, "thresholdMarkers": true, "thresholdLabels": false },
          "targets": [ { "refId": "A", "expr": "sum(up{instance=~\"$instance\"})", "intervalFactor": 2, "step": 60 } ] },
        { "id": 2, "type": "graph", "title": "Request rate", "span": 9, "datasource": "${DS_PROMETHEUS}", "lines": true, "fill": 1, "linewidth": 1, "stack": true, "percentage": false, "nullPointMode": "null",
          "legend": { "show": true, "avg": true, "max": true, "current": false, "values": true, "alignAsTable": true }, "tooltip": { "shared": true, "sort": 0, "value_type": "individual" },
          "yaxes": [ { "format": "reqps", "label": null, "logBase": 1, "min": 0, "max": null, "show": true }, { "format": "short", "show": false } ],
          "aliasColors": { "5xx": "#bf1b00" }, "seriesOverrides": [ { "alias": "5xx", "yaxis": 2 } ],
          "thresholds": [ { "value": 100, "op": "gt", "colorMode": "critical", "fill": true, "line": true } ],
          "targets": [ { "refId": "A", "expr": "sum(rate(http_requests_total{instance=~\"$instance\"}[$interval])) by (code)", "legendFormat": "{{code}}", "intervalFactor": 2, "step": 120 } ] } ] },
    { "title": "Collapsed extras", "collapse": true, "height": "200px", "showTitle": true, "repeat": "instance",
      "panels": [ { "id": 3, "type": "table", "title": "Per instance", "span": 12, "datasource": "${DS_PROMETHEUS}", "transform": "timeseries_to_rows",
        "styles": [ { "pattern": "Value", "type": "number", "unit": "short", "decimals": 1, "colorMode": "cell", "colors": ["green", "orange", "red"], "thresholds": ["10", "20"] } ],
        "targets": [ { "refId": "A", "expr": "node_load1{instance=\"$instance\"}", "format": "table", "instant": true } ] } ] } ]
}
```

### 9.5 v2-small.json

Synthetic, written from the v2beta1 CUE file (CODE C2). The `vizConfig.version` string is made up. Whether a live `v2beta1` export uses `group: "prometheus"` and `spec.expr` exactly like this is UNVERIFIED; check against a real export before the golden file is approved.

```json
{
  "apiVersion": "dashboard.grafana.app/v2beta1",
  "kind": "Dashboard",
  "metadata": { "name": "v2-small", "namespace": "default", "annotations": { "grafana.app/folder": "team-a" }, "labels": {} },
  "spec": {
    "title": "Small v2 dashboard",
    "description": "Fixture written from the v2beta1 CUE schema (apps/dashboard/kinds/v2beta1), not exported from a stack.",
    "tags": ["fixture"],
    "cursorSync": "Off",
    "editable": true,
    "liveNow": false,
    "preload": false,
    "links": [],
    "timeSettings": { "timezone": "browser", "from": "now-6h", "to": "now", "autoRefresh": "1m", "autoRefreshIntervals": ["1m", "5m"], "hideTimepicker": false, "fiscalYearStartMonth": 0 },
    "annotations": [],
    "variables": [
      { "kind": "QueryVariable", "spec": { "name": "job", "label": "Job", "hide": "dontHide", "refresh": "onDashboardLoad", "skipUrlSync": false, "multi": true, "includeAll": true, "allowCustomValue": true, "regex": "", "sort": "alphabeticalAsc", "current": { "text": "All", "value": "$__all" }, "options": [],
        "query": { "kind": "DataQuery", "group": "prometheus", "version": "v0", "datasource": { "name": "prom-main" }, "spec": { "expr": "label_values(up, job)" } } } },
      { "kind": "CustomVariable", "spec": { "name": "mode", "hide": "dontHide", "skipUrlSync": false, "multi": false, "includeAll": false, "allowCustomValue": true, "query": "rate,total", "current": { "text": "rate", "value": "rate" }, "options": [] } }
    ],
    "elements": {
      "panel-1": { "kind": "Panel", "spec": { "id": 1, "title": "Targets up", "links": [],
        "data": { "kind": "QueryGroup", "spec": { "queries": [ { "kind": "PanelQuery", "spec": { "refId": "A", "hidden": false, "query": { "kind": "DataQuery", "group": "prometheus", "version": "v0", "datasource": { "name": "prom-main" }, "spec": { "expr": "sum(up{job=~\"$job\"})", "instant": true } } } } ], "transformations": [], "queryOptions": {} } },
        "vizConfig": { "kind": "VizConfig", "group": "stat", "version": "13.0.0", "spec": { "options": { "colorMode": "value" }, "fieldConfig": { "defaults": { "unit": "short", "thresholds": { "mode": "absolute", "steps": [ { "value": null, "color": "red" }, { "value": 1, "color": "green" } ] } }, "overrides": [] } } } } },
      "panel-2": { "kind": "Panel", "spec": { "id": 2, "title": "Memory", "links": [],
        "data": { "kind": "QueryGroup", "spec": { "queries": [ { "kind": "PanelQuery", "spec": { "refId": "A", "hidden": false, "query": { "kind": "DataQuery", "group": "prometheus", "version": "v0", "datasource": { "name": "prom-main" }, "spec": { "expr": "node_memory_MemAvailable_bytes{job=~\"$job\"}", "legendFormat": "{{instance}}" } } } } ], "transformations": [], "queryOptions": { "maxDataPoints": 300, "interval": "1m" } } },
        "vizConfig": { "kind": "VizConfig", "group": "timeseries", "version": "13.0.0", "spec": { "options": {}, "fieldConfig": { "defaults": { "unit": "bytes" }, "overrides": [] } } } } },
      "lib-1": { "kind": "LibraryPanel", "spec": { "id": 3, "title": "Shared CPU panel", "libraryPanel": { "uid": "lib-cpu-1", "name": "CPU by pod" } } }
    },
    "layout": { "kind": "RowsLayout", "spec": { "rows": [
      { "kind": "RowsLayoutRow", "spec": { "title": "Overview", "collapse": false, "layout": { "kind": "GridLayout", "spec": { "items": [
        { "kind": "GridLayoutItem", "spec": { "x": 0, "y": 0, "width": 8, "height": 6, "element": { "kind": "ElementReference", "name": "panel-1" } } },
        { "kind": "GridLayoutItem", "spec": { "x": 8, "y": 0, "width": 16, "height": 6, "element": { "kind": "ElementReference", "name": "panel-2" } } } ] } } } },
      { "kind": "RowsLayoutRow", "spec": { "title": "Shared", "collapse": true, "layout": { "kind": "AutoGridLayout", "spec": { "maxColumnCount": 2, "columnWidthMode": "standard", "rowHeightMode": "standard", "items": [
        { "kind": "AutoGridLayoutItem", "spec": { "element": { "kind": "ElementReference", "name": "lib-1" } } } ] } } } }
    ] } }
  }
}
```

## 10. Build list

Effort: S under a day, M a few days, L a week or more. Risk is the chance the item needs rework after contact with real data or New Relic.

| # | item | effort | risk | depends on | notes |
|---|---|---|---|---|---|
| 1 | Dashboard extractor `extract-grafana-dashboards`: `/api/search` (or `/apis` list) then per-uid GET, folders, library panels, data source list; same host rule and token as the alert extractor; page size and total caps; no secrets in output; writes one file per dashboard | M | low to medium | live read (Q1, Q9) | start with the legacy `/api/dashboards/uid` and `/api/library-elements` (documented, DOCS D2, D5) and add `/apis` after seeing what the stack returns |
| 2 | Tolerant loader and normaliser: classic, v1 envelope, v2 envelope, wrapper, list; flatten panels (rows, collapsed rows, legacy `rows[]`), v2 `elements` + layouts to a list with positions; schemaVersion-independent field access; depth, size and count limits; never raises | M | medium (v2 shape unverified) | 1 or fixtures | fixtures 9.1 to 9.5 drive it. Model on `sources/gcp/dashboard_models.py` and `sources/grafana/models.py` |
| 3 | IR extension, minimal: nullable bucket (`TIMESERIES AUTO`), widget `LIMIT MAX`, dashboard `variables[]` in `IRDashboard`, emitters writing `variables` in NerdGraph and `variable` blocks in Terraform, `{{var}}` substitution through the shared escaping | L | medium to high | sandbox check of `variables` input in `dashboardCreate` | the emitter has no variables today (REPO `emit/dashboard.py`); variable names and values are untrusted (add a `variable_name` validator beside `identifier`) |
| 4 | Panel mapper and layout: 24 to 12 grid, heights, rows to heading widgets or pages, tabs to pages, `pack()` reuse, widget cap, reasons for every unmapped panel type | S to M | low | 2 | reuse `gcp/dashboards.py` `pack`, `_layout` |
| 5 | Prometheus T1 for dashboards: instant and range, matchers incl. variable matchers and RLIKE, `AS` from static legend, instant vs range, table format | M | medium | 2, 3 (partly), RLIKE sandbox check | reuse `_outer`, `_matchers`, `_attribute`; lifts the counter and window checks that only make sense for alerts |
| 6 | Prometheus T2: `rate`/`irate`/`increase` with `rate(sum(x), 1 second)`, `histogram_quantile` with `bucketPercentile`, `topk` with `LIMIT`; counter semantics check | M | high (delta vs cumulative storage and metric naming unverified) | sandbox with real remote-written metrics | highest value after 5: +25 points of coverage on Prometheus-heavy dashboards (31% to 57%) |
| 7 | Variables: `query` (label_values), `custom`, `textbox`, `constant`, `interval` (drop), `datasource` (drop and resolve), multi/include-all, chained (nested) with a flag for licence | M | medium | 3, 5 | 89% of Prometheus queries use variables; without this, T1 coverage is roughly nil on real dashboards |
| 8 | Data source resolution: `${DS_*}` through `__inputs`, `${datasource}` variable plus its `query`, uids against `/api/datasources`, default data source, Mixed per target, `-- Dashboard --` reuse | M | low | 1, 2 | needed to even know a panel's type; 36 of 1,404 panels have no resolvable type from the file alone |
| 9 | Prometheus T3 arithmetic over `MetricSignal.expression` | S | medium | 6 | +8 points |
| 10 | Loki: `count_over_time`/`rate` with filters, logs panel to log table | M | medium | 7 | few panels in the sample (39 Loki panels) but common in the owner's likely stack |
| 11 | Widget options: unit, decimals, thresholds, legend, y-axis min/max, line interpolation, stacking as area, billboard sparkline | M | medium (`rawConfiguration` field names for these options are not documented in the pages read; Terraform names are) | 3 | generate Terraform first, which has documented names (DOCS N6), and read back the JSON in the sandbox to learn the NerdGraph names |
| 12 | Data check for dashboards: metric exists in the account, like GCP's `check-gcp-data` | M | low | 5, 6 | tells which converted widgets would be empty (REPO `core/data_check.py`) |
| 13 | CloudWatch and GCP adapters (Grafana target to the existing alert-path inputs) | M each | low | 2 | 56 and 15 panels in the sample |
| 14 | Report: per-dashboard table of widgets with reasons, `md_cell` on every untrusted string, annotation and link inventory | S | low | 2 | reuse `core/dashboard_report.py` |
| 15 | `organize` transformation (rename, hide), repeat to `FACET` | S | medium | 5 | small gains |
| 16 | Hostile-input hardening tests with fixtures 9.3 and generated cases | S | low | 2 | before anything is applied |
| later | `*_over_time` with `SLIDE BY`, `predict_linear`/`deriv`, `label_replace` via `capture()`, nested aggregation via subquery, gauge and bargauge widgets, heatmap and histogram panels, log parser stages via `aparse()` | M to L each | high | | decide after the sandbox results |
| not planned | SQL, Elasticsearch, InfluxDB, Azure Monitor, Tempo, Graphite panels (data lives elsewhere), annotations, state timeline, status history, news, dashlist, alertlist, canvas, geomap, node graph, traces, flame graph, candlestick | | | | listed in the report with the reason |

Suggested first cut for a useful release: items 1, 2, 4, 5, 7, 8, 14, 16 (the T1 level on Prometheus data with variables), then 3 in step with 7, then 6.

### 10.1 Questions that need a live, read-only look at the owner's Grafana stack (13 dashboards)

Answers decide the order above. Each is a read of dashboard JSON or the data source list; none needs a write.

1. Which JSON shape does the stack return for `GET /api/dashboards/uid/{uid}` and for `GET /apis/dashboard.grafana.app/v1/namespaces/{ns}/dashboards`? Is any of the 13 a v2 dashboard (`elements`, `layout`), and if so which `apiVersion` is served (v2, v2beta1, v2alpha1)?
2. `schemaVersion` of each, panel counts, and total widget count; any dashboard above 200 panels.
3. The panel type histogram: how many `graph`/`singlestat` legacy panels, how many gauge, bargauge, heatmap, state timeline, geomap, news, alertlist, dashlist.
4. Data sources: `GET /api/datasources` (type, uid, name, default). Is every data source Prometheus or Loki, or are there CloudWatch, Elasticsearch, Infinity, SQL, Tempo, Pyroscope? Is "Prometheus" Grafana Cloud Metrics (Mimir)? Do the metrics also reach New Relic, and under which names? (This decides whether T2 `rate` on counters is meaningful.)
5. Variables: which types, any chained, any with regex, any with `allValue`, any `interval` or `datasource` variables.
6. Library panels: how many, and do the 13 dashboards use them? Do the extract's service account permissions cover `library.panels:read`?
7. Are the dashboards provisioned (`meta.provisioned`) or edited in the UI, and is there a Git or Terraform source that could be read instead?
8. Rows, collapsed rows, repeats, tabs, annotations, dashboard links, panel links, transformations, value mappings, field overrides: counts per dashboard.
9. Time range and refresh defaults; whether the owner relies on a default time range that New Relic cannot store.
10. Which of the 13 dashboards matter (usage), so the first conversion targets real ones.
11. The role of the existing token (`GRAFANA_SA_TOKEN`): can it read dashboards and folders, or only alerting?
12. Folder structure and naming (to decide how to name converted dashboards).
13. For the Prometheus queries the owner actually uses: share that use `rate`, `histogram_quantile`, arithmetic between series, `on()/group_left`, `label_replace`, recording rules (metric names with `:`).

## 11. UNVERIFIED claims (not to be built on without a check)

1. New Relic gauge widget visualization id and Terraform support (DOCS N2 lists a Gauge chart; no id or block found). Stacked bar `viz` id.
2. That `viz.area` stacks, that a billboard with `TIMESERIES` draws a sparkline, and the pixel height of a New Relic row (so the 24-to-12 height factor).
3. The valid `units.unit` strings for New Relic and the NerdGraph `rawConfiguration` names for thresholds, units, legend and y-axis (Terraform names are documented, NerdGraph names are not in the pages read).
4. Whether Prometheus counters are stored as deltas so `rate(sum(x), 1 second)` and `sum(x)` equal `rate()` and `increase()`; the `_total` naming in the translator's example; the exact PromQL translator coverage (second-hand note).
5. Whether `RLIKE` behaves in dashboard widgets the way the NRQL page says, and in alert conditions (not needed here).
6. `LIMIT MAX` ceiling for charts (the other session's note says 5,000).
7. The NR variable query form `FROM Metric SELECT uniques(label) WHERE metricName = '...'`, and variable name constraints, and the maximum number of variables or widgets per dashboard or page.
8. Whether New Relic has any value-mapping, ad hoc filter, annotation or default-time-range storage on dashboards, and whether any widget exists for state timelines, alert lists, geomaps or scatter plots.
9. v2 resources: whether the owner's stack serves `v2` / `v2beta1` / `v2alpha1`, how a Prometheus query is stored in `spec` in a live export, the grid width of `GridLayoutItem`, and whether every read path applies schema migrations.
10. The `/api/search` response fields `folderUid` and `folderTitle`; `GET /api/datasources` permission name; legacy `GET /api/folders` and nested folders; any annotation API path under `/apis`.
11. Rate limits of the dashboard read endpoints; the namespace string on the owner's stack.
12. Whether custom RBAC role assignment is available on the owner's stack tier (the fixed role names are documented).
13. Built-in macros `$__from`, `$__to`, `$__timeFilter`, `$__dashboard`, `$__org`, `$__user`, and the full list of `${var:format}` options (only part of the page was read).
14. Prometheus regex semantics (RE2, anchored) used to justify `RLIKE`.
15. That `__elements` is the key for library panel models in an external export (the Go comment mentions it; the key's structure was not read).
16. The coverage numbers themselves depend on a regex classifier (section 8.5) and on a chosen, not random, sample.
