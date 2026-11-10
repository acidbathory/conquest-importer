# New Relic dashboard variables, and how Grafana variables map onto them

Written 2026-11-10 from public documentation only (raw files of `newrelic/docs-website` and `newrelic/terraform-provider-newrelic`, read that day). No New Relic or Grafana instance was called. UNVERIFIED means "not stated in the pages read"; it needs a sandbox test.

## Sources

- V1: docs-website `src/content/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables.mdx`
- V2: same folder, `nested-variables.mdx`
- V3: docs-website `src/content/docs/apis/nerdgraph/examples/nerdgraph-dashboards.mdx` (variable fields, limits table)
- V4: docs-website `src/content/docs/nrql/nrql-syntax-clauses-functions.mdx` (the `WITH ... AS` "NRQL variables" are a different, query-internal feature; not dashboard variables)
- V5: terraform-provider-newrelic `website/docs/r/one_dashboard.html.markdown` (`variable` block)

## What dashboards offer

| Topic | Fact | Source |
|---|---|---|
| Types | Query (NRQL, returns a list through `uniques()` or `keyset()`), List (comma separated; NerdGraph `ENUM`), Text field (`STRING`) | V1, V3, V5 |
| Name | Starts with a letter; letters, digits, underscore; used as `{{name}}` | V1 |
| Value type | A variable generates a string value | V1 |
| Multi-select | Query and List only, not text field; the widget must use `IN`, not `=` | V1, V5 |
| Default values | Optional; "select all" is `default_values = ["*"]` | V5 |
| Include variable toggle | Per variable, per session. Off replaces the variable's condition with `true`; in FACET and WHERE this works, in SELECT or function arguments it errors ("Unknown function Disable_variable()"). Option `excluded` sets the default | V1, V5 |
| Output format / replacement strategy | UI: String (default), Number, Identifier. Provider: `default`, `identifier`, `number`, `string`. Identifier is for event and attribute names (`FROM {{t}}`, `FACET {{x}}`), Number for function arguments (`percentile(d, {{p}})`) | V1, V5 |
| Where `{{var}}` works in a widget query | `SELECT`, `FROM`, `FACET`, `ORDER BY`, `WHERE`. Not in `AS`, `COMPARE WITH`, `LIMIT`, `OFFSET`, `SINCE`, `SLIDE BY`, `TIMESERIES`, `UNTIL`, `WITH` | V1 |
| `LIKE`/`RLIKE` | A text-field variable can hold a `%...%` pattern | V1 |
| Ignore time picker | Query variables only; default on (query runs over 1 hour unless it has `SINCE`); off follows the time picker | V1, V5 |
| Account | Query variables take `accountIds` (variable block `account_ids`; widget blocks use a different attribute name) | V3, V5 |
| Hidden | `hiddenOnVariablesBar` in `options` | V1, V3 |
| Limits | `uniques()` returns 5000 values by default, up to 10000 with the second argument. Dashboard limits (V3): 25 pages, 150 widgets per page, 20 queries per widget, 30 accounts in a cross-account query. **No limit on the number of variables is stated** (the repository cap of 50 is its own) | V1, V3 |
| Not supported | PDF export of widgets with variables; Lens SQL | V1 |

## Can a variable's query use another variable?

Yes on some plans, and the documentation disagrees with itself.

- V2: a variable query may reference another with `{{env}}` (`SELECT uniques(machine) FROM Table WHERE env IN ({{env}})`). "Available only to Pro, Enterprise, and Core Compute customers."
- V1, section on query variables, still says "nested variables are not supported as there can't be variables within variables", while its introduction links to the nested page.
- Limits (V2): the parent needs selected values when the child is defined, otherwise the child's query returns nothing; a widget needs a selected value too; deleting the parent breaks the child; clearing the parent leaves the child showing values from the parent's previous selection. Hide helper parents with `hiddenOnVariablesBar`.
- UNVERIFIED: whether a child may be listed before its parent in the dashboard definition (we write parents first anyway); how many levels are allowed (Grafana dashboards chain 3 to 4 deep); whether "select all" (`["*"]`) in a parent counts as "selected values" for the child; whether the dashboard API refuses the child on plans without the feature or accepts it and shows an error; the plan check for the owner's account.

## What the importer does (this change)

