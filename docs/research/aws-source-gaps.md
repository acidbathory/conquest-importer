# AWS CloudWatch source inspection (rule 9), 2026-11-16

Public sample only; not customer data. Manifest with SHAs: /tmp/research/aws-corpus-manifest.md.

## Bad news first
- Convert aborts on one bad alarm. A PromQL alarm (EvaluationCriteria.PromQLCriteria, no Namespace, no EvaluationPeriods) makes `importer convert --source cloudwatch` stop with a validation error for the whole run (tested with one synthetic alarm). Same crash class as before: dimension Name or Namespace left as a non-string also aborted the run, but that was my CloudFormation resolver, not AWS output.
- The corpus is skewed: one repo (mzazon/cloud-projects) is 60% of alarms and 83% of dashboards. Without it widgets convert 31% (75 of 239), alarms 78% (305 of 390). With it: widgets 40% (411 of 1019), alarms 71% (695 of 980).
- Dashboard conversions depend on unverified names: 22 mapped namespaces, 8 of them by inference.

## Baseline (importer scoreboard, data widgets only)
All: alarms 695/980 (70.9%), widgets 411/1019 (40.3%). Without mzazon: alarms 305/390 (78.2%), widgets 75/239 (31.4%).

## Ranked gaps (corpus count; sampled ceiling = items that would convert if that gap alone closed)
| Construct | Count (alarms / widgets) | Sampled ceiling | NR form |
|---|---|---|---|
| Namespace not in map, AWS/* (EFS, CloudFront, Kafka, DMS, Events, Batch, Backup, AppSync, Glue, Route53 ...) | 208 alarms / 227 widgets | alarms +21.2 pts all, +11.0 pts without mzazon (43/390); widgets +22 pts all but +1.3 pts without mzazon (3/239) | Yes by documented rule: AWS/X becomes aws.x (lower case, "/" to "."); per-service pages say Metric Streams carries all namespaces. Dimension names and live data unverified for most |
| Custom/non-AWS namespaces (AWSBatchMetrics, CWAgent, Business/Metrics, AmazonMWAA, collectd) | 47 alarms / 136 widgets | not sized; name rule for non-AWS namespaces is undocumented | Unknown (our map only handles Custom/<alnum>) |
| Logs Insights widgets | 136 widgets | 103 of 136 are plain "fields/filter/sort/limit" tables (10.1 pts all, 1.7 pts without mzazon: 4 of 239); 29 use stats/parse, 14 of the 18 non-mzazon ones | Yes (NRQL on Log) if logs are forwarded; attribute for the log group unverified |
| Metric math in widgets | 85 widgets (all outside mzazon) | 31 of 239 (13 pts) have only mapped namespaces: SEARCH 9, SEARCH+arith 11, arith 7, METRICS() 4; but 15 from one GameLift repo, 12 from the custom-widget samples, 12 from Batch. 5 more with the AWS/ rule. Whole corpus: 31/1019 = 3.0 pts | Yes for ratio and SUM/AVG(METRICS()); SEARCH needs facet on the dimension (partly) |
| Metrics Insights (SELECT ... FROM SCHEMA/ CWAgent) | 10 widgets, all one repo | ~1 pt | Yes in principle (FROM Metric FACET); CWAgent is a custom namespace |
| Custom (Lambda) widgets | 32 widgets | 0 | No |
| chart / alarm status widgets | 4 + 1 | <0.5 pt | Unknown |
| PromQL alarms, log alarms (PutLogAlarm), anomaly (1), composite (1), threshold-metric (1), extended statistics p95/p99 (9) | 0 to 9 each | under 1 pt each | PromQL: yes through our PromQL engine (unverified); log alarms: NRQL on Log |

Ceiling cautions: the namespace gap also carries unverified names. The 22 unmapped "stack" alarms are my resolver stand-in for ${AWS::StackName} and are excluded.

## What New Relic cannot reproduce
- Custom widgets (arbitrary Lambda HTML). No New Relic form.
- Percentile and trimmed-mean statistics from Metric Streams: New Relic stores AWS metrics as dimensional type "summary" (count, sum, min, max). Percentiles exist only if the stream is configured with additional statistics (AWS PutMetricStream StatisticsConfigurations); TM/WM/IQM statistics have no stored equivalent. Only 9 corpus alarms use p95/p99.
- Anomaly detection band with a different algorithm: the converter already maps it to a baseline condition (approximate).
- Duplicate counting: one metric appears under several dimension combinations; sums need IS NULL / IS NOT NULL filters (documented by New Relic).

## Source model (botocore cloudwatch, commit e250abb)
Alarm kinds: single metric, metric math, Metrics Insights, anomaly detection (Threshold = ThresholdMetricId, operators LessThanLowerOrGreaterThanUpperThreshold, ...), composite (AlarmRule grammar), and since 2026 PromQL (EvaluationCriteria.PromQLCriteria with Query, PendingPeriod, RecoveryPeriod) and log alarms (PutLogAlarm, scheduled Logs query). Statistic enum: SampleCount, Average, Sum, Minimum, Maximum; ExtendedStatistic for pNN, TM, WM, IQM, PR. TreatMissingData: breaching, notBreaching, ignore, missing. Also DatapointsToAlarm, EvaluateLowSampleCountPercentile, WarmUpConfiguration, EvaluationInterval, ActionConfiguration. Our converter: handles metric, math (simple), anomaly, composite, p-stats as approximate; PromQL and log alarms are not known (and crash the run). Dashboard body (AWS docs page, fetched): widget types metric, text, log, alarm, explorer, chart (documented), custom; metric views timeSeries, singleValue, gauge, bar, pie, table; properties metrics, stat, period, region, annotations, yAxis, sparkline, stacked, liveData, legend. Corpus uses only metric (846), log (136), custom (32), text (18), chart (4), alarm (1); 828 time series, 18 single value; no gauge, bar, pie or explorer widgets seen.

## What is going on (botocore history since 2026-05-01, 11 model updates)
New operations: PutLogAlarm (2026-06-29), alarm mute rules, OTel enrichment and resource metrics configuration (2026-09-24), metric data query definitions, PromQL alarms (2026-05). Metric Streams support OpenTelemetry 0.7/1.0 output and statistics configurations. Meaning: CloudWatch is adding PromQL and OTel on its side. New alarm kinds will arrive in describe-alarms; the converter must skip them with a reason rather than abort. Not inspected in detail: aws-observability repos beyond observability-best-practices (many OTel repos were pushed in the last week, no dashboards or alarms checked).

## Recommended levers (no building)
1. Apply the documented naming rule to every AWS/* namespace (one change plus a case fix: AWS/NATGateway is the real namespace; the map key is AWS/NatGateway). Ceiling: alarms +11 pts without mzazon, +21 pts with; widgets +1.3 to +22 pts depending on corpus. Verify names against a live stream first.
2. Make convert tolerate unknown alarm kinds (PromQL, log alarms): skip with a reason. Moves no coverage number, but removes a whole-run abort (hygiene; owner must ask for it by name).
3. Widget math for mapped namespaces (ratio, SUM/AVG(METRICS()), plain SEARCH): 3.0 pts on the full corpus, 13 pts on the non-mzazon set but 3 repos hold most of it. Ceiling is above 2 pts only on the skewed subset: treat as borderline.
(Logs Insights tables: 1.7 pts without mzazon, 10 pts with it: below 2 pts on the unbiased set, so not recommended.)

## Unverified
Dimension naming for the unmapped services; New Relic naming of non-AWS namespaces; log-group attribute on forwarded logs; mzazon licence; whether the unresolved 804 alarms differ in shape from the resolved ones.
