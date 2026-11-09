# Grafana dashboard tiles: output diff for the hash-pinned group `convert/grafana`

Branch `improve/grafana-dash-exact`. `tests/test_emitter_byte_identity.py::test_output_bytes_are_unchanged[convert/grafana]`
fails on purpose; `tests/fixtures/emitter-output-hashes.json` was NOT regenerated. A person must read the
changes below and then regenerate.

## What changes in the output

1. Regex label matchers now convert (`code=~"5.."` becomes `code RLIKE '5..'`, `!~` becomes `NOT RLIKE`,
   `label!=""` and `=~".+"` become `IS NOT NULL`). Seven tiles in the fixtures move from manual placeholder
   to a draft widget, so their IR and NerdGraph files change.
2. Unit: Grafana `percent` (0 to 100) no longer becomes `PERCENTAGE` (New Relic shows 1 as 100%, so 50 would
   read 5000%); the unit is left out with a note. `percentunit` (0 to 1) becomes `PERCENTAGE`. The
   "Memory used" gauge in `node-exporter-rows.json` changes.
3. The reason text `Q_REGEX` changed (it now says why a pattern stays manual).

## Tiles (rows not counted; fixtures `grafana-dashboard-convert` and `grafana-dashboards`, 88 tiles)

| | exact | draft (approximate) | manual |
|---|---|---|---|
| before | 4 | 44 | 40 |
| after | 4 | 51 | 33 |

## The exact rule

A tile is exact only when it is static content copied verbatim (markdown text panel, row heading) with no note.
A query tile is never exact: the New Relic bucket differs from the Grafana step, counter or gauge is read from
the metric name, and an instant value is the latest point in Grafana but a whole-range value in New Relic.
Enforced by `may_be_exact` and a check in `Draft.widget`; tested over all fixtures in
`tests/test_grafana_dashboard_exact_rule.py`.

## Left as it was, and why

- Chained variables (`label_values(m{job=~"$job"}, instance)`): the IR forbids variables inside a variable
  query (nested variables are plan-limited). Five tiles stay manual.
- Library panels not in the export, panel types with no widget, hostile or empty panels: no information.
- Transformations, LogQL parser stages, `offset`, comparisons, CloudWatch: no correct NRQL form found yet.
- Not verified against New Relic: that `RLIKE` is full-match in dashboard widgets (documented, never run) and
  that the `PERCENTAGE` unit string is accepted.
