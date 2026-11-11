# Next actions: the ranked backlog

Maintained by the overseer (`docs/dev/overseer.md`); the owner decides. Every number here was
re-measured on the date in the last column, with the command in "How the numbers were taken". Do
not copy a number from a research note: measure it again and put the date next to it. Before
adding an item, check "Done" below, `docs/improvement/live-evidence.json`, the scoreboard history and
`git log --oneline origin/main`.

Effort: S under a day, M a day or two, L more. Risk: what goes wrong if done badly.

## Done

Never re-propose these. Commits are on `origin/main` unless a branch is named.

| Item | Where | Effect |
|---|---|---|
| Chained, query and `query_result` variables converted | `improve/chained-vars`, merge 94e6574 (d28a40c) | Variable gate cut from 389 to 70 panels on the 19 real dashboards (census 2026-11-10); the old "8 widgets end to end" no longer holds |
| Page splitting of dashboards over 150 widgets | `improve/page-split`, merge 2842085 (f06d23a) | Large dashboards emit and apply every page |
| `sum()` / `count()` of a gauge is manual unless the name map proves one series | `improve/sum-gauge-manual`, merge 7e2aeb2 (d28d43e) | Removes a measured wrong answer (4x to 41x too high) |
| Remediation playbooks and `plan-remediation` | `improve/remediation`, merge f3118a8 (3f5fe29, 6b36cdd, e7041b9) | A person gets steps for every unconverted item |
| Grafana prom-hints (metric types and series keys from Prometheus) | `improve/prom-hints`, merge 1b47097 (838c47d) | Source of the name-map hints |
| Guided menu, one pass and one `yes`, report opened before and after | `improve/menu-one-go`, merge 7f0c9e8 (58cf216); `improve/menu-open-report`, merge b39a86a | One command per source |
| Branded reports | `improve/report-branding`, merge fd53e69 | Pair-named reports with logos |
| Alert firing test (a converted Grafana rule opened and closed an incident) | 24a57ab; record in `docs/improvement/live-evidence.json` | First live proof that a converted alert fires |
| Counter and gauge live round trips, dashboards accepted by New Relic | `docs/research/live-grafana-*.md`; `live-evidence.json` | Query semantics measured against real data |

In progress elsewhere (do not dispatch a second copy): the parity verifier, branch
`improve/parity-verifier` (it will write `parity.json`; the contract is in `docs/dev/overseer.md`).

## Ranked backlog

| # | Item | Value (current measurement) | Effort | Risk | Depends on | Measured on |
|---|---|---|---|---|---|---|
| 1 | Comparison filters `x > n` (`cmp.filter.vector_scalar`) | 108 of 412 PromQL panels blocked, 14 only by this, 12 convert end to end at once; remediation top 5 puts "comparison or vector matching" first (unlocks 48 of 73 items) | M | Subquery or FACET math must match Prometheus semantics | A live check with the sandbox harness; the parity verifier to prove it | 2026-11-10, census on the 19 real dashboards |
| 2 | Use prom-hints series keys to convert gauge `sum` / `count` | `shape.sum_of_gauge` blocks 100 panels, 43 only by this, 36 convert end to end once hinted (36 items, remediation top 5 number 2). No converter change: running `grafana-prom-hints` and supplying the name map | S | A wrong hint gives a wrong total; the live read needs the owner's yes | Owner approval for the read-only Prometheus read | 2026-11-10 |
| 3 | Cheap parser fixes: single-quoted strings (7 panels, 6 only by this), upper-case aggregates (3), `clamp_min` | About 6 to 10 more panels end to end | S | Low | none | 2026-11-10 |
| 4 | Remaining manual variables (70 panels) | Remediation top 5 number 4: unlocks 68 of 92 items. Read the reasons first: ad hoc filters, regexes the converter cannot check, non-Prometheus data sources | M | Wrong filters | none | 2026-11-10 |
| 5 | Range and time functions (`fn.time`, `avg_over_time`, `sum_over_time`, `offset`, `round`) | 94 items; remediation unlocks 25; `offset` 13 panels, `sum_over_time` 10, `avg_over_time` 9 | M | `_over_time` is not `rate`; windows must match | Parity verifier | 2026-11-10 |
| 6 | Counter name-suffix guess (`shape.counter_suffix_bare`) | 27 panels blocked, 2 only by this | M | Wrong rate if the type guess is wrong | prom-hints metadata | 2026-11-10 |
| 7 | Transformations (the NRQL-expressible subset) | 21 panels; remediation top 5 number 5 unlocks 14 of 65 | M | Medium | none | 2026-11-10 |
| 8 | Panel types gauge, bargauge, barchart | 12 panels | M | Needs a New Relic widget mapping the emitter lacks; emitter files are frozen (request via proposal) | Integrator | 2026-11-10 |
| 9 | `without()`, `on()` / `ignoring()` joins, `group_left` | 63, 44 and 43 panels; not expressible in the query layer alone, so guidance or ingest-time label enrichment (HANDOFF next up 3) | L | High: changes what is ingested | Parity verifier; owner decision | 2026-11-10 |
| 10 | Live check of notifications and of Dynatrace | `not_proven` in `live-evidence.json`: nothing about either has run in New Relic | M | Live system: create-only, `ckg-`, owner approval and cost first; token rotation first | Owner go | 2026-11-10 |
| 11 | Report headline by widgets, not dashboards created | The report counts a dashboard as migrated when it can be created; by widgets the scoreboard shows 178 charts and 306 placeholders on the 19 dashboards | S | Changes pinned report output; hash files need the owner's approval | Owner decision | 2026-11-10 |
| 12 | CloudWatch leftovers: composite alarms (2), metric math (1) | Baseline 27 of 32 converted (3 more are manual by design) | S | Low | none | 2026-11-09 (baseline.json) |

