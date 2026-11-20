# Changelog

All notable changes to Conquest Importer. The format follows [Keep a Changelog](https://keepachangelog.com/).
The package version is still 0.1.0: nothing below has been released or tagged yet.

Every conversion is **approximate** and none has been run against production data in New Relic. Numbers
here are counts on public sample content or on one real Grafana estate; they are indicators, not
customer results. See [what migrates](docs/what-migrates.md), including "What converted does not prove".

## Unreleased (November 2026)

### Added
- **Grafana alerts: `_counter` names.** A metric whose name ends `_counter` is treated as a counter in `rate`, `increase` and their ratios, as approximate, with a note that the type was assumed from the suffix; a `gauge` type in the name map still wins. On one real estate this converts 134 more rules and changes none that already converted. Dashboards are unchanged.
- **Datadog dashboards.** A reader and converter for dashboard JSON (`importer convert-dashboards --source
  datadog`): timeseries, query value, toplist, table, markdown and group widgets; template variables as New Relic
  variables; formulas over metric queries; the common functions (`top`, `as_count`, `as_rate`, `per_second`,
  `rollup`, `fill`); `style` and `limit` on formulas; log widgets as `FROM Log` queries; image, treemap and sunburst
  widgets. On the committed fixtures 288 of 454 data widgets convert.
- **Grafana alerts.** Classic conditions written as `[value, 0]`; queries a condition never reads are ignored;
  constants inside `sum()` and `avg()` of a counter; the `mean` reducer over a counter; faceted per-series
  conditions for Prometheus rule files; label matchers: `.*`, `.+`, `!=""` (the label is set), empty-alternative negation and dotted
  hostnames; **regular expressions** as `RLIKE`/`NOT RLIKE` (new optional `regex` flag on filters);
  **`histogram_quantile`** as `bucketPercentile` (new `BUCKET_PERCENTILE` aggregation); the `max` and `min`
  reducers over a rule's range as threshold occurrences (at least once, or all), approximate. On the real estate
  this added 355 converted rules. `sum` and `mean` of a percentile stay manual.
- **Grafana dashboards.** `sum()` and `count()` over gauge series as `average(x) * cardinality(x)` for charts
  (approximate; dashboards only); `query_result()` variables; comment and quote normalisation, `round` and
  `clamp_min`/`clamp_max` with a number around a whole query.
- **Dynatrace.** Tiles built with the tile builder's `scalar: true` option are drafts instead of manual.
- **Azure.** A dropdown parameter that repeats a value is merged, and a workbook that cannot be read becomes one
  manual entry instead of stopping the whole folder; workbook time-range parameters feed the time picker.
- **AWS.** Seven more CloudWatch namespaces in the namespace map (names unverified).
- **Tooling.** A differential harness that checks the NRQL we emit against the PromQL it came from on seeded
  series; capability and data-round scripts for a New Relic sandbox (`scripts/live-test/capability-check/`);
  a contributor guide and issue templates for real exports, Prometheus reads, live checks and NRQL review.
- **Research notes** in `docs/research/`: source inspections for Grafana, Datadog, Dynatrace, Azure, Google Cloud
  and AWS with commit-pinned corpora, the New Relic capability audit and live checks, ingestion-path rules, PromQL
  prior art, message templates, and an article survey.

### Changed
- The repository is public under Apache-2.0. Only what is needed to run, test and build it is tracked; working
  notes and the improvement loop stay out of the repository.
- Dashboard conversion refuses `rate()`, `irate()` and `increase()` on metrics that are not counters, because New
  Relic stores them differently. When this landed, 83 fewer widgets converted on one public corpus (and 19 on
  Google Cloud dashboards); they stay manual by design until the metric type is known.

### Fixed
- The CloudWatch map key for NAT Gateway is `AWS/NATGateway`, the real namespace; the earlier key never matched.
- Documentation of ingestion paths now separates what the telemetry rig verified over Prometheus remote write from
  what is documented only (OTLP histograms).

### Measured (2026-11-16; counts only)
- Public sample content, converted share of alerts and dashboard widgets together: Google Cloud 75%, Datadog 63%
  (a fresh, reproducible sample), Grafana 37%, AWS 35%, Azure 25%, Dynatrace 2%.
- One real Grafana alert estate (5,039 rules): 8% at the start of this period, now 37% (1,888 rules). One real dashboard: 42%.
- Verified on data in a sandbox: `RLIKE` is a full-string match; a missing attribute is excluded by `RLIKE` and
  kept by `NOT RLIKE`; the nested sum of the latest value per series is exact; `average(x) * cardinality(x)` is
  14% low with mixed scrape intervals over 5 minutes and 52% low over 1 minute; `clamp_min` and `if()` evaluate
  correctly; `bucketPercentile` and `rate(sum(x_sum))` matched Prometheus over remote write.

### Known limits
- Exact matches: none. Nothing here has run against customer data.
- Prometheus histograms sent over OTLP are stored as one distribution metric, so `bucketPercentile` conditions
  match nothing on that path. Datadog, Azure, Dynatrace and most Google names have never been seen in stored data.
- New Relic cannot reproduce joins between series, subqueries or `COMPARE WITH` in alert conditions,
  `cardinality()` in conditions, or per-widget conditional visibility.

## 0.1.0

First prototype: Datadog monitors converted to New Relic alert conditions, then AWS CloudWatch, Google Cloud,
Grafana, Dynatrace and Azure Monitor sources, guarded create-only apply with a rollback manifest, reports and
Terraform output.