- IR (`core/dashboard_ir.py`): a variable query may contain `{{other}}` where NRQL substitutes it (the same clause rules as widgets). The dashboard rejects an undefined parent, a variable that uses itself, a cycle (`variable cycle: a, b`) and a child listed before its parent. `order_variables` sorts parents first (a list already in order is unchanged). Dashboards without nested variables serialise exactly as before.
- Emitters (`core/emit/dashboard.py`): refuse the same cases on the way out; variables are written in order.
- `apply-dashboards` already replaces the account placeholder in every variable (`with_account`); a test covers a chain.
- Each dashboard with nested variables gets a note that the feature is plan dependent, one for parents without a default value (V2: the child lists nothing until the parent has a selection) and one for `query_result()` variables.

## Grafana variable types

| Grafana | New Relic | Status |
|---|---|---|
| `label_values(metric{...}, label)` | `FROM Metric SELECT uniques(label) WHERE metricName = 'metric' AND ...`; with a name map entry, `FROM <event> SELECT uniques(label) WHERE <attr> IS NOT NULL ...` | converted; data presence UNVERIFIED |
| `label_values(label)` | `FROM Metric SELECT uniques(label)` | converted |
| `label_values({__name__=~"container.*", ...}, l)` | `metricName LIKE 'container%'` (plain words, prefix or suffix only; regex and `!=`, `!~` supported); refused when a name map renames metrics | converted |
| `$parent` in a matcher (`k=~"$p"`, `k="$p"`, `k=~"$a|$b"`) | `k IN ({{p}})` (multi or single) | converted; whole-variable matchers only |
| `$const` (constant variable) | its value is written into the query | converted |
| `query_result(selector)` + regex capturing one label (`/.*l="([^"]+).*/`, `/l="([^"]+)/`) | same as `label_values(selector, l)` | converted, approximate: Grafana reads the latest sample, `uniques()` reads the time window |
| `metrics(regex)` | `FROM Metric SELECT uniques(metricName) WHERE metricName LIKE ...` | converted; refused with a name map |
| `custom`, `switch` | `enum` with items; multi and "include all" default to all items | converted |
| `textbox` | `string` | converted |
| `constant` | substituted, no variable | converted |
| `interval`, `datasource` | dropped; the bucket is `TIMESERIES AUTO`, one account | converted (dropped) |
| `adhoc` | none in the pages read | manual: ad hoc filters have no New Relic variable |
| multi / include all | `isMultiSelection`, default `["*"]`, widgets use `IN ({{v}})`; `allValue` is ignored | converted |
| `$__interval`, `$__rate_interval` | `rate(sum(x), 1 second)` or the bucket (`TIMESERIES AUTO`); `increase` over them becomes `sum(x)` per bucket | converted, approximate |
| `$__range` | the dashboard time picker window | converted, approximate |

Manual, each with a fixed reason: `query_result()` of an expression or without a capture regex; `label_names()`; non-Prometheus variable queries; regex beyond plain words (lookahead, capture group, backreference); `${var:csv}` formats; a variable inside a metric or label name or mixed with text (`"x-$job"`; New Relic substitutes a variable whole); a label called `metricName` (collides with the attribute that holds the metric name); `metrics()` or `__name__` with a name map; a cycle; a child of a manual variable (down the chain).

## What New Relic cannot do (as far as the pages read show)

- No ad hoc filter variable and no group-by variable beyond an `identifier` variable after `FACET`.
- No variable in `TIMESERIES`, `LIMIT`, `SINCE`, `UNTIL`, `COMPARE WITH`, `SLIDE BY`, `AS`, `WITH`, so no `$__interval` as a bucket and no "top N" variable.
- Nested variables only on some plans; the parent needs a selection.
- Text fields cannot be multi-select.
- A variable is substituted whole: `"$job.*"` or `"x-$job"` cannot be written.
- Cannot express Grafana's `allValue` regex, the "All" label, or a variable that refreshes on time range with different semantics than "ignore time picker" (approximate).

## Measured on the 19 live-extracted dashboards (offline conversion)

Panels (rows excluded) exact / approximate / manual. Before to after:

- 6 community dashboards (243 panels): 0 / 3 / 240 to 0 / 173 / 70. Node Exporter Full: 0 of 124 to 100 approximate.
- 13 built-in dashboards (245 panels): 39 / 7 / 199 to 39 / 29 / 177 (the 39 exact are text panels).

No query panel is ever exact (`may_be_exact`); that rule is unchanged.
