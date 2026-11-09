# Proposal: convert Grafana dashboards into New Relic dashboards

Branch `improve/grafana-dashboard-convert` (from `integration/all-threads`, rebased on its tip
`2aa1f8f`). Status: waiting for the owner. Nothing here was run against a live Grafana or New
Relic account, no network was used, no Terraform was run. Dashboards are still created private by
the existing `apply-dashboards`; that path is untouched.

## What changed

New sub-package `src/importer/sources/grafana/dashboard_convert/`, auto-loaded by
`import_subpackages`, and `src/importer/commands/grafana_dashboard_convert.py`.

- **Registry.** `DASHBOARD_REGISTRY["grafana"]`, so `importer convert-dashboards --source grafana`
  already works. The extra command `convert-grafana-dashboards --in <folder> --out <folder>
  [--name-map file]` is kept because the registry path cannot take a name map and cannot add a
  closing section to `report.md`.
- **Queries** (`promql.py`, `nrql.py`, `legend.py`). PromQL and LogQL become NRQL as the research
  note describes: selectors and `avg|sum|min|max|count`, `rate`/`irate`/`increase`,
  `histogram_quantile` to `bucketPercentile`, `topk` to `FACET ... LIMIT n`, arithmetic with
  `filter()` around a term that has an extra condition, LogQL counts and rates, `TIMESERIES AUTO`
  or the fixed per-query interval, legend format to `AS` or a `FACET`. All names go through
  `identifier`, all literals through `quote_string`, a variable becomes `{{name}}` and the
  result passes `check_raw_nrql`.
- **Panels** (`panels.py`, `display.py`). Line, area (stacked), billboard (stat, gauge), bar (bar
  gauge, bar chart), pie, table, a log table, markdown. Unit, decimals, axis limits, legend,
  stacking and thresholds by colour family. Every other panel is a visible "Not migrated"
  placeholder with the reason and the first 100 characters of the original query.
- **Variables** (`variables.py`). `label_values` to an `nrql` variable over `uniques()`, custom to
  `enum`, text box to `string`, multi and include-all to a multi-select variable that defaults to
  all, a constant written into the queries, interval and data source variables dropped.
- **Layout** (`layout.py`). 24 to 12 columns by halving edges, a heading per row or tab,
  collapsed rows opened, sections stacked, overlaps moved down.
- **Name map** (`name_map.py`). Optional JSON `{"metrics": {name: {"event", "attribute"}}}`, read
  as strictly as the Dynatrace metric map; `--name-map` or `IMPORTER_GRAFANA_NAME_MAP`.
- **Data prerequisite.** Every dashboard's first reason says the data must arrive by remote
  write, OTLP or Alloy under the same names; the closing section of `report.md` lists the metric
  names used.
- **Rules.** 12 remediation rules and taxonomy phrases for the converter's reasons, 28 notes as
  approximations; all anchored on `grafana dashboard convert`.
- **Docs.** New section "Grafana dashboard conversion" in `docs/what-migrates.md`; lessons in
  `docs/improvement/lessons.d/2026-11-09-grafana-dashboard-convert.md`.

## Panel conversion rates on the fixtures

88 panels in 12 files: the 5 research fixtures and the planner's `hostile/` folder (40 panels),
and 6 new hand-made dashboards (48 panels): Node exporter style with rows, Kubernetes style with
variables, Loki logs, SRE stat with thresholds, repeated panels, hostile.

| | Panels | Widgets made |
|---|---|---|
| Before this branch (no Grafana dashboard converter) | 88 | 0 |
| The plan's verdict (T1 to T3 convertible) | 88 | 44 (50%) |
| After: converted widgets | 88 | 48 (55%) |

All 44 panels the plan calls convertible produce a widget; none is refused by the converter on
these fixtures. The 4 more are gauge (2), bar gauge and logs panels, which the plan counts as
"extra widget types" and the IR can express. The other 40 are placeholders. Per file (plan
convertible, widgets after): easy-node-overview 5, 5; hard-service-mixed 0, 0; hostile 1, 1;
legacy-rows-v12 2, 2; v2-small 2, 2; planner hostile folder 5, 5; node-exporter-rows 6, 8;
kubernetes-variables 6, 6; loki-logs 3, 4; sre-stat-thresholds 4, 5; repeated-panels 4, 4;
hostile-convert 6, 6.

Most of the 40 placeholders come from a few shapes that real dashboards use constantly and that
the planner (not this branch) keeps manual: `container!=""` (2 panels), `code=~"5.."` and
`device!~"lo|veth.*"` (5 panels), a variable that depends on another variable (the node exporter
`instance` variable and the Kubernetes `pod` variable), transformations, other data sources,
library panels missing from the export, `offset`, parser stages. See requests 5 and 6.

## How it was checked

- 267 tests in 8 files `tests/test_grafana_dashboard_convert_*.py`: exact NRQL for each shape,
  display options, variables, whole dashboards, layout, the command, the name map, rules.
- Consistency (`..._consistency.py`): 80 query strings (69 PromQL, 11 LogQL) go through the
  planner's classifier and the converter, as a chart and as a single value. Every query the
  planner reads at tier 1 to 3 produces NRQL, and no query it refuses is converted. Every
  convertible panel on both fixture folders produces a widget; every other panel produces a
  placeholder whose first reason is the planner's.
- Every converted dashboard on all fixtures is emitted to NerdGraph and Terraform, and the
  Terraform is checked against the provider schema names (as `test_dashboard_ir_emit.py` does).
