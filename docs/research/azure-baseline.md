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
| Metric alerts | 22 | 6 | 16 | 0 |
| Scheduled query rules (KQL) | 15 | 0 | 15 | 0 |
| Activity log alerts | 10 | 0 | 10 | 0 |
| Prometheus rule groups | 7 | 0 | 7 | 0 |
| Smart detector rules | 7 | 0 | 0 | 7 |
| All | 61 | 6 | 48 | 7 |
Rules that differ only in fields the conversion ignores (descriptions, actions, tags) appear in
several example files. Counting each distinct criteria, query, condition or detector once gives 38
rules, of which 4 convert (metric alerts 4 of 16, scheduled query rules 0 of 7, activity log alerts
0 of 8, Prometheus alert rules 0 of 2, smart detector rules 0 of 5). The share is similar either way:
about one rule in ten converts.

## What converts

Six metric alerts: four with a static threshold, one criterion and a platform metric (virtual
machine CPU) on several resources or a subscription prefix, and two PromQL metric alerts. They become NRQL conditions on `Metric` with `collector.name = 'azure-monitor'`, the
event timer, a sliding window when the evaluation frequency is shorter than the window, and the
original Azure severity written into the description.

## What stays manual or unsupported, by alert type, and why

| Alert type | Main blockers (count) | Why |
|---|---|---|
| Metric alerts | dynamic threshold (5); metric name not writable as a New Relic name (4); several resource groups in one scope (2); availability test (2); window over one hour (1); PromQL form not supported (2) | Azure learns a range with its own model and New Relic has no matching sensitivity; guest metrics such as `\Processor(_Total)\% Processor Time` are custom metrics the Azure Monitor integration does not collect; one New Relic condition matches one resource id prefix; availability tests belong to Synthetics; the importer accepts windows up to one hour |
| Scheduled query rules (KQL) | table from an agent (13: Perf 7, Heartbeat 6); dynamic threshold (2) | Perf and Heartbeat are filled by the Azure Monitor agent, whose data New Relic does not read; the examples use no simple count over a resource log table |
| Activity log alerts | activity event (7); service health or resource health (3) | There is no source of Azure activity events in New Relic until the Event Hub forwarder is deployed and its attribute names are known; the rule is manual with the workaround listed |
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
