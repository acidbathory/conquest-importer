# Runbook: Google Cloud Monitoring to New Relic

For observability engineers and architects who will move Google Cloud alert policies, dashboards and
related configuration to New Relic. Tested in a sandbox project. Not tested on customer data.

State described here: the `integration/all-threads` branch, 2026-11-10. Every command, flag and
default was checked against `importer <command> --help`. Anything marked UNVERIFIED has not been
seen working on a live system.

## 1. What you get, and what you do not get

| Asset | What happens | Honest status |
|---|---|---|
| Alert policies | Metric threshold conditions (including percentile series), metric-absence conditions and plain log-match conditions become NRQL conditions, created disabled. | Approximate. 59% of our synthetic sample policies convert (3 of 4 in the shipped fixture). In a sandbox, five conditions converted from real policies ran against live data and returned the expected values. No customer data. |
| Dashboards | Chart, scorecard, table, pie, logs and text widgets, section headers and PromQL charts become widgets in private dashboards; collapsible and single-view groups become pages. Incident lists, alert charts, error reporting panels and MQL or ratio queries are visible "Not migrated" placeholders with steps. | Built and tested on hand-made fixtures. Never run on a real export. |
| Notification channels | One section per set of channels, listing the policies that used it, with steps per channel type. | Manual steps only. The export deliberately holds no addresses, URLs or tokens, so nothing is created. |
| Uptime checks | A list: each check with its path, period, and the New Relic monitor to create. | A list only. The host is not exported. Uptime-check alerts stay manual. |
| Metric data | Terraform links your project to New Relic, for only the services your alerts use. | Verified in a sandbox: first data about 4 minutes after linking. A person runs `terraform apply`. |

What you do NOT get:

- Most PromQL conditions stay manual too (a simple gauge or a sum of rates over one metric converts;
  ratios of plain selectors, `_over_time`, joins, `offset`, subqueries, `topk` and `${NAME}` placeholders do
  not), each with the exact reason. A converted one assumes the metric reaches New Relic by Prometheus
  remote write under its Prometheus name; the notes say how the name was spelled.
- MQL, SQL and ratio conditions, filters with OR, regular expressions or functions,
  and uptime-check alerts stay manual. So do alerts on custom, Ops Agent, Prometheus and log-based
  metrics: New Relic does not document collecting those from Google Cloud, so a converted query would
  return nothing.
- SLOs, log sinks and log-based metrics are not converted.
- No firing test has been done on a converted Google condition.
- **VM CPU utilization does not arrive.** `gcp.compute.instance.cpu.utilization`, `cpu.usage_time` and
  `uptime` never reached New Relic in two sandbox runs (checked for 40 minutes and more than an hour),
  although Google had the data. Network, uptime and memory-balloon VM metrics were also absent. The reason
  is unknown and no other name exists in New Relic (see [diagnosis](../research/gcp-vm-cpu-diagnosis.md)).
  A CPU alert will return nothing. The conversion says so.
- Metric names are confirmed only for Compute Engine, Pub/Sub and Storage: 60 names arrived, and 4
  documented names did not. Cloud Run, Cloud SQL, GKE, load balancing, BigQuery and others carry a
  "not confirmed" note. See [verified names](../research/gcp-metric-names-verified.md).
- New Relic polls Google every 5 minutes. Counter data arrives as one change per poll. Conditions use a
  window of at least 300 seconds and a rescaled threshold. They are slower than Google alerts.
- Internal rubric (our own scoring, not a customer measurement): coverage about 69 out of 100, output
  ready about 43 out of 100 for Google Cloud.

## 2. Prerequisites

Accounts and access:

- A Google Cloud project to read, and a Google login that can read Cloud Monitoring (alert policies,
  notification channels, uptime checks, dashboards). Read-only is enough. The standard read-only role is
  Monitoring Viewer (`roles/monitoring.viewer`). UNVERIFIED: that this is the minimum.
- Sign in one of two ways: `gcloud auth application-default login`, or set `GOOGLE_OAUTH_ACCESS_TOKEN`
  to a short-lived access token. The importer never stores credentials.