- Hostile input: injection text in titles, legends, queries, variable names and values, control
  and bidirectional characters, html in text panels, a 5,000 character legend, 9,000 character
  names, 5,000 parentheses, 1,200 matchers, 1,100 operands: refused or valid, each under a
  second.
- 61 hand-made mutants; 15 survived the first tests, each got a test, 0 survive now.
- `tests/test_dashboard_ir_byte_identity.py` and the other dashboard tests pass untouched.
- `rm -rf .mypy_cache; sh scripts/improve/gate.sh`: ruff, ruff format, `mypy --strict src tests`,
  vulture, 4,986 passed and 24 skipped, improve-measure against the baseline, golden files
  untouched: **GATE PASSED**.

## Risks

- **Silent emptiness.** A widget over a metric that is not in New Relic shows nothing, not an
  error. The data note and the metric list in the report are the only warning until the data
  check for Grafana exists.
- **Counter semantics.** `rate(sum(x), 1 second)` assumes New Relic stores the counter as deltas
  (datapath note, DOCS D8, D9). Data that arrived another way (OTLP cumulative, a collector that
  re-cumulates) gives other numbers.
- **Gauges across series.** `sum(gauge)` becomes `sum(gauge)` over a bucket, which adds every
  sample in the bucket, not one value per series; `avg` of a flat selector averages all samples.
  Every widget is marked approximate for this reason.
- **Stat panels** show the value over the whole time range (or `latest` for a flat selector),
  Grafana showed the latest point of the aggregate.
- **Text panels** carry the customer's markdown as written; only html tags are removed. Whether
  the New Relic markdown widget sanitises links and inline html is not known.
- **Placeholders** show the first 100 characters of a query in the dashboard; they are the
  customer's own queries in the customer's own private dashboard.
- **Layout.** Heights use a factor of 1/2 and are clamped at 12 by the IR; the real row height
  in New Relic is unknown.

## UNVERIFIED (nothing here was run against New Relic)

1. The `units.unit` strings (`PERCENTAGE`, `BYTES`, `BYTES_PER_SECOND`, `BITS`, `BITS_PER_SECOND`,
   `MS`, `SECONDS`, `REQUESTS_PER_SECOND`, `HERTZ`).
2. `rate(sum(x), 1 second)` on remote-write counters, `rate(sum(x), 5 minutes)`, `bucketPercentile`
   on a `_bucket` name, `latest(metric)`, `count(metric)`.
3. `filter(sum(x), WHERE ...)` inside `rate(...)`; `LIMIT MAX` and `LIMIT n` before `TIMESERIES AUTO`.
4. `IN ({{var}})` with an unquoted variable, `NOT IN`, and the `number` strategy for a variable
   used in arithmetic.
5. The NerdGraph and Terraform variable field names (as in the IR note).
6. `uniques(label)` with `WHERE metricName = '...'` for a Prometheus label; `metricName` equal to
   the Prometheus name.
7. Label names as attributes, and on `Log`: `message`, `timestamp`, and Loki labels as attributes.
8. Billboard `critical` and `warning` meaning "at or above"; a stacked area stacking.
9. The grid factor; a markdown widget used as a heading.
10. The reading of v2 display options (`vizConfig.spec.fieldConfig` and `options`) and of v2
    variables (`spec.current`, `spec.options`): from the code comments the planner relied on, no
    live export. The custom-variable `key : value` form needs spaces around the colon here.
11. That an interval variable's value is not needed: `[$interval]` becomes the automatic bucket.

## Requests (outside this branch's paths; nothing was edited there)

1. `sources/grafana/translate.py`: public functions for "read a Prometheus selector with
   matchers into conditions" and "read a LogQL count into conditions". `dashboard_convert/reuse.py`
   borrows five private names (`_Ctx`, `_Fail`, `_matchers`, `_outer`, `_regex`) next to public constants;
   the planner borrows `_Ctx` and `_Fail`. One public function would remove both.
2. `core/emit/dashboard.py`, `placeholder_text`: for an `UNSUPPORTED` widget that has `text`, write
   that text, and raise the 200 character cap on the reasons. Today the query is cut to what is
   left of 200 characters. A placeholder made `MARKDOWN` would be counted as converted by
   `core/dashboard_report.py`, so this branch keeps `UNSUPPORTED`.
3. `core/`: one `pack()` for overlapping widgets. `sources/gcp/dashboards.py` and
   `dashboard_convert/layout.py` each have one (third use will be Dynatrace).
4. `core/dashboard_report.py`: a per-source note hook, so the data prerequisite is a report note
   and not the first reason of each dashboard.
5. `sources/grafana/dashboards/query_tier.py`: `label!=""` as "has the label" and `label=~".+"`
   (both are `IS NOT NULL`); they keep several real panels manual.
6. The same file: decide on RLIKE. `code=~"5.."` and `device!~"lo|veth.*"` are the most common
   regex matchers in the fixtures. Either verify RLIKE in the sandbox and let the planner pass
   RE2 regexes, or keep them manual.
7. `sources/grafana/dashboards/load.py`: a public `read_json_file`; `dashboard_convert/raw.py` has
   a small copy (size cap, UTF-8, `RecursionError`) because the other agent is changing the
   private one.
8. `cli.py` (integrator): optional `--name-map` on `convert-dashboards` for the registry path, or
   leave the dedicated command. A Grafana data check (`check-grafana-data` in the datapath note,
   section 6.8) is the missing half of the data prerequisite.
