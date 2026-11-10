# Runbook: Dynatrace to New Relic

For observability engineers and architects who will run the importer against a Dynatrace tenant.
Status: early prototype. Dynatrace is the least proven source. Read section 1 first.

State described here: the `integration/all-threads` branch, 2026-11-10. Every command, flag and
default was checked against `importer <command> --help`. Anything marked UNVERIFIED has not been
seen working on a live tenant.

## 1. What you get, and what you do not get

| Asset | What happens | Honest status |
|---|---|---|
| Workflows (scheduled DQL log-count checks) | A workflow with one `execute-dql-query` task of the form `fetch logs ... filter ... summarize count()` becomes a New Relic NRQL condition counting `Log` events. Created disabled. | Approximate. Built from scrubbed copies of three real objects and a few hand-made ones. Never run on a tenant that has such workflows. On our 8 sample objects (6 workflows and 2 SLOs), 2 converted and 6 stayed manual. That is a test set, not a customer result. |
| Classic alerting: metric events and Davis anomaly detectors | Static-threshold metric events and `timeseries` detectors convert when the metric is in the metric map. Baseline events become baseline conditions. | The shipped metric map has 7 rows. All are UNVERIFIED. Any metric without a row is manual. |
| Davis problem workflows (notifications) | Planned as destinations, channels and a disabled workflow. Email converts. Slack, PagerDuty, Jira, Teams, ServiceNow and Opsgenie become manual entries with steps. | Every plan is marked "needs review". A workflow is planned only when the trigger has an entity-tag filter: on our sample, 2 plans gave 0 workflows. Nothing was delivered live. |
| SLOs | A plan per SLO: target, period (1, 7 or 28 days map), notes. | The good and valid event queries are left as TODO. No service level is created. |
| Dashboards | Plan only. Each tile is exact, draft or manual. No New Relic dashboard is created. | On 4 real dashboards (93 tiles, 59 data tiles, 34 markdown tiles) the stricter planner gives 5 exact, 21 draft and 33 manual data tiles. None of the exact ones has a data source in New Relic: they read Dynatrace self-monitoring or security events. |
| Synthetic monitors, instrumentation inventory | Plans: which New Relic monitor type fits, and which New Relic agent or integration replaces OneAgent for each technology. | Plans only. Nothing is created or installed. |
| Data | Not moved. | There is no automatic route for OneAgent data (section 5). |

What you do NOT get:

- No firing test. No converted Dynatrace item has ever opened a New Relic incident.
- No live proof of any metric name mapping or entity-tag filter (`accumulations.tag.<name>`).
- No Davis AI equivalent. Davis problems, PurePath and OneAgent deep monitoring have no migration path.
- Dynatrace ran workflow queries on a schedule. New Relic evaluates continuously. A "below" condition
  may not fire on an empty result.
- Internal rubric (our own scoring, not a customer measurement): coverage about 49 out of 100, output
  ready about 13 out of 100 for Dynatrace.

## 2. Prerequisites

Accounts and access:

- A Dynatrace Platform tenant (`https://<tenant-id>.apps.dynatrace.com`). Classic SaaS tenants
  (`https://<tenant-id>.live.dynatrace.com`) are accepted by `extract-dynatrace-classic`.
- A Dynatrace token with read-only scopes, per command:

| Command | Token scopes (read only) |
|---|---|
| `extract-dynatrace` (workflows, SLOs) | `automation:workflows:read`, `slo:slos:read` (platform token) |
| `extract-dynatrace-classic` (metric events, Davis detectors, alerting settings) | `settings:objects:read`, `settings:schemas:read` (platform token), or an API token with `settings.read` |
| `extract-dynatrace-inventory` (dashboards, synthetics, entities) | not stated in `--help`. The code mentions `document:documents:read`, `entities.read` and `ReadConfig`. UNVERIFIED: the exact scopes. Start read-only and add only what a 403 asks for. |

- A New Relic User API key (`NRAK-...`), needed only to create things and to run the data checks.
  See [permissions](../permissions.md).
- A New Relic test account.

`.env` in the repository root (git-ignored). Never put real values in documents or tickets.

```sh
DYNATRACE_ENV_URL=https://<tenant-id>.apps.dynatrace.com
DYNATRACE_API_TOKEN=
NEW_RELIC_API_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
# Only behind a proxy that inspects TLS:
SSL_CERT_FILE=
# Optional: your own reviewed metric map for classic conversion
IMPORTER_DYNATRACE_METRIC_MAP=
```

`NEW_RELIC_ALLOWED_ACCOUNT_IDS` is a comma-separated allow-list of accounts the tool may write to.
Any other account is refused.

