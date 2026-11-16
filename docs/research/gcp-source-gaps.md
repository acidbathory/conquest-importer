# GCP source inspection (rule 9), 2026-11-16

BAD NEWS FIRST
- No fresh alert corpus exists. All 276 public alert policies in monitoring-dashboard-samples are already in gcp-estate (same SHA 2ef05da). Other repos tried (professional-services, terraform modules) held no policy JSON. Alert numbers below are a re-measure of fixtures, not independent. Dashboards are fresh (142 not in fixtures).
- "Converted" overstates. Dashboards: 1714 data widgets, 617 manual, 1097 converted, of which only 450 of 1699 metric widgets are native (26%); 334 more are PromQL that needs Prometheus/OTel ingest. Alerts: 156 of 276 converted (57%), only 56 of those native (20%); 64 need Ops Agent/OTel, 34 Prometheus remote write, 2 custom ingest.
- 84 of 276 alerts (30%) are PromQL conditions; 79 stay manual. 729 of 1699 metric widgets (43%) are PromQL.

RANKED GAPS (dashboards, 617 manual of 1714 data widgets)
| Construct | Count | Sampled ceiling | NR form |
| PromQL "shape the planner reads" | 255 | see rows below | partly |
|  - avg_over_time(x[interval]) around plain selector | ~40 | +40 widgets = +2.3 pp (measured by rewriting inputs, converter run) | yes (average over TIMESERIES bucket) |
|  - "A or B" metric fallback (anthos) | 27 | <=27, +1.6 pp, second metric dropped | unknown |
|  - vector matching on()/group_left (rabbitmq identity 30, flink "and on" 19) | 64 | NR has no join | no |
|  - label_replace/label_join | 52 | NR cannot relabel | unknown |
| rate() of a metric with no counter suffix | 52 | +50 widgets = +2.9 pp (measured: input rewritten with a _total suffix, so an upper bound; needs metric kind) | yes if counter |
| sum()/count() across a gauge | 44 | not measured | yes with facet by identity |
| histogram_quantile on Prometheus buckets | 69 | not measured | OTel distribution: percentile()/histogram() documented; Prometheus buckets unknown |
| REDUCE_PERCENTILE after ALIGN_DELTA (distribution) | 40 | not measured | integration polling of distributions unverified |
| ratio with unlike aligner/period | 19 | not measured | not checked |
| SQL widgets | 15 | 0 | no (no SQL widget form in proto either; not in TimeSeriesQuery) |
Variables: replacing ${__interval} and ${Cluster}-style label filters gave 0 change (measured), so template variables are not the binding gap on this corpus.
Alerts (276): PromQL range function 12, ratio 11, _over_time 9, binary op 8, vector matching 5, template placeholders ~21, topk 2; MQL 5 (out of scope); log-based metric 6; system labels 4; boolean aligners 3+.

NEW RELIC CANNOT REPRODUCE: vector matching joins, label rewriting, SQL widgets and SQL/MQL conditions, count of time series in a condition. Distribution type exists in NR (docs: metric-data-type.mdx) for percentile/histogram, via OTel; Prometheus bucket series not verified.

WHAT IS GOING ON
- googleapis: last alert.proto change 2024-12-13 (SqlCondition); dashboard/v1 only a doc change 2025-09/2026-09 (IncidentList wording). v3 has no new condition type in the last 6 months. Proto condition oneof: threshold, absent, matched log, MQL, PromQL, SQL. Widget oneof additionally has alertChart, collapsibleGroup, logsPanel, incidentList, errorReportingPanel, sectionHeader, singleViewGroup (we know most; singleViewGroup and errorReportingPanel not checked in the converter).
- Samples since May 2026: OTel GenAI and Agent Runtime alerts, Crashlytics alerts, SAP HANA memory alert, llm-d, Claude Code, Vertex dashboards: all Ops Agent/OTel/Prometheus metrics, so more non-native data paths. PromQL is the sample authors' default form; MQL still present but PromQL replaces it.
- Metric kinds: GAUGE, DELTA, CUMULATIVE; value types BOOL, INT64, DOUBLE, STRING, DISTRIBUTION, MONEY. Aligners 21, reducers 14 (ALIGN_PERCENTILE_*, ALIGN_FRACTION_TRUE, ALIGN_PERCENT_CHANGE all exist).

RECOMMENDED LEVERS (no building)
1. Metric-kind-aware rate: use metric kind to accept rate() of Google cumulative metrics lacking _total. Ceiling +2.9 pp of data widgets (upper bound; in-flight builder build-rate-shapes may overlap).
2. avg_over_time of a plain selector as plain average: ceiling +2.3 pp, widgets only; most of these metrics still need Prometheus/OTel ingest.
3. Report the native vs ingest split in the headline so "converted" is not read as working: 26% native widgets, 20% native alerts.
DEFINITION OF PROGRESS: met (2 levers >= 2 pp on the dashboard corpus, not blocked by an NR limit), but both are upper bounds, and they overlap with each other only in the 86-widget rewrite union (combined run: +90 widgets, 617 -> 527 manual = 5.2 pp).

UNVERIFIED: alert ceilings not sampled beyond reason counts (no fresh corpus); gauge-sum, histogram, distribution ceilings not measured; converter may lag main; NR GCP integration distribution handling and Prometheus histogram ingest not read; terraform provider not read; notification channels and uptime protos inventoried by name only.
