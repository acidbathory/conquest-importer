# Datadog dashboards, steps 1 and 2 (2026-11-11)

Branch `improve/dd-dashboards` from `integrate/1111`. New package `src/importer/sources/datadog/dashboards/`
(loaded by `import_subpackages`, registers `convert-dashboards --source datadog`, its rules, playbooks and
taxonomy). Plan: `docs/research/datadog-dashboard-feasibility.md` (branch `research/dd-dash-measure`).

## Measure (30 real dashboards, `tests/fixtures/datadog/real/dashboards`)

Data widgets exclude notes (62 notes and free text, all converted as markdown).

| | Before | After |
|---|---:|---:|
| Dashboards read | 0 (no dashboard converter) | 30 (27 emit; 3 are all placeholders) |
| Data widgets | 454 | 454 |
| Converted | 0 | 123 (27.1%) |
| Placeholders | 454 | 331 |
| Template variables created | 0 | 24 |

Placeholders by reason (first reason of each widget):

| Reason | Widgets |
|---|---:|
| query function (top, rollup, as_count, timeshift ...) | 82 |
| widget type with no converter yet (heatmap, check_status, image ...) | 77 |
| formula over several queries, or a function over a formula | 71 |
| several queries or requests in one widget | 42 |
| reads logs | 38 |
| reads cloud_cost / network_device_flows / process | 8 / 7 / 2 |
| query_value aggregator `last` | 4 |

Every converted chart is approximate; none is exact. The 3 synthetic dashboards convert without error (the
1500-widget one is capped at the page limit and says how many were left out).

## What converts

timeseries (line, area, bars as area), query_value (avg, sum, min, max), toplist, query_table over one plain
metric query; note and free_text as markdown; groups as pages (two or more) or headings; tag template variables
as NRQL dashboard variables with `tag IN ({{name}})`; a variable with no tag key as its default with a note.
Functions, formulas, several queries, logs and other widget types are placeholders with a reason, a
remediation rule and a playbook (12 rules). Nothing is counted as converted unless it has a query.

## Pinned output: new files only

No existing pinned file changes (nothing registered in the improvement measure, so `baseline.json` and the
golden files are untouched; monitor output is unchanged). New files the owner may want to pin:

- `tests/fixtures/datadog-dashboards-expected/` (not created here; the pin in the test is the measure above)
- `tests/test_datadog_dashboards.py::test_the_real_dashboards_measure_is_pinned` pins 30 / 454 / 123 / 331 / 62.

Registrations left for the owner: a hash file for the dashboard IR/NerdGraph output of the 30 dashboards
(`dashboard-output-hashes.json` style) and a `PROFILES` / `SOURCE_LABELS` entry for the report if wanted.

## Requests for frozen or shared files (none applied)

- `sources/datadog/metric.py`: `_Terms` is imported by name from the dashboard package; please expose it as
  `Terms`. `sources/datadog/models.py`: `_files` and `_read` are imported the same way; make them public.
- `core/emit/nrql.py`: dashboards need a public way to build a WHERE clause with a dashboard variable. The
  package writes a marked `attr = '<mark>'` through `TagFilter` and replaces it with `attr IN ({{name}})` (the Azure
  converter does the same). A `TagFilter` field `variable` would remove the string step.
- `table` is an NRQL word; a tag named `table` is written unquoted (`table IN ({{table}})`), as the monitor path
  does. `identifier()` could backtick reserved words (seen in the Cassandra dashboards).

## Not verified

- Offline only. No query was run against New Relic; the dashboard variable field names are the emitter's
  UNVERIFIED NerdGraph names. Metric and tag names are assumed unchanged (`datadog-same-names`) unless
  `name-map.json` says otherwise.
- `data_path` uses the monitor path's values (`name-map`, `datadog-same-names`), not the word `unknown` from the
  brief: the monitor path never sets `unknown`, and the report already explains these two.
- A variable with a `*` default is created with default `["*"]` (all values); that `*` means "all" in the
  Azure converter too, but it is not confirmed for NerdGraph.
- `sum` over a gauge and a prefix-less variable's default are approximations that are labelled, not checked.
