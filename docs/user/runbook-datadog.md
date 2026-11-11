# Runbook: Datadog to New Relic

For observability engineers and architects who will convert exported Datadog monitors.
Status: first useful release, built offline. Read section 1 before you promise anything to anyone.

State described here: the `improve/datadog-source` branch, 2026-11-11. Every command and flag on this
page was checked against `importer <command> --help`. Nothing has been run against a Datadog account
(the Datadog site is blocked on the project's own network and no account exists) or against live data
in New Relic; anything marked UNVERIFIED has not been seen working. Research behind it:
[verified reference and mapping](../research/datadog-verified.md) and
[measured baseline](../research/datadog-baseline.md).

## 1. What you get, and what you do not get

| Asset | What happens | Honest status |
|---|---|---|
| Metric monitors (`query alert`) with a plain threshold | A static NRQL condition on `Metric`, created disabled. Critical and warning thresholds, the operator, the window (1 minute to 6 hours), `by {tag}` as FACET, the scope as WHERE, arithmetic between series (a ratio) and `.as_count()` on a sum convert. | Every converted monitor is labelled approximate. Metric and tag names are assumed to stay the same and have not been seen arriving (UNVERIFIED). |
| Log monitors (`log alert`) that count lines | A count over the `Log` event with the search as WHERE (ANDed terms, `@x:*`, comparisons, ranges, OR between values of one attribute) and `.by()` as FACET. | Log attribute names are UNVERIFIED. Windows over 6 hours, other rollups (average, percentile, cardinality) and `formula()` are manual. |
| Anomaly, outlier, forecast, change, composite, service check, event, SLO, synthetics, RUM, trace analytics, CI, audit, database, network, cost, process and other types | Manual (or unsupported for an unknown type), each with the exact reason and steps. Anomaly, outlier, forecast and composite name the milestone that will convert them approximately (D5). | Not converted. |
| Dashboards | Converted offline with `importer convert-dashboards --source datadog` (259 of 454 data widgets on the 30 public dashboards; every chart approximate; see [what migrates](../what-migrates.md#datadog-dashboards)). | Metric names assumed unchanged; not run on New Relic. |
| Downtimes, SLO objects, notification handles | Not read by this source yet (D6, D7 in the build plan). | Handles in a message are counted and masked, never copied. |
| Data (metrics, logs, history) | Not moved. See section 5. | History stays in Datadog. |

Measured on 70 real integration monitors: 50 convert (approximate), 20 are manual; see the
[baseline](../research/datadog-baseline.md). That is a sample of Datadog's own templates, not an estate.

What you do NOT get:

- No proof on live data. Live verification of metric names and tags is 0 until `check-datadog-data` has run
  against an account that receives the data.
- Recovery thresholds, reminders (`renotify_*`), `require_full_window`, `new_group_delay`, scheduling options
  and similar settings have no New Relic condition setting. They are listed per monitor in the report under
  "Settings that were not migrated".
- Datadog evaluates a rolling window every minute. For windows of 2 minutes to 1 hour a sliding window of 60
  seconds is used (more data points, UNVERIFIED); above an hour the condition is evaluated once per window and
  a violation can open up to a window late.
- Priority P3 to P5 on a monitor with only a critical threshold makes that threshold a warning term. The
  original priority is written into the condition description. This is a policy choice, change it if wrong.
- Message text goes into the description with every `@handle`, URL, token and key masked. Destinations are not
  created.

## 2. Prerequisites

No Datadog login, API key or network access to Datadog is needed. The input is JSON you already have:

- the list that `GET /api/v1/monitor` returns (saved to a file by whoever can reach Datadog), or one monitor
  or a `{"monitors": [...]}` wrapper; or
- integration monitor templates (`{version, title, definition}`), which the loader unwraps.

Put them in one folder. Unknown fields and options are kept and never cause a rejection. Dashboards in the
same folder are skipped.

For `check-datadog-data` and for creating anything you need a New Relic User API key (starts with `NRAK-`) in
`.env`; see [permissions](../permissions.md). A plan-only run needs neither.

## 3. Convert

```sh
importer doctor --source datadog
importer convert --source datadog --in datadog-monitors --out out/datadog-alerts
```

`convert` writes `ir/`, `nerdgraph/`, `main.tf` and `report.md` (headed "Datadog to New Relic"). Every
condition is created disabled. Read `report.md`: the first reason of every manual monitor says what Datadog
does that New Relic cannot, and `importer plan-remediation --in out/datadog-alerts` turns the reasons into
steps and playbooks.

The menu has the same flow as number 11 (it asks for the folder).

## 4. Names: the part that decides whether anything fires

No source says how a Datadog metric is named in New Relic. The converter assumes the same name on the
`Metric` event, and the same tag as an attribute of the same name. That holds for the OpenTelemetry collector
and StatsD routes as documented and is UNVERIFIED for the Datadog Gateway preview and for host metrics (which
land in `SystemSample`, not as `system.cpu.user`).

1. List what the export uses: `importer scaffold-datadog-names --in datadog-monitors --out name-map.json`.
2. Edit `name-map.json` (keep it beside the export, or point `IMPORTER_DATADOG_NAME_MAP` at it). Per metric:
   `event`, `attribute`, `unit_scale` (multiplies thresholds, for example 1000 for seconds to milliseconds)
   and `kind` (`gauge`, `count` or `rate`, which decides how `.as_count()` is read). Under `attributes`, map
   tag keys that arrive under another name (`env` to `deployment.environment.name`). The file is read
   strictly: a typo fails at load time.
3. Convert again, then `importer check-datadog-data --alerts out/datadog-alerts --nr-account-id <id>`
   (read-only SELECT queries). It exits with code 2 when a converted condition has no data and lists the
   metric names that did arrive with the same start.

## 5. Moving the data

Dual-shipping through the Datadog Gateway API (preview, enabled by New Relic staff), the OpenTelemetry
collector `datadogreceiver`, and DogStatsD through the New Relic StatsD integration are the routes the
research found. None was tested. A Datadog RATE arrives as a delta sum with a marker attribute, not divided
by the interval. Read [section 13 of the research note](../research/datadog-verified.md) before choosing.

## 6. What to do with the manual monitors

Each manual monitor lists steps from its remediation rule; the cookbook collects them:
[remediation cookbook](remediation-cookbook.md). The common ones: anomaly and forecast monitors become baseline
or predictive conditions you tune by hand; change alerts and time shifts have no condition form; service checks
need the check results sent to New Relic; one-day log counts need a dashboard or a 6 hour condition.

## 7. Create, review, roll back

Creating works as for the other sources: [menu and rollback](menu-and-rollback.md). Review each condition,
compare it with Datadog for a day, enable one at a time.

## 8. Other guides

| Your source | Guide |
|---|---|
| AWS CloudWatch | [runbook-aws-cloudwatch.md](runbook-aws-cloudwatch.md) |
| Google Cloud | [runbook-gcp.md](runbook-gcp.md) |
| Azure Monitor | [runbook-azure.md](runbook-azure.md) |
| Grafana | [runbook-grafana.md](runbook-grafana.md) |
| Dynatrace | [runbook-dynatrace.md](runbook-dynatrace.md) |
