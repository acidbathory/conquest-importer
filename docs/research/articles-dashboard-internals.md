# Articles and docs on dashboard, widget, query and alert internals

Run date 2026-11-16. Agent: articles-dashboard-internals. No web search tool. Public URLs only, 1 request per second, user agent `research-bot`, cache in /tmp/community/articles/. No Datadog domain requested.

## Coverage honesty

Worked: Medium tag feeds (14 tags: alerting, aws-cloudwatch, azure-monitor, cloudwatch, datadog, dynatrace, google-cloud-monitoring, grafana, kql, newrelic, observability, opentelemetry, prometheus, promql; each feed is recent items only, about 10 per tag, so it is a snapshot not an archive); dev.to API (14 tags, 30 each); prometheus.io, grafana.com and opentelemetry.io blog feeds; raw GitHub for prometheus/prometheus, grafana/grafana and newrelic/docs-website docs.

Did not work or not tried: the awsdocs/amazon-cloudwatch-user-guide and MicrosoftDocs/azure-docs paths I tried returned 404 (repos moved or trimmed), so CloudWatch alarm evaluation, Azure log-alert and Google Cloud Monitoring internals are NOT covered by primary text this round. `gh search issues` returned empty for two queries; `gh search code` hit "unexpected end of JSON input" once. The New Relic MCP server failed to connect (unused). Technique dev.to / Medium feeds are mostly beginner or marketing content: of about 450 items only a handful explain semantics. Vendor blog feeds (Grafana, OTel, Prometheus) are release news, with little on internals. I stopped at about 38 tool calls; the source count is below the 40-60 target because most feed items were not relevant (see "Feed items scanned and rejected").

## Annotated sources (primary docs first)

Reliability: high = vendor primary text; medium = practitioner with worked examples; low = unsupported or beginner.

