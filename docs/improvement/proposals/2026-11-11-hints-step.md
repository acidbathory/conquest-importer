# Proposal: hints step in the guided Grafana flow (2026-11-11)

Blocker: `shape.sum_of_gauge` (gauge `sum()`/`count()` kept manual unless the name map proves one series).

## Change
- `grafana-prom-hints` gains `--prometheus-url` (direct read of a Prometheus; loopback, or https with
  `--allow-host`; same three read-only paths and caps). `--datasource` still works; exactly one is required.
- Guided Grafana flow: asks for a Prometheus URL; with one it runs hints after the read step and passes
  `--hints` to `convert-grafana-dashboards`; without one it says what is lost. A failed read only drops the hints.

## Numbers (offline, fixtures; upper bound)
Census (`promql_census.py`): sum_of_gauge is step 1 of the greedy order: 14 of 19 query-layer panels converts
(`grafana-dashboards`, today 11), 35 of 39 (`grafana-dashboard-convert`, today 25).
Widgets converted, with a hints file that declares every named metric one series (no real Prometheus):

| fixture | before | after |
|---|---:|---:|
| grafana-dashboard-convert | 31 converted / 17 placeholders | 41 / 7 |
| grafana-dashboards | 12 / 28 | 15 / 25 |

Real gains depend on each Prometheus: a gauge with several series needs a group-by on its key labels.
Baseline unchanged (fixtures carry no hints). No pinned output changes; no frozen file touched.

## Notes
- `sources/grafana/prom_hints.py` now imports `check_direct` from `importer.parity.source` (no cycle). If a
  purist layering is wanted, move `check_direct` to `core/` (integrator).
- Existing Grafana menu tests gained one scripted Enter (the new prompt).
- Gap: no live Prometheus was read; MockTransport only.

## Review follow-up
- Series are looked for over 7 days (was 1 hour); the hints file, menu prompt and widget note say
  "one series seen in the last 7 days on <that Prometheus>". Hinted widgets are approximate.
- The guided flow validates the URL (loopback only, 3 tries, Enter skips).
- The proxy and direct getters each accept only their own path shape; the direct client ignores proxy
  environment variables.
- Finding: with a multi-series hint, `sum(gauge)` already converts through the engine to a sum of the
  newest value per series (FACET subquery), which is correct; the test checks it never becomes a flat average.
