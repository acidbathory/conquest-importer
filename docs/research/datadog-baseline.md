# Datadog baseline: first useful release measured on 70 real monitors

Measured 2026-11-11 on branch `improve/datadog-source`. Offline: no Datadog site and no New Relic
account was contacted for this measurement. Reproduce with
`uv run python scripts/analysis/datadog_measure.py --markdown /tmp/out.md`.

## What was measured

The 70 integration monitors under `tests/fixtures/datadog/real/monitors/` (BSD-3-Clause, copied from
`DataDog/integrations-core`, see `tests/fixtures/datadog/SOURCES.md`). The synthetic hostile cases are
not counted. "Converted" means confidence exact or approximate, as the scoreboard counts; no Datadog
monitor is ever exact (a query-bearing object is never exact). Placeholders are never counted as
converted, and this source produces none. Every converted monitor was also emitted (query builds, limits
accepted, NerdGraph payload built) and every other monitor has remediation steps.

## Result

Monitors: 70. Converted (approximate or better): 50. Manual: 20. Unsupported: 0. Emitted as NRQL conditions: 50. Without remediation steps: 0.

| Type | converted (approximate) | manual | unsupported |
|---|---:|---:|---:|
| database-monitoring alert | 0 | 1 | 0 |
| event-v2 alert | 0 | 1 | 0 |
| log alert | 5 | 4 | 0 |
| query alert | 45 | 13 | 0 |
| service check | 0 | 1 | 0 |

| Reason a monitor is not converted | Monitors |
|---|---:|
| query function | 7 |
| anomaly monitor | 2 |
| change alert | 2 |
| multi-query formula monitor | 2 |
| window is longer than the six hours New Relic accepts for an aggregation window | 2 |
| cumulative window | 1 |
| forecast monitor | 1 |
| database monitoring monitor | 1 |
| service check monitor | 1 |
| event monitor | 1 |

| Note on a converted monitor | Monitors |
|---|---:|
| Datadog evaluates the window every minute; a sliding window of {N}s is used, which costs e | 48 |
| metric names are UNVERIFIED: no source says how a Datadog metric is named in New Relic. Th | 45 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name ({LIST | 26 |
| the formula is evaluated on window aggregates (a ratio of averages or sums); Datadog evalu | 10 |
| as_count() is read as a sum of delta counts; the metric kind is not known (set it in name- | 6 |
| the time aggregator min is applied to raw points in New Relic, to space-aggregated rollup  | 5 |
| Datadog log search and a New Relic WHERE clause differ in case handling and in how free te | 5 |
| message contains {N} @handles: destinations are not created from them (milestone D4); each | 4 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name (host) | 3 |
| log attribute names are UNVERIFIED: source, service, type, data.broadcast_id, resend.domai | 3 |
| the time aggregator max is applied to raw points in New Relic, to space-aggregated rollup  | 2 |
| the {N}s window is longer than an hour: aggregation windows above {N}s were accepted in th | 2 |
| Datadog evaluates the window every minute; a New Relic aggregation window of {N}s is evalu | 2 |
| the time aggregator avg is applied to raw points in New Relic, to space-aggregated rollup  | 1 |
| log attribute names are UNVERIFIED: source, cisco_aci.severity, cisco_aci.code, cisco_aci. | 1 |
| log attribute names are UNVERIFIED: service, Severity, host are assumed to arrive under th | 1 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name (statu | 1 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name (gpu_u | 1 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name (supab | 1 |
| tag names are UNVERIFIED: tags are assumed to arrive as attributes of the same name (serve | 1 |
| no data: Datadog notifies after {N} minutes without data; a New Relic condition can open a | 1 |

## How to read this

- This is a sample of Datadog's own integration templates, not a customer estate. The note
  `datadog-verified.md` section 3 says the integration corpus under-represents composite, SLO, outlier,
  synthetics, RUM and trace-analytics monitors, and over-represents anomaly monitors (46 of 339 in the full
  corpus, 2 of these 70). Do not read 71 percent as a prediction for an estate.
- "Converted" is a statement about the translation, not about data. No converted condition has been seen
  firing, and the metric names are an assumption (below).
- The query-function line covers: as_rate (2), abs (1), cutoff_min (1), clamp_max (1), fill (1) and a nested
  space aggregation after a rollup (1). as_rate, fill and the rate family are planned for milestone D5.
- The two log monitors with a one-day window are manual because New Relic aggregation windows were only
  accepted up to six hours in the owner's live test; splitting a day into several windows that must all
  breach would be a different rule.
- The 2 multi-query formula monitors are log monitors written as `formula(...)`. The database monitoring monitor
  also uses a formula but is counted under its own type.

## Design choices that change the numbers

| Choice | Effect | Why |
|---|---|---|
| `.as_count()` is converted when the time aggregator is `sum` and the metric kind is not gauge or rate | 6 monitors converted that the build plan put in D5 | It is the most common pattern in the corpus (324 uses); every such monitor carries the note that delta arrival is UNVERIFIED |
| Arithmetic between series (ratios) is converted to one NRQL with several selects | 10 monitors | The IR already holds formulas; the note says the ratio is of window aggregates |
| A low-priority (P3 to P5) monitor with only a critical threshold becomes a warning term | recorded per monitor | A policy choice; the original priority is in the description |
| Windows from one minute to six hours | 2 log monitors manual (one day) | NerdGraph limit; see runbook |

## UNVERIFIED (not seen in anything read or run)

1. How a Datadog metric or tag is named in New Relic. The converter assumes the same name on the `Metric`
   event; every converted monitor says so and `check-datadog-data` is how it gets confirmed.
2. Whether an aggregation window above one hour (up to six) and a 60 second slide-by are accepted when the
   condition is created. The owner's live test is the source for six hours; the slide-by was not tried.
3. `.as_count()` on a metric that arrives as a delta count, and the Datadog empty-window behaviour.
4. Datadog log search against a New Relic WHERE clause: case handling and free-text splitting.
5. That `@handle` destinations can be rebuilt (milestone D4).

New Relic accepted every NRQL shape this source emits as a query (read-only NerdGraph check on 2026-11-11);
that says nothing about the same query inside a condition.