| # | Title | URL | Author/org | Date | Topic | What it says (own words) | Rel. |
|---|---|---|---|---|---|---|---|
| 1 | Prometheus querying basics (extended range selectors, staleness) | https://github.com/prometheus/prometheus/blob/main/docs/querying/basics.md | Prometheus | main, 2026 | Prometheus | Range selectors are left-open; `anchored` and `smoothed` modifiers change how rate/increase treat boundaries; staleness marks and the 5 minute lookback. | high |
| 2 | Prometheus query functions | https://github.com/prometheus/prometheus/blob/main/docs/querying/functions.md | Prometheus | main | Prometheus | rate/increase/delta extrapolate to the window edges and fix counter resets; `info()` is documented as the join for info metrics. | high |
| 3 | Prometheus 3.0 migration guide | https://github.com/prometheus/prometheus/blob/main/docs/migration.md | Prometheus | main | Prometheus | Lookback and ranges became left-open, so evenly spaced samples now give exactly N samples per N-interval range; regex dot now matches newlines. | high |
| 4 | Prometheus feature flags | https://github.com/prometheus/prometheus/blob/main/docs/feature_flags.md | Prometheus | main | Prometheus | Extended range selectors are on by default; experimental `fill()` modifiers for binary operators; start-timestamp synthesis drops the first sample. | high |
| 5 | Prometheus operators | https://github.com/prometheus/prometheus/blob/main/docs/querying/operators.md | Prometheus | main | Prometheus | Vector matching rules, comparison filtering, `bool` modifier. | high |
| 6 | Introducing the experimental info() function | https://prometheus.io/blog/2025/12/16/introducing-info-function/ | Prometheus | 2025-12-16 | Prometheus | Info-metric joins get a first-class function instead of `* on() group_left()`. | high |
| 7 | Modernizing Prometheus: native storage for composite types | https://prometheus.io/blog/2026/02/14/modernizing-prometheus-composite-samples/ | Prometheus | 2026-02-14 | Prometheus | Histogram/summary parts move toward being stored as one composite sample. | high |
| 8 | Prometheus and OpenTelemetry interoperability survey | https://prometheus.io/blog/2026/09/21/otel-prometheus-interoperability-survey/ | Prometheus | 2026-09-21 | OTel to Prometheus | Survey of users on how OTLP metrics land in Prometheus (feed summary only, not fully read). | medium |
| 9 | Prometheus 3.0 announcement | https://prometheus.io/blog/2024/11/14/prometheus-3-0/ | Prometheus | 2024-11-14 | Prometheus | UTF-8 names, OTLP ingest, range selector change. | high |
| 10 | Subquery support | https://prometheus.io/blog/2019/01/28/subquery-support/ | Prometheus | 2019-01-28 | PromQL | Subquery step alignment and evaluation semantics. | high |
| 11 | Introducing the @ modifier | https://prometheus.io/blog/2021/02/18/introducing-the-@-modifier/ | Prometheus | 2021-02-18 | PromQL | Pins evaluation time of a selector. | high |
| 12 | Grafana alerting: No Data and Error states | https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/nodata-and-error-states.md | Grafana Labs | main | Grafana alerts | NoData means the query returned zero series; both states honor the pending period; extra DatasourceNoData/DatasourceError alerts with their own labels. | high |
| 13 | Grafana alerting: stale alert instances | https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/stale-alert-instances.md | Grafana Labs | main | Grafana alerts | A series that disappears while others remain resolves the alert after N evaluations (default 2); this is not NoData. | high |
| 14 | Grafana alerting: queries and conditions | https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rules/queries-conditions.md | Grafana Labs | main | Grafana alerts | Reduce/Math/Resample/SQL/Threshold nodes; math joins series by identical labels; threshold returns 1 or 0; recovery threshold; classic condition yields one instance only. | high |
| 15 | Grafana alerting: evaluation within a group | https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/evaluation-within-a-group.md | Grafana Labs | main | Grafana alerts | Grafana-managed rules in a group run concurrently; imported data-source rules run in sequence. | high |
| 16 | What is new with Grafana variables | https://grafana.com/blog/whats-new-with-grafana-variables-less-clutter-easier-reuse-across-dashboards-and-more/ | Grafana Labs | 2026-11-05 | Grafana dashboards | Variables become reusable across dashboards (title and feed only, not read). | medium |
| 17 | Grafana 13.3 release | https://grafana.com/blog/grafana-13-3-release-all-the-latest-features/ | Grafana Labs | 2026-10-29 | Grafana | Cross-dashboard variable management, UI refresh (feed summary). | medium |
| 18 | NRQL alert conditions: create | https://github.com/newrelic/docs-website/blob/develop/src/content/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions.mdx | New Relic | develop | NR alerts | Lists NRQL pieces unusable in alerts, null vs zero behaviour, nested aggregation rules, signal loss limits, 20000 facets, aggregation windows 30 s to 120 min. | high |
| 19 | Use sliding windows for smoother charts | https://github.com/newrelic/docs-website/blob/develop/src/content/docs/nrql/using-nrql/create-smoother-charts-sliding-windows.mdx | New Relic | 2024-03 | NRQL | SLIDE BY semantics, and extra compute charges when a sliding window query becomes an alert. | high |
| 20 | Translate PromQL queries to NRQL | https://github.com/newrelic/docs-website/blob/develop/src/content/docs/infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql.mdx | New Relic | undated | NR + Prometheus | Counters become delta counts, histograms become counts, summaries gauges plus counts. | high |
| 21 | New Relic alert conditions overview | https://github.com/newrelic/docs-website/blob/develop/src/content/docs/alerts/create-alert/create-alert-condition/alert-conditions.mdx | New Relic | develop | NR alerts | Condition types and thresholds (cached, skimmed). | high |
| 22 | I wrote a Datadog to Grafana monitor translator | https://dev.to/kaushik94/i-wrote-a-datadog-to-grafana-monitor-translator-250f | kaushik94 | 2026-09-29 | Datadog to Grafana | Account of a translator whose output fired forever: PromQL comparisons filter series, `for` is not `no_data_timeframe`, recovery thresholds and `new_group_delay` lose meaning. | medium |
| 23 | Alerting isn't just adding rules: the PromQL traps I hit | https://jason-chen-0604.medium.com/alerting-isnt-just-adding-rules-the-promql-traps-i-hit-2c152f800a4f | jason-chen | 2026-08-15 | PromQL alerting | Practitioner list of PromQL alert pitfalls (title and feed summary only; not read in full). | low-medium |
| 24 | We put the error rate in our alert labels and it never stayed the same alert long enough to fire | https://dev.to/sergey_shinder_ab2d943365/we-put-the-error-rate-in-our-alerts-labels-and-it-never-stayed-the-same-alert-long-enough-to | sergey_shinder | 2026-10-28 | Alert identity | A value in a label changes the series identity every evaluation, so the pending period never completes. | medium |
| 25 | Azure Monitor alert secrets: stateful vs stateless | https://medium.com/@mailtosyedmohamed/azure-monitor-alert-secrets-stateful-vs-stateless-which-one-should-you-use-e568f54dce3c | mailtosyedmohamed | 2026-10-29 | Azure alerts | Stateful alerts fire once until resolved, stateless alerts fire each evaluation (feed summary only). | low-medium |
| 26 | Prometheus Subqueries, Histograms, Recording Rules and the HTTP API | https://medium.com/@Kwindisch/prometheus-subqueries-histograms-recording-rules-and-the-http-api-b64bc61e4e29 | Kwindisch | 2026-09-01 | PromQL | Beginner walk-through. | low |
| 27 | Returning to PromQL after a short break | https://medium.com/@Kwindisch/returning-to-promql-after-a-short-break-5e35232a0e45 | Kwindisch | 2026-08-31 | PromQL | Beginner refresher. | low |
| 28 | PromQL: production queries every DevOps engineer should know | https://medium.com/@TheVega.ai/mastering-promql-production-queries-every-devops-engineer-should-know-fa529344d1ca | TheVega.ai | 2026-07-19 | PromQL | Common query patterns. | low |
| 29 | Building Kubernetes Grafana dashboards from scratch: PromQL, panels, variables | https://slycreator.medium.com/building-kubernetes-grafana-dashboards-from-scratch-promql-panels-variables-and-nestjs-ecf64b1cadaa | slycreator | 2026-09-08 | Grafana dashboards | Tutorial on variables and panels. | low |
| 30 | Prometheus Explained: metrics, scraping, PromQL, alerting | https://medium.com/@sayedebad.777/prometheus-explained-metrics-scraping-promql-and-alerting-617c47baed17 | sayedebad | 2026-11-15 | Prometheus | Overview. | low |
| 31 | Temporal Cloud metrics with OTel and Azure Managed Prometheus | https://medium.com/@siva_bankapalli/temporal-cloud-metrics-with-opentelemetry-and-azure-managed-prometheus-without-application-3c5c5186 | siva_bankapalli | 2026-08-06 | Azure Managed Prometheus | OTel to Azure Managed Prometheus path. | low-medium |
| 32 | Manage team alert routing in Grafana | https://medium.com/@huzaifa.io/manage-team-alert-routing-in-grafana-19174e8fac03 | huzaifa.io | 2026-10-10 | Grafana notifications | Routing via notification policies. | low |
| 33 | A real-world Graphite alerting case study: reducing noise | https://medium.com/@MetricFire/a-real-world-graphite-alerting-case-study-reducing-noise-at-metricfire-3d7476b17c53 | MetricFire | 2026-08-20 | Alerting | Noise reduction practice. | low |
| 34 | Why average response time is a terrible performance metric | https://medium.com/@spkarthick91/why-average-response-time-is-a-terrible-performance-metric-5d63e36d6f7a | spkarthick91 | 2026-08-23 | Percentiles | Averages hide tails. | low |
| 35 | Anatomy of an OpenTelemetry span: fields and DQL | https://dev.to/spanobserver/anatomy-of-an-opentelemetry-span-fields-and-dql-45od | spanobserver | 2026-11-13 | Dynatrace DQL | Span fields in DQL (title only). | low-medium |
| 36 | Monitoring Dynatrace Managed cluster health | https://dev.to/spanobserver/monitoring-dynatrace-managed-cluster-health-indicators-and-checks-kpd | spanobserver | 2026-11-13 | Dynatrace | Cluster health checks (not relevant to conversion). | low |
| 37 | AWS CloudWatch fundamentals for DevOps engineers | https://medium.com/@shanbojja/aws-cloudwatch-fundamentals-for-devops-engineers-monitor-metrics-logs-and-alarms-818a4d75b0bf | shanbojja | 2026-10-26 | CloudWatch | Beginner overview. | low |
| 38 | Monitoring Amazon S3 with CloudWatch: dashboard and alarm | https://medium.com/@shaileshyadav1416/monitoring-amazon-s3-with-cloudwatch-build-a-dashboard-and-an-alarm-in-under-an-hour-2c1143180a04 | shaileshyadav | 2026-11-01 | CloudWatch | Tutorial. | low |
| 39 | Create Google Cloud Monitoring alerts manually and via Terraform | https://medium.com/google-cloud/create-google-cloud-monitoring-alerts-manually-and-via-terraform-1715668f0d79 | Medium google-cloud | 2023-09-14 | GCP alerts | Tutorial with Terraform. | low-medium |
| 40 | Monitor Google Cloud with self-hosted Grafana | https://medium.com/google-cloud/monitor-google-cloud-with-self-hosted-grafana-5fdb8a6f27b1 | Medium google-cloud | 2024-05-14 | GCP and Grafana | Data source setup. | low |
| 41 | Opentelemetry blog: OTTL reaches v1 | https://opentelemetry.io/blog/2026/ottl-v1/ | OpenTelemetry | 2026-10-27 | OTel | Transformation language stability. | high |
| 42 | OTel-native by design | https://opentelemetry.io/blog/2026/otel-native-by-design/ | OpenTelemetry | 2026-10-09 | OTel | Vendor export design (title only). | medium |
| 43 | Versioned metrics: first RC metrics in hostmetrics | https://opentelemetry.io/blog/2026/hostmetrics-process-scraper-rc/ | OpenTelemetry | 2026-10-13 | OTel metric names | Metric name and schema versioning affects name mapping. | high |
| 44 | Bridging Alloy and OpenTelemetry: OTel engine in public preview | https://grafana.com/blog/bridging-alloy-and-opentelemetry-the-otel-engine-is-now-in-public-preview/ | Grafana Labs | 2026-11-03 | Ingestion | Alloy gets an OTel collector engine. | medium |
| 45 | Prometheus: 100% GPU utilization, 39% of rated FP16 | https://itnext.io/100-gpu-utilization-39-of-rated-fp16-ce97ba1b88f0 | itnext | 2026-11-06 | Prometheus metric semantics | Reads a utilization gauge that hides actual throughput (title only). | low |

