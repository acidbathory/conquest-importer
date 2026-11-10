# Runbook: Grafana to New Relic

For observability engineers and architects who will run the importer against a Grafana stack.
Status: prototype. Read section 1 before you promise anything to anyone.

State described here: the `integration/all-threads` branch, 2026-11-10. Every command, flag and
default in this page was checked against `importer <command> --help`. Anything marked UNVERIFIED
has not been seen working on a live system.

## 1. What you get, and what you do not get

What the importer does for Grafana:

| Asset | What happens | Honest status |
|---|---|---|
| Alert rules (Grafana-managed) | Rules that are a query, an optional reduce and one threshold convert to New Relic NRQL alert conditions, created disabled. | Every converted rule is labelled approximate. None is exact. On our scrubbed sandbox file, 5 of 17 sample rules converted and 12 stayed manual. That is a test set we wrote, not a customer result. |
| Dashboards | Panels the planner can read become widgets. Every other panel becomes a visible "Not migrated" widget with the reason. Dashboards are created private. | On 6 popular community dashboards (243 panels): 3 approximate, 240 manual, and only 1 of the 6 dashboards could be emitted. On the 13 built-in dashboards of a Grafana Cloud stack (245 panels): 39 exact, 7 approximate, 199 manual, 9 dashboards emitted. Most manual panels use chained variables, `without()`, transformations or vector matching. |
| Notifications (contact points, policies, mute timings) | Planned as New Relic destinations, workflows and muting rules. Email, webhook and PagerDuty convert. Slack, Teams, Jira, SNS and Pushover are manual steps. | Offline plans only. No payload or workflow was tested against a live receiver. UNVERIFIED. |
| Data (metrics, logs) | Not moved. You must send new data to New Relic. | No documented path copies data stored in Grafana Cloud into New Relic. History stays in Grafana. |

What was proven in a sandbox:

- Alert firing test (2026-11-10): two synthetic Grafana rules were converted, applied, enabled and fed
  test data through the New Relic Metric API. Both opened an incident, and both closed on recovery.
  Detection took 4 min 19 s and 5 min 12 s after the first breaching sample. Closing took 4 min 8 s
  and 7 min 21 s after recovery. Nothing could notify anyone: no destination or workflow existed.
  Details: [live-alert-firing-test](../research/live-alert-firing-test.md).
- Counter rates and gauge averages: converted queries matched the arithmetic on the points we sent in
  19 of 20 runs ([counter round trip](../research/live-grafana-counter-roundtrip.md)). The one
  exception is a "latest" value across many series.
- One dashboard widget with real source data matched Grafana
  ([data round trip](../research/live-grafana-data-roundtrip.md)).

What you do NOT get:

- No proof on customer data. Live verification on real customer estates is 0.
- No automatic data path. Converted dashboards and alerts stay empty until your metrics reach New
  Relic under the names the queries use (section 5).
- Notifications are untested live. Treat them as a draft plan.
- Behaviour differs. New Relic evaluates fixed windows, Grafana looks back from each evaluation.
  Alerts can open later than in Grafana (about 5 minutes in our test) and close later.
- `keep_firing_for`, recovery thresholds, group wait, group interval and repeat interval are lost.
- Some rules never convert: math across queries, range thresholds, several queries, most PromQL
  and LogQL beyond plain selectors, rates, ratios and `absent()`.
- Internal rubric (our own scoring, not a customer measurement): coverage about 49 out of 100,
  output ready about 12 out of 100 for Grafana.

## 2. Prerequisites

Accounts and access:

- A Grafana Cloud stack, or a self-hosted Grafana over https.
- A Grafana service account token. Use read-only access. The importer sends only `GET` requests, on a
  fixed list of paths. It needs to read: alert rules, contact points, notification policies, mute
  timings, templates, data sources (type and uid), dashboards, folders and library panels.
  Start with the Viewer role. If a command answers 403, add only the missing read permission.
  UNVERIFIED: which exact Grafana RBAC action each path needs on your version.
- Check the token before you use it. In our own sandbox a token requested as Viewer turned out to hold
  32 alerting write permissions. Check it with the Grafana API
  `GET /api/access-control/user/permissions`, give it a short expiry, and delete it afterwards.
- A New Relic User API key (starts with `NRAK-`). It is needed only to create things and to run data
  checks. A plan-only run does not need it. See [permissions](../permissions.md).
