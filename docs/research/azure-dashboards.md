# Azure dashboards and workbooks: formats, mapping, and what converts

Written 2026-11-11. Offline: the Azure REST specs, the public `microsoft/Application-Insights-Workbooks` repository (MIT) and this repository's fixtures. No Azure tenant and no New Relic dashboard was written. UNVERIFIED means "inferred from templates, not from documentation".

## Sources

- S-WB: `Azure/azure-rest-api-specs` at `939d1a81ec5159e61394cd8ca2589c2c0ce49ccd`, `applicationinsights/.../stable/2023-06-01/workbooks_API.json`. List by subscription requires `category`; `canFetchContent` makes the list return `serializedData`, a string holding JSON.
- S-DASH: the `Microsoft.Portal/dashboards` examples, `2025-04-01-preview` (preview only). The owner's subscription answered 502 for every version, so extraction treats a 5xx as "not available".
- W-REPO: `microsoft/Application-Insights-Workbooks` at `cc8922da42fcf1ef5ae308e076d9a7dc69698655`, 713 `.workbook` templates. 30 are saved under `tests/fixtures/azure-workbooks/` with `SOURCES.md` and the licence.

## Workbook format (read from the 103 templates fetched)

- Items: `type` 1 text, 3 query, 9 parameters, 10 metrics, 11 links, 12 group. An item's `content` is an object, or a string holding JSON in old templates (8 of ~1,800 items).
- Query types: 0 or absent Log Analytics KQL, 1 Azure Resource Graph, 7 merge, 8 static JSON, 9 Azure Data Explorer, 11 Intune, 12 ARM endpoint.
- Metrics aggregation numbers (UNVERIFIED, from pairs in the templates: Sum on transaction counts, Average on latencies, Max on cache load, Count on request counts): 1 sum, 2 min, 3 max, 4 average, 7 count. `chartType`: 0 area, 1 bar, 2 line, 3 scatter, -1 or absent grid (the documented order is Area, Bar, Line, Scatter, Grid; line is the commonest value).
- A metric id is `<namespace>-<Category>-<Name>` or a plain name. The converter takes the last segment and gives it to `azure/names.py`, the same rule the alert converter uses.
- Tabs come in two forms: a links item with `style: tabs` (`parameterName`/`parameterValue`, or `cellValue`/`subTarget` with the label in `linkLabel`), and a type 10 "options group" parameter with `jsonData` options. Items and groups then carry `conditionalVisibility` `isEqualTo` the tab value. Other conditions (a parameter is empty or equals a value) switch views and have no New Relic form.
- The official REST examples hold `serializedData` that is not valid JSON (item `content` quoted with unescaped inner quotes). The reader repairs exactly that shape, accepts the result only if it parses, and says so in the report.

## Mapping decisions

- Metric charts use `Metric` with `collector.name = 'azure-monitor'`, `azure.resourceId` scope and `TIMESERIES AUTO`, so the data path is the Azure Monitor integration ("native", left out of the saved IR).
- A resource picker without a query becomes an NRQL variable (`uniques(azure.resourceId, 200)`, narrowed by `metricName LIKE 'azure.<type>.%'`); the chart uses `azure.resourceId IN ({{name}})`. A picker filled by a Resource Graph query is not migrated; the chart covers every resource of the type and says so.
- KQL goes through `sources/azure/dashboard_kql.py`, one function that now calls the subset parser of the scheduled query rules (`kql_parse.py`, `kql_tables.py`, `kql_nrql.py`) and writes one SELECT over `Log` (aggregates, WHERE, FACET, TIMESERIES). Its result carries the data path of the table: `needs-log-forwarding` for forwarded tables, `needs-agent` for agent tables. A query with a workbook parameter in braces, raw rows or a `where` on the result stays a placeholder.
- Not migrated, always with a reason that starts with a fixed phrase (see `dashboard_rules.py`): conditional visibility that is not a tab, parameters that depend on others, Resource Graph, ARM, merge, JSON and Data Explorer queries, unknown item types, linked templates, portal tiles without a counterpart.

## Measured (exact; placeholders never counted as converted)

Before this work Azure visualisations converted 0 of N widgets (no reader, no converter). Now, with the KQL subset translator connected (2026-11-11, `integrate/wave2`):

| Set | Dashboards | Widgets | Converted | Placeholders |
|---|---:|---:|---:|---:|
| 14 official workbook examples | 16 | 26 | 13 | 13 |
| 6 official dashboard examples | 4 | 9 | 1 | 8 |
| Quickstart dashboard templates | 2 | 10 | 7 | 3 |
| 30 public workbook templates | 30 | 458 | 273 | 185 |

Widgets are source items or parts; the headings added for workbook groups are in neither count. The dashboard examples have parts with a position and no type, so they are placeholders. Before the translator was connected the public templates read 259 converted and 199 placeholders, and 105 of the placeholders were KQL items. Now 14 of those KQL items convert (all with the data path `needs-log-forwarding`) and 91 stay placeholders: 71 use a workbook parameter written in braces (`{Name}`) inside the query, which the subset does not read, 5 have an expression around an aggregate, 4 return rows with no aggregate, 4 define a `let` table, and the rest use `union`, a function or `{`. Of the 185 placeholders, 70 are conditional visibility that is not a tab, 8 are metric names that cannot be written, and the rest are Resource Graph, ARM, merge and linked-template items. `tests/test_azure_dashboard_corpus.py` pins these numbers.