Feed items scanned and rejected: about 400 other Medium and dev.to items (Sentinel hunting, beginner exporter tutorials, cloud security). They were not about conversion semantics.

## Findings (changes or challenges to our assumptions)

Notes touched: `docs/research/newrelic-grafana-data-and-query-analogues.md` (F08, F10, F28, section on staleness), `docs/research/promql-engine-rules.md`, `docs/research/nr-promql-baseline.md`, `docs/research/grafana-native-coverage.md`, `.claude/worktrees/integrate-1111/docs/what-migrates.md` (Grafana alert section, line about no-data state), `docs/research/datadog-verified.md`.

### F1. Prometheus now has a non-extrapolating `increase`: `anchored`
Source: https://github.com/prometheus/prometheus/blob/main/docs/querying/basics.md (extended range selectors) and feature_flags.md (flag is a no-op, on by default).
Fact (primary): `increase(x[5m] anchored)` uses the last sample before the window start and the last sample in the window, with no extrapolation or interpolation, and returns integers for float counters. Our notes (F08, F10, "Prometheus extrapolates, so values differ at window edges") treat the difference as irreducible approximation. NR's `sum` of delta counts is exactly this anchored quantity. So the comparison against live Prometheus, and the "approximate" label, can be made exact by evaluating the source with `anchored` when we validate; the sentence "approximate because of extrapolation" is true only against the default selector. Also `smoothed` linearly interpolates and, per the doc, under-estimates in alert rules unless the rule group has `query_offset`.
Confidence: high on the doc facts; medium on how much it changes our reported tier (it changes the validation method more than the output).
Touches: newrelic-grafana-data-and-query-analogues.md F08, F10, "Edge rule" paragraph; live-grafana-counter-roundtrip.md validation.

