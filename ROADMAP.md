# Roadmap: migrating other observability platforms into New Relic

Status as of 2026-10-02. This describes what we can do, what we need, and how we measure progress.
Nothing here is a delivery promise: the goal of the next phase is to **measure where migration
fails**, source by source, on real data.

## Principles
1. **A universal framework, not universal coverage.** One shared intermediate format and shared
   outputs; one subproject per source. No source will convert 100%.
2. **Honest labels.** Every item is exact, approximate, manual or unsupported, with a reason.
3. **Safe by default.** Nothing is created enabled; writes need explicit confirmation and an
   allow-listed test account; the customer runs the export so we never hold their credentials.
4. **Data first.** Converting an alert or dashboard is pointless if its data is not in New Relic
   under the names the conversion expects. Ingestion setup is part of every source's scope.
5. **Measure on real data.** Synthetic results are not evidence. Every source needs real exports.

## What exists today
| Capability | AWS CloudWatch | Datadog |
|---|---|---|
| Alerts to New Relic conditions (API + Terraform) | Built, sandbox-verified | Built, sandbox-verified; input is synthetic (network blocked) |
| Ingestion setup (Terraform) | Built, verified with real data (DynamoDB confirmed) | Not started |
| Dashboards | Built offline on synthetic input (Terraform validates; not created in New Relic) | Not started |
| SLOs, synthetics, logs | Not started | Not started |

Shared pieces: intermediate format, NRQL/HCL/GraphQL emitters, gap report, guarded `apply`,
test fixtures with golden files, threat model. See `docs/`.

## Sources: what we can do, and how hard it is
### Tier A: hyperscalers (AWS, GCP, Azure)
Why they are the better starting point: public, documented, stable APIs; the customer can export
with a standard CLI; the partnership gives access to test accounts and engineering contacts. Note
that open APIs make **reading** easy; the hard part is still **translation** (semantics and
data-model mapping), which a partnership does not remove.

| Source | Alerts | Dashboards | Ingestion | Notes |
|---|---|---|---|---|
| AWS CloudWatch | Done (partial) | Built offline, synthetic input | Done for Metric Streams | Add percentiles, more services verified |
| GCP Cloud Monitoring | Alert policies with MQL/PromQL conditions | Dashboard JSON | New Relic GCP integration | Research needed: New Relic's current GCP ingestion options |
| Azure Monitor | Metric and log alert rules | Portal dashboards / Workbooks | New Relic Azure integration | Research needed: streaming vs polling |

### Tier B: SaaS platforms (each its own subproject)
| Source | Alerts | Dashboards | Ingestion | Specific risk |
|---|---|---|---|---|
| Datadog | Built on synthetic input | Not started | Not started | Needs API access (blocked on corporate network) or customer exports |
| Dynatrace | Metric events, Davis anomaly detection | Dashboard JSON | OneAgent to New Relic agents | Davis AI/anomaly logic has no direct equivalent; proprietary query language |
| Grafana | Alert rules | Dashboard JSON (easy to get, hard to convert) | Depends on the data source behind it | A visualization layer over other systems: plugins, transformations, mixed data sources; PromQL/LogQL/other query languages |

Grafana note: converting a Grafana dashboard usually means also deciding where its data comes
from (Prometheus, Loki, Elasticsearch, CloudWatch, ...). Treat "Grafana" as several projects.

## Structure: one subproject per source
Done 2026-10-03 (was flat, with Datadog and CloudWatch mixed). Layout:
```
src/importer/
  core/            # IR, emitters, report, apply, ingest (shared)
  sources/
    datadog/       # models, queries, translator, fixtures
    cloudwatch/
    grafana/ ...   # each with the same small interface: load() and translate()
```
Each source gets: its own fixtures (synthetic and real-sanitized), golden tests, mapping files, a
verification log entry, and a conversion-rate figure.

## To-dos
### Foundation (before adding sources)
- [x] Refactor into `core/` and `sources/<name>/` with a common source interface (2026-10-03)
- [x] Build a `stats` command that aggregates conversion results and failure reasons across runs
      (this is how we "see where we are failing")
- [x] (alerts done, dashboard categories pending) Define a failure taxonomy: unsupported query function, missing metric mapping, unsupported
      visualization, unsupported plugin, data not ingested, naming mismatch
- [ ] Add side-by-side validation: run the source query and the converted query over the same
      period and compare results (the safety net that makes approximate conversions acceptable)
- [ ] Add usage triage: skip dashboards nobody views and alerts that never fire

### Per source (repeat for each)
- [ ] Get access: test account or real customer export (with permission), network allow-list,
      legal/terms check for using the vendor's API
- [ ] Capture real exports and add sanitized copies as fixtures
- [ ] Alerts, then dashboards, then ingestion setup
- [ ] Record conversion rate on real data and the top failure reasons
- [ ] Write up what fails and why

### Order I would suggest
1. Finish AWS (dashboards, percentiles, verify more services). Lowest risk, proves the pattern.
2. Grafana dashboards (JSON is easy to obtain, and it is where visuals are expected to fail, so it
   tells us the most about the earlier failure).
3. GCP and Azure alerts (same shape as AWS).
4. Datadog on real data once access exists; Dynatrace last (proprietary concepts).

## What we need from others
- Your manager: what exactly failed in the earlier attempt and what the target was
- IT/security: allow-list for Datadog (and later Dynatrace/Grafana cloud) APIs, or an approved
  non-corporate environment
- Partner engineering (AWS, GCP, Azure): test accounts and a contact for export questions
- Customers (with consent): real exports of alerts and dashboards, sanitized before use
- A New Relic test account for each cloud provider's integration

## Success criteria for the exploration phase
- A conversion rate per source and object type, measured on real data
- A ranked list of failure reasons per source
- A clear statement of what is automatable, what is assisted, and what stays manual
- No claim of "universal" beyond what the numbers show

## Known risks
- Query-language semantic differences can produce plausible but wrong results
- Vendor APIs and formats change; each source needs an owner
- Customers' custom metrics and naming can defeat metric mappings
- Numbers so far come from synthetic data and one small sandbox
