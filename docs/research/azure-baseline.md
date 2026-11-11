# Azure conversion baseline on the official examples

Measured offline on 2026-11-11 with `tests/fixtures/azure/`. No Azure tenant and no New Relic
account was called. This is the first measure of the `azure` source; it is a floor for the
documentation examples, not a prediction for a real estate (a live test estate will add real
captured responses under `tests/fixtures/azure-live/`, which the loader and `improve-measure`
already read).

## Method

- Input: the official example files of the Azure REST API specs (MIT, pinned in
  `tests/fixtures/azure/SOURCES.md`), five alert folders: metric alerts, scheduled query rules,
  activity log alerts, Prometheus rule groups, smart detector rules.
- A rule is read from the **response body** of an example, never from the request echo.
- Not counted: examples with no resource body (delete, 204, status), exact copies of a resource
  that several example files repeat (read once), quickstart templates, dashboards, workbooks,
  action groups, alert processing rules (notification routes, not conditions) and the 18 hostile
  synthetic files (they test the reader and are not an estate).
- Unit: one alert rule; a Prometheus rule group counts once per alert rule. A rule counts as
  converted when its confidence is approximate or better. Nothing is "exact": metric names are
  derived from a documented rule and have not been seen arriving (UNVERIFIED).
- Reproduce: `importer.sources.azure.baseline.measure()`; `tests/test_azure_baseline.py` keeps this
  table equal to the code.

## Result

| Alert type | Rules | Converted (approximate or better) | Manual | Unsupported |
|---|---|---|---|---|
| Metric alerts | 22 | 10 | 12 | 0 |
| Scheduled query rules (KQL) | 15 | 7 | 8 | 0 |
| Activity log alerts | 10 | 8 | 2 | 0 |
| Prometheus rule groups | 7 | 0 | 7 | 0 |
| Smart detector rules | 7 | 0 | 0 | 7 |
| All | 61 | 25 | 29 | 7 |

Before the metric, activity and KQL work of 2026-11-11 the same table read: metric alerts 6 of 22,
scheduled query rules 0 of 15, activity log alerts 0 of 10, all 6 of 61. The scheduled query rule
row is measured with the KQL subset translator (docs/research/azure-kql.md); the 7 Perf rules
convert with the data path `needs-agent` and UNVERIFIED names. The distinct-rule count below was taken before that work
(38 rules, 4 convert) and has not been repeated.

Live estate fixtures (`tests/fixtures/azure-live/`, the 11 metric alerts and 3 activity log alerts
of the test subscription): metric alerts 4 of 11 before, 10 of 11 after; activity log alerts 0 of 3
before, 3 of 3 after. The one live metric alert left is a one-day window.

## Data path of the converted rules

Shown the way the Google source shows it (`IRMonitor.data_path`): what must be in place for the
converted query to return data. `native` is left out of saved IR.

| Alert type | native | needs-log-forwarding | needs-prometheus-remote-write |
|---|---|---|---|
| Metric alerts (official examples) | 8 | 0 | 2 |
| Activity log alerts (official examples) | 0 | 8 | 0 |
| Metric alerts (live) | 10 | 0 | 0 |
| Activity log alerts (live) | 0 | 3 | 0 |

The 7 converted scheduled query rules (Perf table) carry the data path `needs-agent`.

## What converts

Metric alerts: static thresholds on a platform metric for one resource, several resources or a
resource group or subscription prefix; windows up to six hours (a note says no violation can open
until a full window has passed); several criteria that share scope, dimensions and window, written
as one `if(test AND test, 1, 0)` query above 0; dynamic thresholds as an APPROXIMATE baseline
condition (High 2, Medium 3, Low 4 standard deviations, UNVERIFIED, never equivalent); dimension
operators Include, Exclude and StartsWith with one prefix; and PromQL metric alerts. They become NRQL
conditions on `Metric` with `collector.name = 'azure-monitor'`, the event timer, a sliding window
when the evaluation frequency is shorter than the window, and the original Azure severity written
into the description.

Activity log alerts: conditions made of equals tests (several values of one field become IN),
one `containsAny` value, and a resource, resource group or subscription scope become a count of
forwarded activity log records at or above 1 over 60 seconds. This includes service health and
resource health, because the Event Hub forwarder template lists both categories. All attribute
names are UNVERIFIED.

## What stays manual or unsupported, by alert type, and why

| Alert type | Main blockers (count) | Why |
|---|---|---|
| Metric alerts | metric name not writable as a New Relic name (4); several resource groups in one scope (2); availability test (2); PromQL form not supported (2); dynamic threshold that cannot be read (1); window over six hours (1) | guest metrics such as `\Processor(_Total)\% Processor Time` are custom metrics the Azure Monitor integration does not collect; one New Relic condition matches one resource id prefix (OR of LIKE is not expressible); availability tests belong to Synthetics; no aggregation window above six hours exists; multi-criteria rules with different dimensions or an unconvertible criterion stay manual (none in the official examples) |
| Scheduled query rules (KQL) | table from an agent or service New Relic does not read (6: Heartbeat); dynamic threshold (2) | A count of `Heartbeat` rows with no filter cannot be told apart from other logs once the table name is gone; the 7 Perf rules convert with the data path `needs-agent` and UNVERIFIED names |
| Activity log alerts | condition cannot be converted (2) | An OR across different fields, several containsAny values, a tenant scope or a field the record has no counterpart for (resourceGroup, resourceType, caller); the rule is manual with the condition written out |
| Prometheus rule groups | PromQL the Grafana translator refuses (7) | The examples use recorded names with colons and `histogram_quantile`; managed Prometheus data has no documented export to New Relic |
| Smart detector rules | machine-learning detector (7) | No New Relic equivalent (unsupported, with a baseline condition suggested as a replacement) |

## What this does not tell us

- Whether the derived metric names, dimension attribute names, resource id casing or the forwarded
  log attribute names are right. Every converted rule says UNVERIFIED; `check-azure-data` is the
  way to confirm them against an account that receives the data.
- How a real tenant's rules look. The official examples are small and use few tables; a real
  estate probably holds more simple resource-log counts (convertible) and more multi-criteria
  rules (manual).
- Latency. The notes say New Relic opens later than Azure; the size of the gap is not measured.