### F2. Per-series disappearance in Grafana resolves; it is not NoData, so "NoData=Alerting becomes loss of signal" is wrong for faceted rules
Source: https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/stale-alert-instances.md and nodata-and-error-states.md.
Fact (primary): NoData occurs only when the query returns no series at all. If one series of several vanishes for 2 evaluations (default; `missing_series_evals_to_resolve`), that instance goes Normal (MissingSeries) as resolved. New Relic loss of signal is per signal (per facet) and, per the NR doc, opens a violation when the expiry timer ends. Our converter maps `noDataState: Alerting` to loss of signal on a possibly faceted condition. For a multi-series rule, NR would open violations for every pod that terminates, where Grafana resolves them. The `missing_series_evals_to_resolve` field is documented in grafana-native-coverage.md but not used in the mapping caveats.
Confidence: high on both vendors' documented behaviour; medium on the NR side because the doc says LOS needs a prior signal and the per-facet behaviour is inferred from "signal" wording (no live test).
Touches: what-migrates.md Grafana section (no-data state sentence), grafana-native-coverage.md section on stale instances.

### F3. A PromQL comparison inside the alert query turns "below threshold" into NoData
Source: https://dev.to/kaushik94/i-wrote-a-datadog-to-grafana-monitor-translator-250f (practitioner, with a reproduced run), consistent with Grafana doc F2 (NoData = zero series) and Prometheus operators.md (comparisons filter).
Fact vs opinion: the PromQL filtering behaviour is primary (Prometheus docs). The reported symptom with `noDataState: Alerting` (alert never clears) follows from Grafana's NoData definition but the run is the author's own.
Impact: our Grafana rule path expects the query/reduce/threshold shape. A Grafana-managed rule whose PromQL embeds the comparison (`... > 90`, common for rules imported from Prometheus rule files, and the Prometheus ruler path "Grafana PromQL translator") has the same NoData ambiguity. With `NoData=Alerting` mapped to loss of signal, an NR condition built on the already-filtered PromQL would open a loss-of-signal violation whenever the metric is healthy but below the threshold. Check that the converter moves the comparison into the NR threshold and never leaves it in the query when no-data handling is set (the elastic note line 320 says "alerts move the comparison into the threshold"; verify it also holds for Grafana imports and the Prometheus rule group path).
Confidence: medium-high (behaviour is documented; our code path not re-read).
Touches: promql-engine-rules.md row for filters on aggregates; what-migrates.md (Prometheus rule group row); grafana-native-coverage.md.

