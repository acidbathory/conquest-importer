# Next actions: the ranked backlog

Maintained by the overseer (`docs/dev/overseer.md`); the owner decides. Value is measured where a
number exists (census 2026-11-10 on 19 dashboards and 412 PromQL panels; fixture runs on the
Grafana fixtures: 29 widgets converted and 17 placeholders). Effort: S under a day, M a day or two,
L more. Risk: what can go wrong if done badly. Re-rank after every `importer scoreboard` run.

## Ranked backlog

| # | Item | Value (evidence) | Effort | Risk | Depends on | Lever |
|---|---|---|---|---|---|---|
| 1 | Lift the "variable stays manual" gate for chained and query variables | 389 of 412 census panels carry it; lifting it alone would convert 221 (54 percent) end to end, against 8 (1.9 percent) today (`docs/research/promql-to-nrql-census.md` section 2) | M | Wrong filters if New Relic variable semantics differ; nested variables are documented but plan-dependent | Live check that the account supports nested variables (live round trip already accepted nested variables: `live-evidence.json`) | blocker by gate size |
| 2 | Comparison filters `x > n` (`cmp.filter.vector_scalar`) | 108 panels blocked, 18 only by this, 3 convert end to end now | M | Subquery or FACET math has to match Prometheus semantics | A live check with the sandbox harness | blocker by count |
| 3 | Parity verifier command (compare each converted chart or alert with the source over one window; label items "verified on <date>") | Turns "converts" into "matches"; feeds `parity.json` into the scoreboard, which has no parity data today | L | Flaky comparisons if windows and scrape intervals are not aligned; record method and tolerance | none for the offline part; live harness to run | missing measurement |
| 4 | `without()` aggregation | 63 panels blocked, 1 only by this; verdict 4 (not expressible without label knowledge), so the fix is guidance or a name-map hint, not conversion | S | Low | `grafana-prom-hints` data | blocker by count |
| 5 | `on()` / `ignoring()` joins and `group_left` info-metric pattern (ingest-time label enrichment) | 44 and 43 panels; none can be fixed in the query layer alone; HANDOFF "next up" 3 | L | High: changes what is ingested | Parity verifier; owner decision on remote write | blocker by count |
| 6 | Remediation top 5 on the Grafana fixtures: gauge `sum`/`count` guidance (10 items), interval and data source variables (7), PromQL features with no alert equivalent (3) | Items fully finished by a person once these playbooks exist: 10, 7, 3, 2, 2 of 46 unconverted | S | Low (guidance only) | none | guidance |
| 7 | Report headline by widgets, not by dashboards created (56 percent against about 43 percent) | Removes a misleading number; needs owner confirmation | S | Changes pinned report output: hash files need owner approval | Owner decision (HANDOFF open decision 2) | honesty of the metric |
| 8 | Name-suffix counter guess (`shape.counter_suffix_bare`) | 27 panels blocked, 2 only by this; needs Prometheus metadata | M | Wrong rate if the type guess is wrong; MEASURED rows exist | `grafana-prom-hints` run against a Prometheus with metadata | blocker by count |
| 9 | Live check of notifications (contact points, policies, mute timings deliver) | Listed `not_proven` in `live-evidence.json`; no live evidence for the notification path | M | Touches a live system: create-only, `ckg-`, owner approval and cost first | Owner go | live evidence |
| 10 | Grafana transformation support (the NRQL subset) | 21 panels blocked by transformations | M | Medium | none | blocker by count |
| 11 | Dynatrace live check (nothing converted from Dynatrace has run in New Relic) | `not_proven` in `live-evidence.json` | M | Owner approval; token rotation first (HANDOFF open decision 1) | Owner go | live evidence |
| 12 | CloudWatch top blockers: composite alarms (2), metric math (1), alarm actions (3 manual by design) | Baseline: 27 of 32 converted; 5 left | S | Low | none | blocker by count |

Not now: Azure (paused by the owner), Datadog (blocked network), recording-rule runner (no customer
needs it), 48-hour history seeding (HANDOFF next up 4, after the parity verifier).

## Waiting for the owner

- Review and approve the pinned-output diffs on any branch that changed `tests/fixtures/*hashes.json`.
- Rotate the Dynatrace and Grafana tokens (HANDOFF open decision 1).
- Decide whether the headline percentage counts widgets (item 7).
- Approve each live run, with the cost named first.

## From the field research (to fill in)

<!-- MARKED SECTION. docs/research/observability-migration-best-practices.md is expected in the
integration later. When it appears: read its gap list, add one row per gap to the table above with
value/effort/risk/dependency, and mark the rows "field". Do not copy the note; link it. -->

| # | Gap | Value | Effort | Risk | Depends on |
|---|---|---|---|---|---|
| F1 | (fill in) | | | | |

## How this file is used

`/oversee` re-ranks this table after each scoreboard run: an item that moved no outcome metric in
three cycles is demoted or replaced by a different lever (see "Stall detector" in the operating
model).
