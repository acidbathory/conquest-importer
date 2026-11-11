# Remediation cookbook: what to do when New Relic cannot do it one-to-one

For practitioners finishing a migration into New Relic. The importer converts what it can and stops where
a conversion would give a wrong number. This guide covers the most common stops, measured on a test estate of
50 community Grafana dashboards and 55 alert rules (2026-11-10). For each blocker there are ranked methods; the
first is the one to try first. Every method says what it needs, what it changes, what it costs, and how to prove it.

This file is generated from `src/importer/core/cookbook/data/*.toml` by
`uv run python scripts/improve/render_cookbook.py`; edit the data, not this file.
`importer plan-remediation` prints the recommended method for each unconverted item.
`importer cookbook --in <converted folder>` writes the same guide as an HTML page with only the blockers your
run hit (the guided menu opens it after a create); `importer cookbook --all` writes the whole guide.

## One-page decision guide

Ask these five questions in order. They pick the family of method before you look at a single query.

1. **Is it an alert?** Alerts are stricter than dashboards: no subquery, no JOIN, no LIMIT, no TIMESERIES, one
   number per signal. If a dashboard method uses a nested query or JOIN, look for the alert method (family D) or
   change the data at the source (family A).
2. **Do you control Prometheus, Alloy or the OpenTelemetry Collector?** If yes, family A (change at ingest) gives
   exact numbers and simple queries, but only for new data and at the cost of a deploy. If no, stay in NRQL
   (families B, C, D).
3. **Is history needed?** Family A does not rewrite history. Families B, C and D work on data already in New Relic.
4. **How much delay can the alert take?** Every condition adds its aggregation window plus delay (a live firing test
   measured about 5 minutes more than Grafana's evaluation). Sparse data needs a different streaming method.
5. **Is the difference acceptable?** Family F means writing down what is different and getting the owner to agree.
   Some things have no New Relic form at all; the section for each blocker says so.

Families: A change at ingest, B rewrite in NRQL, C restructure, D redesign the alert, E use a New Relic native
feature, F accept and document the difference.

### Status labels

- **VERIFIED-VALUE**: every NRQL in the method was accepted by New Relic (read-only NerdGraph) and a result matched
  the arithmetic on test data (two independent ways of getting the number agree).
- **VERIFIED-VALID**: every NRQL in the method was accepted by New Relic; no value was compared.
- **DOC-ONLY**: read in public documentation; nothing was run (collector files, Terraform, GraphQL mutations,
  Prometheus rules). Test them in a sandbox first.
- **UNVERIFIED**: from memory or inference; the evidence line says so.

The run record is `docs/improvement/cookbook-verification.json`; `scripts/improve/verify_cookbook.py` rewrites it.
Test data carries `ckg_test=true`. Queries below use the real metric names of that estate; replace them with the
customer's.

### Recommended first method per blocker

{{SUMMARY_TABLE}}

## Contents

{{TOC}}