Install (Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
uv sync
source .venv/bin/activate
importer doctor --source dynatrace
```

`doctor --source dynatrace` makes no network call. It checks the token and tenant address, and warns
if `SSL_CERT_FILE` is not set. Without activating the environment, put `uv run --active` in front of
each `importer` command. Use `--source dynatrace-classic` for the classic path.

Corporate TLS inspection: set `SSL_CERT_FILE` to a PEM file with the corporate root certificate.
Verification stays on. Never turn it off.

## 3. The easy way

```sh
bash scripts/migrate.sh
```

The script loads `.env` and runs `importer menu`. Nothing is created unless you type `yes`.

Screens, in order:

1. Main menu. Type `4` for Dynatrace.
2. Missing `DYNATRACE_ENV_URL` is asked for. Missing `DYNATRACE_API_TOKEN` is asked with a hidden prompt,
   kept in memory only.
3. Step 1 of 4: setup check.
4. Step 2 of 4: read. `extract-dynatrace` for workflows and SLOs, and `extract-dynatrace-inventory` for
   dashboards.
5. Step 3 of 4: convert. `convert --source dynatrace`, `convert-notifications --source dynatrace`,
   `plan-dynatrace-dashboards`, then the remediation plan and the data-path note.
6. Step 4 of 4: preview. Alerts: N will be created, M need attention. Notifications: Dynatrace plans
   are marked needs review, so the menu counts them as needing attention and does not create them.
   Dashboards: "planned only, nothing is created".
7. One question, only `yes` continues. The HTML report opens in your browser after the preview and again after a successful create (not when stdin or stdout is not a terminal, with `IMPORTER_NO_BROWSER=1`, or with `importer menu --no-open`); the `file://` path is always printed. See [menu and rollback](menu-and-rollback.md).  Then conditions are created disabled.
8. Done screen. Reports under `out/guided/dynatrace-<timestamp>/`.

The menu does not run the classic path (`extract-dynatrace-classic`), `plan-service-levels`,
`plan-dynatrace-synthetics`, `plan-dynatrace-reinstrumentation` or any data check. It creates in the
US data centre only. Use the manual way for those. See also [menu and rollback](menu-and-rollback.md).

## 4. The manual way

Run from the repository root with the environment active. Use a new `--out` folder each time.

Path A: workflows and SLOs (Platform)

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer extract-dynatrace --out out/dynatrace` | Dynatrace workflows, SLOs (GET) | `out/dynatrace/` | no |
| 2 | `importer convert --source dynatrace --in out/dynatrace --out out/dynatrace/alerts` | step 1 | IR, NerdGraph JSON, `main.tf`, `report.md` | no |
| 3 | `importer convert-notifications --source dynatrace --topics out/dynatrace --alerts out/dynatrace/alerts --out out/dynatrace/notifications` | step 1 | plans, `notifications.tf`, report | no |
| 4 | `importer plan-service-levels --in out/dynatrace --out out/dynatrace/service-levels` | step 1 | one plan per SLO, commented-out `newrelic_service_level` skeleton | no |
| 5 | `importer plan-remediation --in out/dynatrace --out out/remediation` | step 2 | `remediation-plan.md`, `.csv` | no |
| 6 | `importer apply --in out/dynatrace/alerts --account-id <account-id>` | step 2 | dry run | no |
| 7 | `importer apply --in out/dynatrace/alerts --account-id <account-id> --apply --confirm-account <account-id>` | step 2 | `manifest.json` | WRITES: disabled conditions |
| 8 | `importer apply-notifications --in out/dynatrace/notifications --account-id <account-id> --apply --confirm-account <account-id> --include-needs-review` | step 3 | `manifest.json` | WRITES: destinations, channels, disabled workflows. Skips needs-review plans without `--include-needs-review` |
| 9 | `importer check-dynatrace-data --alerts out/dynatrace/alerts --nr-account-id <account-id> [--hours 24] [--out check.md]` | New Relic (read-only NRQL) | optional markdown | reads only |
| 10 | `importer report out/dynatrace --source dynatrace` | step 2 | `report.html` | no |

Path B: classic alerting (metric events, Davis detectors)

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer extract-dynatrace-classic --out out/dynatrace-classic` | Settings 2.0 objects (GET). Notification secrets, webhook URLs and recipients are dropped before writing | `out/dynatrace-classic/` | no |
| 2 | `importer validate-dynatrace-map my-map.json` (only if you wrote your own map) | the map file | nothing | no |
| 3 | `importer convert --source dynatrace-classic --in out/dynatrace-classic --out out/dynatrace-classic/alerts` | step 1 | IR, NerdGraph JSON, report | no |
| 4 | `importer summarize-dynatrace-classic --alerts out/dynatrace-classic/alerts` | step 3 | prints the rate, with "listed-only" rows on their own line | no |
| 5 | `importer check-dynatrace-metrics --alerts out/dynatrace-classic/alerts --nr-account-id <account-id> [--hours 24]` | New Relic (read-only counts) | optional markdown | reads only. Exit code 2: some condition has no data |
| 6 | `importer apply --in out/dynatrace-classic/alerts --account-id <account-id> [--apply --confirm-account <account-id>]` | step 3 | `manifest.json` with `--apply` | WRITES with `--apply` |

Alerting profiles, problem notifications, maintenance windows and management zones are listed as
manual items only. The HTML `report` command has no profile for classic yet.

Path C: dashboards, synthetics, inventory (plans only, nothing is created)

| # | Command | Reads | Writes |
|---|---|---|---|
| 1 | `importer extract-dynatrace-inventory --out out/dynatrace-inventory` | dashboards, synthetic monitors, entity inventory (GET). An unreadable area is a warning and the rest runs | `out/dynatrace-inventory/` |
| 2 | `importer plan-dynatrace-dashboards --in out/dynatrace-inventory --out out/dynatrace-dashboard-plan` | step 1 (or one dashboard JSON file) | `report.md`, `plan.json` |
| 3 | `importer plan-dynatrace-synthetics --in out/dynatrace-inventory --out out/dynatrace-synthetics-plan` | step 1 | `report.md`, `plan.json` |
| 4 | `importer plan-dynatrace-reinstrumentation --in out/dynatrace-inventory --out out/dynatrace-reinstrumentation-plan` | step 1 | `report.md`, `plan.json` |

Defaults: `--region` is `us`, `--hours` is 24, notification prefix is `Imported:`, policy name is
`Imported by Conquest Importer`. Every `apply*` command is a dry run unless you give `--apply` and
`--confirm-account` with the same account id, and the account is on `NEW_RELIC_ALLOWED_ACCOUNT_IDS`.
They only create. Existing names are skipped, never updated.

Two checks are named differently on purpose. `check-dynatrace-data` looks for matching `Log` events (the
workflow path). `check-dynatrace-metrics` looks for the signal behind each classic condition.

## 5. The data path

There is no automatic route that sends what OneAgent collected into New Relic. OneAgent sends to
Dynatrace. Its local OTLP endpoint receives traces on localhost. It does not forward them. Dynatrace
metric names (`dt.host.cpu.usage`, `builtin:...`) do not exist in New Relic.

Documented routes (from our research, [data path section](../research/dynatrace-dashboards-inventory-datapath.md)):

- Install New Relic agents or integrations next to, or instead of, OneAgent. `plan-dynatrace-reinstrumentation`
  names the agent for each technology it finds.
- Send OpenTelemetry data to both vendors from an OpenTelemetry Collector (dual export).
- Re-point cloud integrations (AWS metric streams, Azure integration) to New Relic.
- Replace log collection with New Relic log forwarding. History stays in Dynatrace Grail.
- Pull Dynatrace metrics through its API and re-ingest them with a bridge you write. Not built here.

Rules for converted items:

- Converted workflow conditions count `Log` events. The logs must be forwarded to New Relic, and the
  attribute names must match. The `loglevel` to `level` mapping is not confirmed.
- Converted classic conditions watch a signal that exists only when an agent, integration or exporter
  sends it. Each conversion says which one in its data prerequisite.
- A metric with no row in the metric map is manual. Write your own map, check it with
  `validate-dynatrace-map`, and name it in `IMPORTER_DYNATRACE_METRIC_MAP`. Mark a row verified only
  after you saw data in your own account.
- Converted conditions and dashboards stay empty until data arrives. A `sum()` over a missing metric
  returns 0, not an error, so an empty condition looks healthy and never fires.

## 6. Verification checklist

- [ ] The report of each conversion read, notes first.
- [ ] Dry-run `apply` output read.
- [ ] After `--apply`: in New Relic open Alerts, Alert conditions, policy `Imported by Conquest Importer`.
      All conditions are disabled. Compare NRQL, window and threshold with the Dynatrace object.
- [ ] Data: run `check-dynatrace-data` (workflows) or `check-dynatrace-metrics` (classic). Exit code 2
      means a condition had no data in the window. For workflows that is not proof of a fault: the
      event may simply not have happened in those hours. Widen `--hours` up to 168.
- [ ] Entity tag filters (`accumulations.tag.<name>`) in notification plans: UNVERIFIED. Test with a
      throwaway workflow.
- [ ] Firing test: not done for any Dynatrace item. Do it on one condition in a test account. Enable it,
      produce a matching log event or metric crossing the threshold, and expect an incident after
      the window, the duration, and about 3 minutes of delay. See
      [live firing test](../research/live-alert-firing-test.md) for how the Grafana one was run.
- [ ] Enable conditions one at a time only after data and the firing test pass.

## 7. Rollback and undo

- Menu: `bash scripts/migrate.sh`, then `5`. It shows what would be deleted and asks for `yes`.
- `importer runs` lists recorded runs. `importer rollback-run` is the same as menu item 5.
- `importer rollback --manifest out/dynatrace/alerts/manifest.json --account-id <account-id>`
  previews. Add `--apply --confirm-account <account-id>` to delete. Each object is re-read and deleted
  only if its id still has the recorded name. Use the path you gave `--manifest` to the apply command
  (default `<in>/manifest.json`).
- Sandbox strictness: if the account was on `NEW_RELIC_ALLOWED_ACCOUNT_IDS` (or the list was not set)
  the run counts as sandbox, and rollback deletes only names starting `ckg-`, `Imported:` or the policy
  `Imported by Conquest Importer`. `--real-migration` on `rollback-run` also lets it delete dashboards
  of a real run by kind, after re-reading each one. Dynatrace dashboards are never created, so this
  matters little here.
- Dynatrace is never changed by the importer.

## 8. Known limits and the remediation plan

- Workflows: only the plain `fetch logs ... summarize count()` form converts. Any other DQL, a `by:`
  grouping, joins, schedules longer than one hour, manual or non-Davis triggers stay manual.
  A window over one hour stays manual. `contains()` is case-sensitive in Dynatrace and how `LIKE`
  treats case in New Relic was not checked.
- Classic: management zone, tag, entity-id and regex filters, `:fold`, `:delta`, `:timeshift`, several
  metrics in one selector, baseline and records-mode detectors, `OUTSIDE` on a static threshold are
  manual. A Dynatrace entity id must be resolved to a name first. Adaptive thresholds become a baseline
  condition with a fixed band of 3 standard deviations. Tune it.
- `violatingSamples` below `samples` converts to something stricter than Dynatrace, and a note says so.
- Duration limit in New Relic is UNVERIFIED: its pages disagree (2 hours or 24 hours).
- Not done: maintenance window conversion, classic notification conversion, service level creation,
  dashboard creation, routing a converted condition to its notification plan.
- Dashboards: tiles that read Dynatrace-only data are flagged "no New Relic data source".

The remediation plan (`importer plan-remediation --in <folder> --out out/remediation`) is offline.
`remediation-plan.md` has the five fixes that unlock the most items, a group table, a section per group
with steps, links and a checklist. `remediation-plan.csv` has one row per item. Effort numbers are a
planning guide.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `ConnectError` or certificate error reading the tenant | Corporate TLS inspection | Set `SSL_CERT_FILE` to the corporate root PEM. `doctor --source dynatrace` warns when it is missing. Never disable verification. |
| 401 from Dynatrace | Token wrong, expired or wrong type for the host | Use a platform token for `*.apps.dynatrace.com` and an API token for `*.live.dynatrace.com`. Create a new one. |
| 403 on one command | The token lacks that command's scope | Add only the read scope from the table in section 2. |
| 404 on a settings schema | The classic proxy does not know the schema | `extract-dynatrace-classic` retries through the Platform settings service on a Platform host. Check the host form. |
| Token seen in chat or a file | Tokens were pasted into chat before | Rotate it. Keep tokens only in `.env`. The tool never stores or prints them. |
| `NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set` or account not in list | Write safety catch | Add the account id. `--confirm-account` must repeat it. |
| Plans skipped, "needs review" | Dynatrace notification plans always need review | Read the plan, then add `--include-needs-review` to `apply-notifications`. |
| `check-dynatrace-data` exits 2 | No matching `Log` events in the window | Forward logs, check the attribute names, widen `--hours`. The event may not have happened. |
| `check-dynatrace-metrics` exits 2 | The signal behind the condition has no data | Install what the data prerequisite names. A condition on a missing metric never fires. |
| Metric "has no row in the map" | Dynatrace names are not New Relic names | Add a reviewed row to your own map; check with `validate-dynatrace-map`. |
| A condition on a log count never opens | New Relic evaluates continuously. A "below" condition may not fire on an empty result. | Use loss of signal, or test it. UNVERIFIED. |
| Alert opens about 5 minutes after the cause | Window plus duration plus 120 s aggregation delay plus about 50 s processing (measured on the Grafana firing test) | Expected. |
| Folder already exists | The importer never mixes runs | Use a new `--out`. |
| `importer menu` prints one line and exits | Input is piped | Run it in a terminal. |