### F4. Datadog `no_data_timeframe` is not `for`, and `critical_recovery` is real hysteresis; NR has neither
Source: same dev.to article (medium reliability, practitioner), table of five options.
Fact vs opinion: that Datadog has these options is primary elsewhere (our datadog-verified.md already lists them). The opinion part is the mapping lesson: mapping `no_data_timeframe` to the condition's threshold duration (or Grafana `for`) applies the window twice. Our datadog-verified.md line 88 maps `new_group_delay` to `evaluation_delay` ("nearest"); the article suggests new-group delay has no real equivalent. Our notes do not name hysteresis (`critical_recovery`) as a loss; line 81 lists it only as a field. The what-migrates Grafana text says a recovery threshold is "ignored" which is honest; check the Datadog path says the same and does not approximate hysteresis with a lower threshold duration.
Confidence: medium. Not verified against NR (NR has no recovery threshold in the condition docs I read; I did not search for one).
Touches: datadog-verified.md lines 81 to 88; datadog alert converter caveats.

### F5. NR alerts do allow nested aggregation when the inner query is faceted and the window is shared; our notes say "an alert cannot hold the nested form"
Source: https://github.com/newrelic/docs-website/blob/develop/src/content/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions.mdx, section "Nested aggregation NRQL alerts".
Fact (primary): nested queries are allowed if the innermost query is faceted, all levels use the same aggregation window, and there is no loss of signal on them. The same page says plain subqueries and subquery JOINs are not compatible. promql-engine-rules.md (line 50) says the nested form is impossible in an alert; dynatrace-alerting-objects-deep.md (lines 514-515) already records the correct rule. The practical consequence: `sum(gauge)` as an exact sum over latest-per-series (`SELECT sum(x) FROM (SELECT latest(g) FACET instance)`) and `avg(sum without(mode)(rate(c)))` may be expressible in an alert with an inner `FACET`, instead of `average * cardinality`. Not verified live; the doc also contradicts itself loosely (subqueries "not compatible" while FROM-subselect nesting is allowed), so treat as a candidate to test.
Confidence: medium (doc text clear, interaction untested).
Touches: promql-engine-rules.md line 50, nr-promql-baseline.md items on `sum(gauge)`, newrelic-grafana-data-and-query-analogues.md F28 (over-count by points per series).