- To link the project: someone who can create a service account and workload identity pool, and grant
  IAM roles, in the project. The generated Terraform grants New Relic's read-only service account
  `roles/viewer`, `roles/serviceusage.serviceUsageConsumer` and `roles/cloudasset.viewer`. Review them.
  Narrowing the deployer's own permissions is an open to-do.
- Terraform installed, to deploy the link.
- A New Relic User API key (`NRAK-...`) to create things and to run the data check. See
  [permissions](../permissions.md).
- A New Relic test account.

`.env` in the repository root (git-ignored). Never put real values in documents or tickets.

```sh
# Optional: a short-lived Google access token instead of gcloud sign-in
GOOGLE_OAUTH_ACCESS_TOKEN=
NEW_RELIC_API_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
# Only behind a proxy that inspects TLS:
SSL_CERT_FILE=
```

`NEW_RELIC_ALLOWED_ACCOUNT_IDS` is a comma-separated allow-list of accounts the tool may write to.
Any other account is refused.

Install (Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
uv sync
source .venv/bin/activate
importer doctor --source gcp
```

`doctor --source gcp` checks for a usable Google login. Without activating the environment, put
`uv run --active` in front of each `importer` command.

Corporate TLS inspection: the tool uses your operating system trust store. If a call fails with a
certificate error, set `SSL_CERT_FILE` to a PEM file with the corporate root certificate. Verification
stays on. Never turn it off.

## 3. The easy way

```sh
bash scripts/migrate.sh
```

The script loads `.env`, then runs `importer menu`. Nothing is created unless you type `yes`.

Screens, in order:

1. Main menu. Type `1` for GCP. (Item `7` converts the built-in GCP sample offline and sends nothing.)
2. "Google Cloud project id".
3. Step 1 of 4: setup check. If it fails: sign in with `gcloud auth application-default login` and pick
   again.
4. Step 2 of 4: read. `extract-gcp` reads policies, channels, uptime checks and dashboards.
5. Step 3 of 4: convert, local files only: alerts, notification plans, dashboards, and the remediation plan.
6. Step 4 of 4: preview. Alerts and dashboards: N will be created, M need attention. Notifications:
   "Google channels are manual steps, nothing is created". The New Relic account id (asked for if
   `NEW_RELIC_ACCOUNT_ID` is not set).
7. One question. Only `yes` continues. Conditions are created disabled, dashboards private.
8. Done screen. Files are under `out/guided/gcp-<timestamp>/`. The HTML report opens in your browser after the preview and again after a successful create (not when stdin or stdout is not a terminal, with `IMPORTER_NO_BROWSER=1`, or with `importer menu --no-open`); the `file://` path is always printed. See [menu and rollback](menu-and-rollback.md).

The menu does not write the link Terraform and does not run `check-gcp-data`. It creates in the US data
centre only. Use the manual way for those. More detail: [menu and rollback](menu-and-rollback.md).

## 4. The manual way

Run from the repository root with the environment active. Use a new `--out` folder each time.

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer doctor --source gcp` | Google login | nothing | no |
| 2 | `importer extract-gcp --project <project-id> --out out/gcp` | Cloud Monitoring: alert policies, channels, uptime checks, dashboards (read-only). Channels are saved as type and name only | `out/gcp/policies`, `channels`, `uptime-checks`, `dashboards` | no |
| 3 | `importer convert --source gcp --in out/gcp/policies --out out/gcp/alerts` | step 2 | IR, NerdGraph JSON, `main.tf`, `report.md` | no |
| 4 | `importer convert-gcp-notifications --channels out/gcp/channels --alerts out/gcp/alerts --out out/gcp-notifications` | steps 2, 3 | routing and manual steps | no |
| 5 | `importer convert-gcp-uptime --checks out/gcp/uptime-checks --out out/gcp-uptime` | step 2 | list of monitors to create | no |
| 6 | `importer convert-dashboards --source gcp --in out/gcp/dashboards --out out/gcp-dashboards` | step 2 | dashboard IR, NerdGraph JSON, `dashboards.tf`, report | no |
| 7 | `importer plan-remediation --in out/gcp/alerts --out out/remediation` | step 3 | `remediation-plan.md`, `.csv` | no |
| 8 | `importer gcp-link-terraform --project <project-id> --nr-account-id <account-id> --alerts out/gcp/alerts --out out/gcp-link` | step 3 | `out/gcp-link/main.tf` only | no. Offline. It runs nothing |
| 9 | `terraform init && terraform plan`, read it, then `terraform apply` in `out/gcp-link` | | creates a read-only service account and IAM grants in Google, and the link in New Relic | A PERSON RUNS THIS. It is not an `importer` command |
| 10 | `importer check-gcp-data --alerts out/gcp/alerts --nr-account-id <account-id> [--hours 24] [--out check.md]` | New Relic (read-only NRQL) | optional markdown | reads only. Exit code 2: some condition has no data |
| 11 | `importer apply --in out/gcp/alerts --account-id <account-id>` | step 3 | dry run | no |
| 12 | `importer apply --in out/gcp/alerts --account-id <account-id> --apply --confirm-account <account-id>` | step 3 | `out/gcp/alerts/manifest.json` | WRITES: policy `Imported by Conquest Importer`, disabled conditions |
| 13 | `importer apply-dashboards --in out/gcp-dashboards --account-id <account-id> [--apply --confirm-account <account-id>]` | step 6 | `manifest.json` | WRITES with `--apply`: private dashboards |
| 14 | `importer report out/gcp --source gcp` | step 3 (the report reads the `alerts/` folder inside the folder you name) | `out/gcp/report.html` with Google wording | no |

Pass `--services run --services sql` to step 8 to enable extra service blocks. `--pool-id` defaults to
`newrelic-pool`. Defaults: `--hours` 24, `--region` `us`, notification prefix `Imported:`.
Every `apply*` command is a dry run unless you give `--apply` and `--confirm-account` with the same account id,
and the account is on `NEW_RELIC_ALLOWED_ACCOUNT_IDS`. They only create. Existing names are skipped.

There is no `apply` for Google notification channels or uptime checks. They are manual.

The export can contain project ids and resource names. Review it before sharing.

## 5. The data path

Link the project to New Relic with the Google Cloud integration. This is what `gcp-link-terraform` writes.

- It creates a workload identity pool and a read-only service account. New Relic then polls Google
  Cloud Monitoring about every 5 minutes. Google bills those reads to your project. In our test the
  cost was effectively nothing beside a tiny VM.
- With `--alerts`, it enables only the services your converted conditions use.
- The link is done by a person, with Terraform. The importer never applies it.
- Metric names look like `gcp.compute.instance.disk.read_bytes_count`, `gcp.pubsub.topic.send_request_count`,
  `gcp.storage.api.request_count`. Percentile series arrive for distribution metrics (`.p50`, `.p95`, `.p99`).
- Attributes are `gcp.<label>` for resources and `metric.<label>` for metric labels. Event-style metrics
  carry only `fullResourceName`. If a query that filters on a resource label returns nothing, filter on
  `fullResourceName`.

Warning: converted conditions and dashboards stay empty until data arrives. A `sum()` over a metric that
does not exist returns 0, not an error, so a condition on a missing metric looks healthy and never
fires. Run `check-gcp-data`. Custom, Ops Agent and log-based metrics have no documented route.

## 6. Verification checklist

- [ ] `report.md` of each conversion read, notes first.
- [ ] Dry-run `apply` read.
- [ ] After `--apply`: in New Relic open Alerts, Alert conditions, policy `Imported by Conquest Importer`.
      All conditions are disabled. Compare NRQL, window and threshold with the Google policy.
- [ ] Link applied. Wait about 5 to 10 minutes, then run
      `importer check-gcp-data --alerts out/gcp/alerts --nr-account-id <account-id> --hours 24`.
      Exit code 0: each metric was seen. Exit code 2: at least one condition would be blind. Expect
      the VM CPU conditions to be among them.
- [ ] Run a converted NRQL by hand in the query builder and compare with Cloud Monitoring over the same
      period. In our sandbox the numbers matched within the minutes between polls (871 against 877
      disk writes).
- [ ] Absence conditions: if the metric never arrives, the condition fires as soon as it is enabled.
      The notes say so.
- [ ] Firing test: not done for Google. In a test account, enable one condition, push the metric over
      its threshold for longer than the duration, and expect an incident after the 5-minute
      polling, the window and the aggregation delay. Check with `FROM NrAiIncident SELECT * SINCE 1 hour ago`.
- [ ] Notification channels: build the workflows by hand from the plan, then test one.
- [ ] Enable conditions one at a time, last.

## 7. Rollback and undo

- Menu: `bash scripts/migrate.sh`, then `5`. It lists recorded runs, shows exactly what would be deleted
  and asks for `yes`. `importer runs` lists them. `importer rollback-run` does the same from the command
  line.
- `importer rollback --manifest out/gcp/alerts/manifest.json --account-id <account-id>` previews. Add
  `--apply --confirm-account <account-id>` to delete. Each object is re-read and deleted only if its
  id still has the recorded name. A policy that still holds conditions that are not ours is kept.
  Dashboards have their own manifest (`out/gcp-dashboards/manifest.json`).
- Strictness. If the account was on `NEW_RELIC_ALLOWED_ACCOUNT_IDS` (or the list was not set), the
  run counts as sandbox. Rollback then deletes only names starting `ckg-`, `Imported:` or the policy
  `Imported by Conquest Importer`. Dashboards keep their Google titles in a real migration. To delete
  those by kind, run `importer rollback-run --real-migration`. Each is re-read first. If its name or
  description changed since the run, it is refused.
- The link: run `terraform destroy` in `out/gcp-link`. That removes the service account and grants.
- Google Cloud is never changed by the importer.

## 8. Known limits and the remediation plan

Alert fields beyond the query (threshold, absence and log-match conditions):

| Google field | New Relic result |
|---|---|
| `severity` | `WARNING` is a warning term; `CRITICAL` and unset are critical; `ERROR` is critical with a note (New Relic has two levels). The Google severity is also kept as the tag `gcp-severity:...` and in the description. |
| `alertStrategy.autoClose` | Violation time limit (5 minutes to 30 days); outside that range it is a note. |
| `alertStrategy.notificationRateLimit` | Log alerts: the violation time limit is set to the period, so a violation that keeps matching re-notifies at most that often. Other conditions: a note. |
| `notificationPrompts`, `notificationChannelStrategy` (renotify) | A note: set the workflow to notify on issue activated and closed; New Relic has no repeat reminder. |
| `documentation` subject, format and links; `userLabels`; `labelExtractors` | Listed in the condition description (cut at 1000 characters). The first link without a `${...}` placeholder is the runbook URL. Labels are kept as IR tags. |
| `evaluationMissingData` | `ACTIVE`: loss of signal opens a violation. `INACTIVE`: loss of signal closes open violations. `NO_OP`: nothing to set (noted). New Relic waits for the duration; Google reacts at once. |
| `forecastOptions` | The predict-future option (beta), a different model from Google's. |
| `trigger` | Absence: noted when it needs N series or P percent (New Relic fires when none report). Threshold: manual when several series must breach, unless the aggregation leaves one series. |
| Aligners and reducers | Mean, max, min, sum, rate, delta, percentile 50/95/99 series, `REDUCE_PERCENTILE_50/95/99` and `REDUCE_STDDEV` (one point per series per window only) and `ALIGN_STDDEV` (period of 900 s or more) convert. `ALIGN_COUNT`, the boolean aligners, `ALIGN_PERCENT_CHANGE` and resampling aligners stay manual, each with its reason. |

- Counters: windows are at least 300 seconds. The threshold is rescaled and the note says by how much.
- Log severity: `severity>=ERROR` becomes `severity IN (...)`. The attribute name for forwarded Google
  logs is not confirmed.
- Dashboards: stacked bars show as area charts. Counters are charted as the sum of 5-minute changes, not
  as a rate. Overlapping widgets are moved down.
- Dashboard widgets: a `collapsibleGroup` or `singleViewGroup` tile becomes a page and the tiles below it
  (until the next group) go on that page, a guess from the layout because Google stores no parent. A
  `sectionHeader` is a markdown heading. A `timeSeriesTable` is a table (column settings are lost), a
  `pieChart` a pie, a `logsPanel` a table over `Log` (a filter with OR or parentheses is dropped and the
  note says so). PromQL queries go through the Prometheus translator; `${Cluster}`-style template
  variables become New Relic variables. Metrics from the Ops Agent, custom metrics and log-based metrics
  stay placeholders, because New Relic does not collect them from Google Cloud.
- Notification channel types: `email`, `webhook_tokenauth`, `webhook_basicauth`, `slack`, `pagerduty`,
  `google_chat` (webhook), `sms`, `pubsub` (webhook to a receiver), `hipchat` and `campfire` (deprecated by
  Google, nothing to migrate to). `mobile` is not in Google's descriptor list, so it is unverified and
  manual. Secrets Google obfuscates (Slack `auth_token`, webhook password, PagerDuty `service_key`) are
  named in the plan as `GCP_SLACK_AUTH_TOKEN`, `GCP_WEBHOOK_PASSWORD`, `GCP_PAGERDUTY_SERVICE_KEY`; you enter
  the value in New Relic yourself.
- Names that look like addresses, URLs or tokens are replaced by the resource id in outputs. Other names
  are kept as written, so do not put secrets in names.

Remediation plan: `importer plan-remediation --in out/gcp/alerts --out out/remediation` (the menu writes
it for you). Offline and read-only.

- `remediation-plan.md`: the five fixes that unlock the most items, a group table, then per group: what
  Google did, the closest New Relic construct, what will differ, steps, links and a checklist.
- `remediation-plan.csv`: one row per item.
- Effort figures are a planning guide.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `doctor --source gcp` fails | No Google login | `gcloud auth application-default login`, or set `GOOGLE_OAUTH_ACCESS_TOKEN`. |
| 401 or 403 from Cloud Monitoring | Token expired, or the login lacks read access to Monitoring on that project | Sign in again. Ask for a read-only Monitoring role on the project. |
| Certificate error on a company network | TLS inspection | Set `SSL_CERT_FILE` to the corporate root PEM. Never disable verification. |
| New Relic 401, or key not set | No key, wrong key, EU account | Use a User key. For EU use `--region eu` (apply) or `--nr-region eu` (check). |
| `NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set` or account not listed | Write safety catch | Add the account id. `--confirm-account` must repeat it. |
| VM CPU condition returns nothing | `cpu.utilization`, `cpu.usage_time` and `uptime` do not reach New Relic (unexplained) | Raise with New Relic support (evidence in the [diagnosis](../research/gcp-vm-cpu-diagnosis.md)). No Terraform setting changes it. Keep the Google policy, or alert on the infrastructure agent's `SystemSample.cpuPercent` (unverified). |
| Condition returns nothing for a label filter | Event-style metrics carry no resource label | Filter on `fullResourceName`. |
| Counter condition looks too quiet | Data arrives once per 5-minute poll; a 60 s window sees a point one time in five | Already handled: window of at least 300 s with a rescaled threshold. Do not shorten it. |
| `sum()` returns 0 and no error | The metric does not exist in New Relic | Run `check-gcp-data`. A condition on a missing metric never fires. |
| Cloud Run, Cloud SQL, GKE, load balancing or BigQuery condition returns nothing | Names for those services are not confirmed | Read the "not confirmed" note, find the real name in your account, and edit the query. |
| An absence condition fires as soon as it is enabled | Its metric never arrives | Fix the data first (see VM CPU above). |
| Alert opens late | 5-minute polling, 300 s window, aggregation delay | Expected. |
| Folder already exists | The importer never mixes runs | Use a new `--out`. |
| `importer menu` prints one line and exits | Input is piped | Run it in a terminal. |