Not now: recording-rule runner (no customer needs it).

## Cycle 2026-11-11 (re-measured from fixtures, outputs in /tmp/overseer/out)

Backlog rows 1 to 3 above were measured on 2026-11-10 before the PromQL engine merge (0782631);
re-measure them with the census before dispatching. Azure and Datadog are no longer paused or
blocked: both sources are on main. Fresh numbers, 2026-11-11: GCP 170 of 350 conditions emitted
(127 manual), 828 data widgets converted and 320 placeholders on 109 dashboards; Azure metric
alerts 11 of 22 (offline fixtures); Datadog 50 of 70 real monitors; Grafana fixtures 7 of 21
alert rules. Exact is 0 everywhere. Remediation top 1 by source: GCP template placeholders (18),
Azure unreadable rule fields (4), Datadog query functions (7).

Next three dispatches, in order:

| # | Item | Value | Effort | Risk | Depends on | Measured on |
|---|---|---|---|---|---|---|
| A | Hints step in the guided Grafana flow (run `grafana-prom-hints`, feed series keys to the converter) | Unlocks gauge `sum`/`count` panels (about 354 estate panels per earlier note, re-measure) | M | Low, read-only against the customer Prometheus | none | 2026-11-11 (to re-measure with census) |
| B | Offline differential harness for PromQL rewrites (seeded data, verdict ladder) | Turns approximate into evidence; feeds `promql_rule_cases.json` | M | Low | none | 2026-11-11 |
| C | GCP template placeholders (18 conditions) and unsupported filters (17) | Largest GCP blocker pair, 35 of 127 manual | M | Low | none | 2026-11-11 |

Left out: conditional exactness (needs B first), Datadog D4 to D7 (large, wait for owner's view of
the Datadog baseline), Azure workbook `{Param}` (71 placeholders, after C), Azure subscription link
(owner decision).

## Waiting for the owner

- Pinned-output diffs on any branch that changed `tests/fixtures/*hashes.json`.
- Rotate the Dynatrace and Grafana tokens.
- Decide the headline percentage (item 11).
- Each live run, with the cost named first.

## How the numbers were taken

```
importer convert-dashboards --source grafana --in <extract-all/dashboards> --out /tmp/ov/real
importer scoreboard --in /tmp/ov/real --no-write      # widgets and remediation top 5
uv run python scripts/analysis/promql_census.py <extract-all/dashboards> \
    --alerts tests/fixtures/grafana tests/fixtures/grafana-rules2
```

On 2026-11-10 the 19 real dashboards gave 178 chart widgets, 306 placeholders (146 text panels
are neither); the census gave 174 of 412 PromQL panels converting end to end. A figure of 228
converted that was quoted in conversation did not reproduce with these commands; the difference is
unexplained and should be settled by the next measurement.

## From the field research (to fill in)

<!-- MARKED SECTION. docs/research/observability-migration-best-practices.md is expected in the
integration later. When it appears: read its gap list, add one row per gap to the Ranked backlog
with value/effort/risk/dependency and a measurement date, and mark the rows "field". Do not copy
the note; link it. -->