### F6. Sliding-window alerts have a price (extra compute charges), which our `SLIDE BY` and `rate` forms ignore
Source: https://github.com/newrelic/docs-website/blob/develop/src/content/docs/nrql/using-nrql/create-smoother-charts-sliding-windows.mdx, section "Pricing".
Fact (primary): converting a sliding-window query to an alert may incur extra CCU charges on Advanced and Core Compute plans; one event is queried window/slide times. Our converters use `SLIDE BY 15000` for `increase` and `1 minute` slides for over-time functions. A 5 minute window with a 15 second slide queries each event 20 times. This is a cost note for the report, and AGENTS.md says infrastructure with cost must be highlighted.
Confidence: high on the doc; low on the exact effect on our plan (depends on account pricing).
Touches: nr-promql-baseline.md line 70, newrelic-grafana-data-and-query-analogues.md F10, report text.

### F7. Prometheus 3.0 made range selectors left-open: window sample counts changed and "two samples needed" gets sharper
Source: https://github.com/prometheus/prometheus/blob/main/docs/migration.md and basics.md (range is left-open, right-closed).
Fact (primary): a sample exactly at the left edge is excluded now. A 1 minute scrape with `rate(x[1m])` can therefore see one sample and return nothing, whereas under 2.x it often saw two. Our window mapping (`[w]` to bucket `w`) and the "rate window at least 4 scrapes" style rules should state which Prometheus version the source ran. Dashboards exported from Grafana with `$__rate_interval` are safe (Grafana sets it to at least 4 scrape intervals), hard-coded `[1m]` over 1 minute scrapes are the risk.
Confidence: high on the change; medium on our exposure.
Touches: promql-engine-rules.md window rules; live-grafana-counter-roundtrip.md.

### F8. `fill()` binary-operator modifiers and `info()` are the modern spelling of `or vector(0)` and `* on() group_left()`
Source: https://github.com/prometheus/prometheus/blob/main/docs/feature_flags.md (fill modifiers, experimental), functions.md (`info()`), blog https://prometheus.io/blog/2025/12/16/introducing-info-function/.
Fact (primary): `fill(0)` supplies a default for missing matches on a side; `info(v)` adds info-metric labels (for example from `target_info`). Our notes list `group_left` and `or vector(0)` as manual or approximate; newer sources will contain `info(...)` and `fill`, and the parser will reject them. It is an input-coverage gap, not a semantics change. The `info()` doc itself warns that a non-stale old `target_info` series causes a 5 minute conflict.
Confidence: high that they exist; low on how common they will be in our estates (experimental).
Touches: promql-engine-rules.md rows for `or` and `group_left`; census; a parser reject-reason list.

### F9. Grafana Math joins series on identical label sets, and a classic condition always yields one instance
Source: https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rules/queries-conditions.md.
Fact (primary): classic condition "always produces one alert instance only, no matter how many series meet the condition" (also states they exist for compatibility). Our converter turns a classic condition on a faceted query into one NR condition; if that query has FACET, NR would create one violation per facet, which is a different notification count than the single Grafana instance. Check the converter's note says this. Math between two queries is a label-equal join, so a conversion that drops a label from one side changes the result silently.
Confidence: medium-high.
Touches: what-migrates.md lines 502 to 535; grafana-native-coverage.md section 4.

### F10. Grafana-managed rules in one group run concurrently; imported rules run in sequence
Source: https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/evaluation-within-a-group.md.
Fact (primary). Rules that read a recording rule's result in the same group depend on order. NR has no recording-rule ordering. Low impact for conversion, but it explains stale reads when a converted alert depends on a recording rule that we do not convert (see the "recording rule" notes).
Confidence: medium; low impact.
Touches: grafana-native-coverage.md recording rules text.

## Three most actionable

1. F2 (stale vs no-data): add a note, and for faceted rules do not map `NoData=Alerting` to a per-facet loss of signal without saying so. Check `missing_series_evals_to_resolve`.
2. F3 (comparison filters in alert queries): verify with a fixture that the threshold is never left inside the PromQL when no-data handling is set.
3. F5 (nested aggregation in alerts): test a faceted inner query in a sandbox condition; if it works, several `sum(gauge)` and per-cpu mean cases stop being "dashboard only".

Notes on honesty: F4's NR side, F2's per-facet claim and F5's behaviour are untested here (no live access allowed). F1, F7, F8 are documentation-level facts from the Prometheus main branch, which may be ahead of released versions.