- A New Relic account to migrate into. Use a test account first.

Settings go in `.env` in the repository root. It is git-ignored. Never paste real values into chat,
tickets or documents.

```sh
GRAFANA_URL=https://<stack>.grafana.net
GRAFANA_SA_TOKEN=
NEW_RELIC_API_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
# Only behind a proxy that inspects TLS:
SSL_CERT_FILE=
```

- `NEW_RELIC_ALLOWED_ACCOUNT_IDS` is a comma-separated allow-list of accounts the tool may write to.
  Any other account is refused. Put your test account id there, nothing else.
- The extractors accept only `https://<stack>.grafana.net`. For a self-hosted Grafana add
  `--allow-host <host name>`; it must equal the host in `GRAFANA_URL`.

Install (Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
uv sync
source .venv/bin/activate     # then `importer` is on your path
importer doctor --source grafana
```

`doctor` is offline for Grafana. It checks Python, the token and URL are set, and the New Relic key.
Without activating the environment, put `uv run --active` in front of every `importer` command.

Corporate TLS inspection: if your network re-signs HTTPS, point `SSL_CERT_FILE` at a PEM file with
the corporate root certificate. The importer keeps certificate verification on. Never turn it off.
`scripts/migrate.sh` sets `SSL_CERT_FILE=/tmp/q/ca.pem` for you only if that file exists.

## 3. The easy way

```sh
bash scripts/migrate.sh
```

The script loads `.env`, then runs `importer menu`. Nothing is created unless you type `yes`.
The menu needs a person at the keyboard. Piped input is refused.

Screens, in order:

1. Main menu. Type `3` for Grafana.
2. If `GRAFANA_URL` is not set, it asks for it. If `GRAFANA_SA_TOKEN` is not set, it asks with a hidden
   prompt. The token stays in memory only.
3. Step 1 of 4: checking your setup (`doctor`).
4. Step 2 of 4: reading everything Grafana offers, read-only. Alert rules, notifications, dashboards.
5. Step 3 of 4: converting, local files only. It also writes the remediation plan, the data-path
   guide, the dashboard ranking and the quickstart match.
6. Step 4 of 4: preview. "N will be created, M need your attention" for alerts, notifications and
   dashboards. The New Relic account id (asked for if `NEW_RELIC_ACCOUNT_ID` is not set). The top
   five reasons items need attention. A line telling you the metrics must reach New Relic.
7. One question naming the account and the counts. Only `yes` continues. Anything else creates nothing.
8. Creation, in order: alert conditions, notifications, dashboards. Then a Done screen with the report
   folder, the remediation plan and how to undo. The HTML report opens in your browser after the preview and again after a successful create (not when stdin or stdout is not a terminal, with `IMPORTER_NO_BROWSER=1`, or with `importer menu --no-open`); the `file://` path is always printed. See [menu and rollback](menu-and-rollback.md).

Everything the menu writes goes under `out/guided/grafana-<timestamp>/`:
`export/`, `converted/` (alerts, notifications, dashboards), `remediation/`, `datapath/`, `triage/`
and `quickstarts/`.

Limits of the menu: it creates in the US data centre only. For an EU account use the manual way
with `--region eu`. It does not run `check-grafana-data`; run that yourself (section 6).
Other menu items: `5` roll back a run, `6` check setup, `7` sample data (GCP sample), `8` what did I
just do. More detail: [menu and rollback](menu-and-rollback.md).

## 4. The manual way

Run from the repository root with the environment active. Use a new `--out` folder each time.
Commands marked WRITES TO NEW RELIC change your account. All others read the source or local files only.

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer doctor --source grafana` | environment | nothing | no |
| 2 | `importer extract-grafana --out out/grafana` | Grafana alert rules (GET) | `out/grafana/` | no |
| 3 | `importer convert --source grafana --in out/grafana --out out/grafana/alerts` | step 2 | IR, NerdGraph JSON, `main.tf`, `report.md` | no |
| 4 | `importer extract-grafana-notifications --out out/grafana-notifications` | Grafana contact points, policies, mute timings, templates, rules (GET) | `out/grafana-notifications/` | no |
| 5 | `importer convert-grafana-notifications --in out/grafana-notifications --alerts out/grafana/alerts --rules out/grafana --out out/grafana-notification-plans` | steps 3, 4 | plans, muting rules, `notifications.tf`, `secrets.tfvars.example`, `report.md` | no |
| 6 | `importer extract-grafana-dashboards --out out/grafana-dashboards` | Grafana dashboards, folders, library panels (GET) | `out/grafana-dashboards/` | no |
| 7 | `importer plan-grafana-dashboards --in out/grafana-dashboards --out out/grafana-dashboards-plan` | step 6 | `report.md`, `plan.json` | no |
| 8 | `importer triage-grafana-dashboards --in out/grafana-dashboards --out out/grafana-triage [--usage usage.csv]` | step 6 | ranking: migrate now, later, quickstart, skip | no |
| 9 | `importer plan-grafana-quickstarts --in out/grafana-dashboards --out out/grafana-quickstarts` | step 6 | quickstart match | no |
| 10 | `importer grafana-prom-hints --datasource <uid-or-name> --metrics-from out/grafana-dashboards --out hints.json` (optional) | Prometheus through Grafana (GET) | `hints.json` (must not exist yet) | no |
| 11 | `importer convert-grafana-dashboards --in out/grafana-dashboards --out out/grafana-dash [--hints hints.json] [--name-map map.json]` | step 6 | `dashboard-ir/`, NerdGraph JSON, `dashboards.tf`, `report.md` | no |
| 12 | `importer plan-grafana-datapath --out out/grafana-datapath --export out/grafana-notifications --alerts out/grafana/alerts --dashboards out/grafana-dash [--region us\|eu\|jp] [--update-report]` | steps 3, 4, 11 | remote-write block, Alloy and collector snippets, `datapath-plan.md` | no. It sends nothing and never reads a key |
| 13 | `importer plan-remediation --in out --out out/remediation` | converted IR | `remediation-plan.md`, `remediation-plan.csv` | no |
| 14 | `importer apply --in out/grafana/alerts --account-id <account-id>` | step 3 | prints the plan (dry run) | no |
| 15 | `importer apply --in out/grafana/alerts --account-id <account-id> --apply --confirm-account <account-id>` | step 3 | `manifest.json` | WRITES: policy `Imported by Conquest Importer` and disabled conditions |
| 16 | `importer apply-notifications --in out/grafana-notification-plans --account-id <account-id> [--apply --confirm-account <account-id>] [--include-needs-review]` | step 5 | `manifest.json` | WRITES with `--apply`: destinations, channels, disabled workflows |
| 17 | `importer apply-dashboards --in out/grafana-dash --account-id <account-id> [--apply --confirm-account <account-id>]` | step 11 | `manifest.json` | WRITES with `--apply`: private dashboards |
| 18 | `importer check-grafana-data --alerts out/grafana/alerts --nr-account-id <account-id> [--hours 24] [--out check.md]` | New Relic (read-only NRQL) | optional markdown | reads only. Exit code 2 means some condition has no data |
| 19 | `importer report out/grafana --source grafana` | step 3 | `report.html` | no |

Defaults worth knowing: `--region` is `us`, `--policy-name` is `Imported by Conquest Importer`,
`--hours` is 24, notification object prefix is `Imported:`. Every `apply*` command is a dry run unless
you give `--apply` and `--confirm-account` with the same account id, and the account is on
`NEW_RELIC_ALLOWED_ACCOUNT_IDS`. The commands only create. They never update. An object that already
exists by name is skipped.

Notes on single steps:

- Step 4 needs a new folder, not the one step 2 wrote.
- Step 5: without `--rules`, a policy that matches on a rule label is reported as undecidable.
  `--destinations-map` fills addresses and secret names the export cannot hold. Nothing secret is read.
  Webhook and PagerDuty secrets are named in `secrets.tfvars.example`. You supply them as
  `TF_VAR_nr_secret_...` when you run Terraform.
- Steps 11 and 12 write to different folders. Do not point two commands at one folder.
- Step 15 creates conditions disabled. Step 17 creates dashboards private.
- Developer-only sandbox walkthrough for dashboards:
  [steps README](../../scripts/live-test/grafana-sandbox/steps/README.md). It publishes sample
  dashboards into a Grafana folder first. Do not run it against a production Grafana.

## 5. The data path

Converted alerts and dashboards read the Prometheus metric names exactly as they are. They return
nothing until those metrics exist in New Relic. A `sum()` over a metric that is missing returns 0, not
an error, so an empty condition looks healthy. It will never fire.

Routes that exist for Grafana data:

1. Prometheus `remote_write` to the New Relic Metric API. `plan-grafana-datapath` writes a ready
   `prometheus-remote-write.yml`. The URL it writes is
   `https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=<name>`. Use an ingest
   license key, not a user API key, held in a file. The generated files hold a placeholder only.
2. Grafana Alloy, with dual write (keep Grafana, also send to New Relic) or OTLP. Snippets:
   `alloy-dual-write.alloy` and `alloy-otlp.alloy`.
3. An OpenTelemetry Collector (`otel-collector.yml`).
4. Loki logs: forward logs to New Relic logs. LogQL conditions become counts of `Log` events.

What to know before you start:

- No documented way copies data already stored in Grafana Cloud into New Relic. You send new data.
- Types are guessed from the name. A name ending `_total`, `_count`, `_sum` or `_bucket` is treated as
  a counter. `grafana-prom-hints` reads the real type from Prometheus. Its live run on a Grafana Cloud
  stack found empty metadata, so the type path is tested on fixtures only.
- A label called `metricName` collides with the New Relic attribute that holds the metric name.
  The generated file shows a relabel rule. UNVERIFIED against a live remote-write feed.
- If a metric arrives under another name or event, give `--name-map` to the dashboard conversion.
- Remote write itself was not tested end to end. The round trips sent points with the Metric API in
  the same form remote write is documented to produce.

`datapath-plan.md` (with `--alerts` and `--dashboards`) lists, for each converted rule and tile, the
step after which it has data. Read it.

## 6. Verification checklist

Do these in a test account, in this order.

- [ ] `report.md` of each conversion read. Items marked approximate have notes. Read them first.
- [ ] `importer apply ... --account-id <account-id>` dry run read. Names and counts are as expected.
- [ ] After `--apply`: in New Relic, open Alerts, then Alert conditions. Find the policy
      `Imported by Conquest Importer`. Every condition is disabled. Open one and compare the NRQL, the
      window and the threshold with the Grafana rule.
- [ ] Dashboards: open Dashboards. They are private. Widgets named "Not migrated" are expected.
- [ ] Data arrives. Run `importer check-grafana-data --alerts out/grafana/alerts --nr-account-id <account-id> --hours 24`.
      Exit code 0 means each condition's metric or logs were seen. Exit code 2 means at least one
      condition would be blind, or a filter attribute is missing. Fix the data path and run it again.
- [ ] Parity. Where the same data is in Prometheus and in New Relic, let the importer compare the
      numbers instead of by eye (section 6.1). Menu item 9 does it for the newest conversion.
- [ ] Run each converted NRQL by hand in the New Relic query builder and compare it with Grafana for
      the same time range. Expect differences for stat widgets and `increase()` (section 9).
- [ ] Firing test, one condition at a time. Enable one condition in the test account. Send data that
      crosses the threshold, hold it for longer than the duration, then send normal data. Expect an
      incident to open about 5 minutes after the first breaching sample for a 60-second window with a
      2-minute duration (window, duration, plus the aggregation delay of 120 s, plus about 50 s).
      See it with `FROM NrAiIncident SELECT * SINCE 1 hour ago`. Our scripted version is
      `scripts/live-test/grafana-sandbox/alert-fire/fire.py` (a developer script, see
      [script reference](script-reference.md)). Do not enable a condition in production until a test
      like this has passed for that kind of rule.
- [ ] Notifications: nothing was tested live. Send a test notification to a throwaway address first.
- [ ] Enable conditions one by one. Only after data and the firing test look right.

### 6.1 Parity check: do the converted queries show the same numbers?

`verify-parity` evaluates the original PromQL (or a LogQL count) over a window on your Prometheus,
and the converted NRQL over the same window on New Relic, then compares them. It is read-only on
both sides.

```
importer verify-parity --in out/guided/<run>/converted --export out/guided/<run>/export \
  --source-prom http://127.0.0.1:9090 --nr-account-id <account-id> --hours 6 --step 60
```

- `--source-prom` is a Prometheus URL (this machine, or an https host named again with
  `--allow-host`), or a Grafana data source uid or name, read through the data source proxy with
  `GRAFANA_URL` and `GRAFANA_SA_TOKEN`. Only GET calls on `query_range` and the data source lookup
  run. `--source-loki` does the same for LogQL count queries.
- The converted folders keep only NRQL, so `--export` points at the export the conversion started
  from. Use the same `--name-map` and `--hints` as for the conversion; a query that cannot be found
  is reported, not guessed.
- Dashboard variables take the dashboard default. A variable with no single default (an "all" value
  or a query variable) must be given: `--var job=node`. Otherwise the item is NOT COMPARABLE.
- `--watch --every 10m` repeats and rewrites the report until Ctrl-C.

How the numbers are lined up: the window and the step are whole multiples of each other. New
Relic returns one value per bucket (average or sum of the samples in it; counters as deltas).
Prometheus returns the value at the end of each step, so each source point is moved back one step to
the bucket it describes. The first and last buckets are dropped. Series are matched by label set,
with `__name__` dropped and a label called `metricName` read as `metric_name`, as the data path
does. Each point counts as agreeing when it is within `--tolerance` (default 5%) relative, or
within 1e-9 absolute.

| Verdict | Meaning |
|---|---|
| VERIFIED | At least 95% of the aligned points agree, over at least 10 points, no series missing |
| CLOSE | At least 75% agree, or the median error is within twice the tolerance, or a series is missing or an answer was cut by a cap |
| MISMATCH | Below that. The report gives the likely cause: bucket alignment, a constant ratio (counter versus gauge, a summed gauge, a rate window), labels, or missing series |
| NO DATA | One side returned nothing. This says the data path has a gap, not that the query is wrong |
| NOT COMPARABLE | A stat or table widget, a variable with no value, an original that was not found, too few points |

Limits: it means something only where the same data is in both places. A VERIFIED widget is verified
for that window and those variable values. Spiky series can differ by design, because a bucket
average is not an instant value. Pass `parity/verified-labels.json` to
`importer convert-grafana-dashboards --parity` and the report marks verified widgets
"verified on <date>"; without `--parity` the report is unchanged.

## 7. Rollback and undo

Everything created is disabled or private, and recorded.

- Menu: `bash scripts/migrate.sh`, then `5`. It lists recorded runs, shows exactly what would be
  deleted and asks for `yes`. A menu run for Grafana is one run, so one rollback removes the
  conditions, workflows and dashboards together.
- `importer runs` lists recorded runs, newest first. Read-only.
- `importer rollback-run` does the same as menu item 5 from the command line.
- `importer rollback --manifest out/grafana/alerts/manifest.json --account-id <account-id>` previews.
  Add `--apply --confirm-account <account-id>` to delete. Each object is re-read and deleted only if
  its id still has the recorded name. A policy that still holds conditions that are not ours is kept.
  Use the folder you gave `--manifest` to the apply command (default `<in>/manifest.json`).
- Strictness. A run records whether it was a sandbox. If the account was on
  `NEW_RELIC_ALLOWED_ACCOUNT_IDS` (or the list was not set), the run counts as sandbox. Rollback then
  deletes only names starting `ckg-`, `Imported:` or the policy name `Imported by Conquest Importer`.
  A dashboard with its normal Grafana title is listed and left alone. In a real migration the
  dashboards keep their titles. To remove them by kind, run `importer rollback-run --real-migration`.
  Each dashboard is read again first. If its name or description changed since the run, it is refused.
- Dashboards can keep showing in New Relic search for a few minutes after deletion.
- Grafana is never changed by the importer, so there is nothing to undo there.

## 8. Known limits and the remediation plan

Known limits, in short:

- Rules: no `offset`, subqueries, `topk`, `group_left`, `histogram_quantile`, `irate`, regex line
  filters in LogQL, math across queries, several queries, SQL expressions, other data source types
  (CloudWatch, Azure Monitor, Elasticsearch, SQL), recording rules, range thresholds.
- Window rules: reducers that look back use the query range in whole minutes, 60 seconds to 1 hour.
  The pending period becomes the threshold duration. New Relic restarts the count when a window has
  no data.
- No-data state: `Alerting` becomes loss of signal. `NoData`, `KeepLast` and `OK` have no equivalent.
  Loss of signal and no-data were not exercised in the firing test.
- Dashboards: transformations, most chained variables, annotations, links and the original time range
  are not carried over. A repeated panel becomes one widget with a `FACET`.
- Contact points `threema`, `LINE`, `mqtt`, `wechat` have no New Relic equivalent.
- Deeper Grafana conversion work exists on separate, unmerged branches. It is not described here.

The remediation plan (`importer plan-remediation --in <folder> --out out/remediation`, or written for
you by the menu) is offline and read-only. It lists every item that did not convert and, for the
common blockers, the ranked methods from the [remediation cookbook](remediation-cookbook.md), the
recommended one first, with what to ask the customer.

- `remediation-plan.md`: first the five fixes that unlock the most items, then a table of groups
  (reason, number of items, how many only that reason blocks, effort per item), then one section per
  group with what Grafana did, the closest New Relic construct, what will differ, numbered steps, links,
  and a checklist with one box per item.
- `remediation-plan.csv`: one row per item with source, kind, name, reason, effort and steps. Use it as
  a work list in a spreadsheet.
- Effort figures are a planning guide, not a measurement.

Work the top five fixes first. Each line says how many items are completely done after that fix.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectError` or "certificate verify failed" when reading Grafana or New Relic | Your network inspects and re-signs TLS. The system certificate bundle was not enough for the HTTP client in our tests. | Set `SSL_CERT_FILE` to the corporate root certificate PEM. Verification stays on. Do not disable it and do not bypass the proxy. |
| 401 from Grafana | Token wrong, expired or revoked | Create a new service account token and put it in `.env`. |
| 403 from Grafana on one command only | The token lacks a read permission for that path. Dashboards need different reads from alert rules. | Add only the missing read permission. Do not give write access. |
| `GRAFANA_URL must look like https://<stack>.grafana.net` | The importer accepts only Grafana Cloud hosts by default. | For self-hosted use https and `--allow-host <host>` equal to the host in `GRAFANA_URL`. |
| "use a new --out folder" or the notifications export refuses the folder | The importer never mixes runs. | Pick a new folder. Keep `extract-grafana-notifications` out of the folder `extract-grafana` used. |
| `NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set`, or account not in the list | The write safety catch | Add the account id to the variable. Check that `--confirm-account` repeats the account id. |
| New Relic 401 or "API key" error | Wrong key type or EU account | Use a User key (`NRAK-`). Add `--region eu` to `apply*` or `--nr-region eu` to checks for EU. |
| NerdGraph error mentioning `ACCOUNT_ID_PLACEHOLDER` when you paste dashboard JSON by hand | Dashboard variables written as NRQL carry a placeholder for the account id. `apply-dashboards` replaces it with the real account. | Create dashboards with `apply-dashboards`. If you import JSON yourself, replace the placeholder with your account id first. |
| A converted dashboard or condition shows nothing | The metric is not in New Relic, or it has another name or attribute. | Do the data path (section 5). Run `check-grafana-data`. Use `--name-map` if names differ. |
| A stat widget shows 60 where Grafana shows 1 | `sum()` of a gauge adds every sample in the range. Measured: 60 samples of value 1 gave 60. | Use `latest()` per series and a `FACET`, or accept the approximation. The widget carries a note. |
| A rate chart swings (0 and 2.4 around 1.2) | `TIMESERIES AUTO` picked a bucket that is not a multiple of the scrape interval. Measured with 5 s buckets on 10 s data. | Set a fixed bucket that is a multiple of the scrape interval. The mean stays right. |
| `increase(x[1h])` as a stat is too large | NRQL extrapolates the window rate to an hour. With 10 minutes of data it showed 4,386 where Prometheus showed 720. | Wait until an hour of data exists, or read the chart form. |
| A rate on a cumulative gauge is wrong | Summing a running total adds the total every sample. | Send the counter as a counter, or rebuild the query with `derivative`. The converter does not write that. |
| Condition opens about 5 minutes after the problem started | New Relic needs the window, the duration, a 120 s aggregation delay and about 50 s of processing. Grafana with a 1-minute evaluation opens in 2 to 3 minutes. | Expected. Every pending-period note says so. Shorten the duration if you accept more noise. |
| Incident opens on a window that is mostly baseline, and closes late | New Relic evaluates separate fixed windows, Grafana looks back from each evaluation. Measured: a 300 s window with 79 s of high data still crossed the threshold. | Expected. Check alignment of long windows. |
| A condition never fires | Its metric never arrives. | `check-grafana-data`, then fix the data path. |
| "Imported" objects were skipped on a second run | The importer only creates. It never updates. | After a converter fix, roll back and apply again. |
| Dashboards still listed after rollback | New Relic entity search lags a few minutes. | Check again later. |
| `importer menu` prints one line and exits | Input is piped, not typed. | Run it in a terminal. |
