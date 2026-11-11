# Fixtures for the extra Google reads (log-based metrics, SLOs, uptime checks)

All files are SYNTHETIC. Nothing came from a customer or a live Google Cloud project. Each
`api-*.json` is shaped like the response body of one read, so the tests serve them through a fake
getter and the extractor summarises them exactly as it would a real response. Names match the
estate corpus (`tests/fixtures/gcp-estate`) so the estate measurement can use them
(`scripts/analysis/gcp_estate_measure.py --extras tests/fixtures/gcp-extract-plus`).

| File | Read | Shape taken from |
|---|---|---|
| `api-log-metrics.json` | `GET https://logging.googleapis.com/v2/projects/{p}/metrics` (key `metrics`) | Cloud Logging API `LogMetric`: `name`, `description`, `filter`, `disabled`, `metricDescriptor` (`metricKind`, `valueType`, `unit`, `labels`), `valueExtractor`, `labelExtractors`, `bucketOptions` (`exponentialBuckets`) |
| `api-services.json` | `GET .../v3/projects/{p}/services` (key `services`) | Cloud Monitoring `Service` (`name`, `displayName`, `custom`, `cloudRun`) |
| `api-slos-checkout.json`, `api-slos-gateway.json` | `GET .../v3/projects/{p}/services/{s}/serviceLevelObjectives` (key `serviceLevelObjectives`) | `ServiceLevelObjective`: `goal`, `rollingPeriod` or `calendarPeriod`, `serviceLevelIndicator` with `requestBased` (`goodTotalRatio`, `distributionCut`), `windowsBased` (`goodBadMetricFilter`, `goodTotalRatioThreshold`, `metricMeanInRange`, `metricSumInRange`, `windowPeriod`) and `basicSli` |
| `api-uptime-check-configs.json` | `GET .../v3/projects/{p}/uptimeCheckConfigs` (key `uptimeCheckConfigs`) | `UptimeCheckConfig`: `httpCheck` (`requestMethod`, `useSsl`, `validateSsl`, `path`, `port`, `headers`, `authInfo`, `body`), `tcpCheck`, `monitoredResource`, `contentMatchers`, `selectedRegions`, `internalCheckers`, `checkerType`, `period`, `timeout` |

The shapes follow the public API reference pages and protos for these resources (Cloud Logging
`logging_metrics.proto`, Cloud Monitoring `service.proto` and `uptime.proto`). They were written
from that reference, not downloaded from a project, and have not been compared field by field
with a live response: UNVERIFIED against live data.

## What each entry is for

- Log metrics: seven counters named by the estate's policies (`HBase-Server-Stopping`,
  `apache`, `nginx`, `jetty`, `couchdb`, `tomcat`, `checkout_errors`), a counter with a label
  extractor, a regular-expression label extractor (stays manual), a distribution with a value
  extractor, a counter with a value extractor, a disabled metric and one with an OR across
  fields.
- SLOs (service `checkout`): request-based good/total (99.9%, 28 days), request-based bad/total,
  distribution cut with a calendar period, and every windows-based kind plus a basic indicator
  (all stay manual).
- Uptime checks: a plain GET, a content matcher, basic auth with a planted password and a header
  holding a planted token, a POST with a planted body, TCP, an internal check, a one region
  check and a header check. The planted secrets must never appear in an export or a plan.
