# GCP filters: proposal for the owner

Branch `improve/gcp-filters`, from `integration/all-threads`. Offline only. Status: waiting for the owner.

## What changed

- One filter grammar for Cloud Monitoring and Cloud Logging (`sources/gcp/filter.py`): AND, OR, NOT and `-`,
  parentheses, implicit AND, `field=("a" OR "b")`, `one_of`, `starts_with`, `ends_with`, `has_substring`,
  `monitoring.regex.full_match`, `log_id()`, quoted field segments, `${variable}` terms. AND mixed with OR
  without parentheses is refused (the two languages disagree on precedence). Length and depth are capped.
- `sources/gcp/scope.py` turns a metric filter into `TagFilter`s: OR of one label and `one_of` become `IN`,
  the string functions become `LIKE`, a regex becomes `=`, `IN` or `LIKE` only when it is a literal, a list
  of literals, or a literal with `.*` at an end. Everything else stays manual with the reason (OR across
  labels, several metric types in one filter, real regex syntax which needs `RLIKE`, `*` or `%` inside a
  `LIKE` text, line breaks in a value). `sources/gcp/log_scope.py` does the same for log-match conditions.
- Quoted label names (`resource.label."project_id"`) are the same label as the unquoted form.
- `metadata.user_labels.<key>` maps to `gcp.labels.<key>` (confirmed in `gcp-metric-names-verified.md`);
  `metadata.system_labels.*` stays manual (no confirmed attribute).
- Ratios: `denominatorFilter` (alerts) and `timeSeriesFilterRatio` (dashboards) become
  `filter(sum(a), WHERE ...) / sum(b)`. This needed one core change: `MetricTerm.scope` (left out of saved IR
  when empty) and `_term_sql` in `core/emit/nrql.py`.
- Alignment periods that are not whole minutes are rounded up with a note; an absence condition with no
  aggregation uses the polling interval; an unreadable period stays manual.
- Template placeholders (`${PROJECT_ID}`) inside filter values now add a note: the query matches nothing until
  they are replaced.

## Pinned output that changes (needs your review, not regenerated)

`tests/fixtures/dashboard-output-hashes.json`, the `gcp/` entries for `58944063589550` (dashboard IR, NerdGraph,
`dashboards.tf`): the widget "Error ratio" in `tests/fixtures/gcp-dashboards/ops-mosaic.json` was a placeholder
("ratio queries in widgets are not converted") and is now a chart with
`filter(sum(gcp.run.request_count), WHERE metric.response_code_class = '5xx') / sum(gcp.run.request_count)`.
That is the intended improvement, so `tests/test_dashboard_ir_byte_identity.py[gcp]` fails until you approve the
new hashes. It is the only failing test in the whole suite. Golden files are untouched.

## Pinned tests changed on purpose

- `test_gcp.py`: the 30s-period cases are no longer manual; the "ratio" case now uses an unreadable denominator.
- `test_gcp_dashboards.py`: "Error ratio" is a chart; the 45s case is no longer a failed widget.
- `test_gcp_estate_measure.py`: the ratio condition in the subset converts.
- `test_live_findings.py`: the browser guard fixture now patches `webbrowser.open` directly (mypy).

## Left, and why

- Log fields NRQL cannot name as written: `jsonPayload.@type` (5 conditions) and `labels."a.b/c"` (1).
  NRQL accepts backticks for these, but `core/emit/nrql.identifier` rejects them by design. A narrow
  `quoted_identifier` (pattern `[A-Za-z0-9_.@:/-]+`, wrapped in backticks) would unblock them; it is
  shared safety code, so it needs your decision. Also unconfirmed: the attribute names of forwarded logs.
- `log_id()` is matched on the end of `logName` (a LIKE pattern cannot hold the `%` of the encoded slash).
- Regexes with real syntax need `RLIKE`; the IR has no regex match yet (a `regex` flag on the filter classes
  would change every saved IR dump unless omitted when false).
- OR across different labels needs an OR-capable scope in the IR.
- `metadata.system_labels.*` (6 conditions, 1 widget): needs a live check of the attribute names.
- SLO selectors: the SLO objects are not extracted from Google; the reason now says so.
- Not in this area (other threads): aligners (9), reducers (2 conditions, 31 widgets), second stage (34 widgets),
  widget types (68), PromQL, MQL, uptime-check alerts, multi-series triggers.
