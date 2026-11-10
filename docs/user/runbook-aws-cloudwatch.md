# Runbook: AWS CloudWatch to New Relic

For observability engineers and architects who will migrate CloudWatch alarms, dashboards and
notifications. This is the most tested source. It is still a prototype.

State described here: the `integration/all-threads` branch, 2026-11-10. Every command, flag and
default was checked against `importer <command> --help`. Anything marked UNVERIFIED has not been
seen working on a live system.

## 1. What you get, and what you do not get

| Asset | What happens | Honest status |
|---|---|---|
| Alarms | Single-metric alarms on 15 AWS services and `Custom/` metrics become NRQL alert conditions, created disabled. This includes percentiles, anomaly-detection alarms on one metric (as baseline conditions), simple metric math (arithmetic only) and alarms on log metric filters. | Every converted alarm is approximate. On our sandbox estate, 71% of assets converted (25 of 35), stable over 12 runs. The 23-alarm synthetic fixture converts 18 of 23. These are our own estates, not customer results. |
| Dashboards | Metric charts and text widgets become private dashboards. | Create, read back and rollback were verified live in a sandbox. Log, alarm-status and Metrics Explorer widgets, and math inside widgets, do not convert. |
| Notifications (SNS) | Email and https subscriptions become destinations, channels and disabled workflows. | Created with placeholder addresses. Real delivery is untested. SMS, Lambda, SQS, mobile push and Firehose are reported with a suggestion, not migrated. |
| Metric data | Terraform for CloudWatch Metric Streams is generated for you to review and deploy. | Verified end to end with real data. Metric names are verified for DynamoDB, Lambda (per function), Kinesis, SQS, SNS, API Gateway, EC2 CPU, ALB, NLB, classic ELB (5xx), RDS, ElastiCache, EBS, S3 requests and ECS services. Every other name is assumed. |
| Log forwarding | Terraform to forward the log groups behind converted log-metric alarms. | The Terraform validates. It was not deployed live. |

What you do NOT get:

- Composite alarms, anomaly bands on math, math functions, statistics other than
  Sum, Average, Min, Max and percentiles, and periods under 60 seconds stay manual, each with written
  steps.
- Logs in general, X-Ray traces, SLOs, synthetics, canaries, EventBridge and health checks are listed
  with steps. They are not converted.
- Alarm actions other than SNS (EC2, Auto Scaling, Systems Manager) are not migrated.
- One region and one account per run.
- No firing test has been done on a converted CloudWatch condition. Notification delivery is untested.
- No customer data has been run. Live verification on real customer estates is 0.
- The headline percentage means converted, not "created and working". On 2026-11-04 the percentage
  counted an API Gateway alarm that New Relic then refused to create. After you apply, the report is
  rewritten as Applied and counts what New Relic accepted.
- Internal rubric (our own scoring, not a customer measurement): coverage about 90 out of 100, output
  ready about 60 out of 100 for AWS.

## 2. Prerequisites

Accounts and access:

- An AWS account and read-only keys. The importer only reads, and uses `boto3`, so you do not need
  the AWS CLI. The exact policy is in [permissions](../permissions.md). The core actions are
  `cloudwatch:DescribeAlarms`, `cloudwatch:ListDashboards`, `cloudwatch:GetDashboard`,
  `sns:ListSubscriptionsByTopic`, and `cloudwatch:GetMetricStatistics` (only for `validate`).
  `sts:GetCallerIdentity` needs no permission. Optional reads for log metric filters, EventBridge,
  canaries and health checks are listed on the same page. Without them alarms on log metrics stay manual.
- A New Relic User API key (`NRAK-...`) to create things. A plan-only run needs none.
- For the data path: Terraform, and a New Relic license (ingest) key. The importer never reads the
  license key. You pass it to Terraform yourself.
- A New Relic test account.

AWS credentials come from the usual places: environment variables, or a profile with `--profile <name>`.
Use keys you can revoke afterwards.

`.env` in the repository root (git-ignored). Never put real values in documents or tickets.

```sh
# AWS: only if you have no login or profile on this machine
# AWS_ACCESS_KEY_ID=
# AWS_SECRET_ACCESS_KEY=
# AWS_SESSION_TOKEN=
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
importer doctor --region us-east-1
```

`doctor` checks Python, your AWS login and the New Relic key. Without activating the environment, put
`uv run --active` in front of each `importer` command. Plain `pip install .` also works if you only
want the `importer` command.

Corporate TLS inspection: the tool uses your operating system trust store. If a call still fails
with a certificate error, set `SSL_CERT_FILE` to a PEM file with the corporate root certificate.
Verification stays on. Never turn it off.

## 3. The easy way

Two easy ways exist. Pick one.

### 3a. The guided menu

```sh
bash scripts/migrate.sh
```

The script loads `.env`, then runs `importer menu`. Nothing is created unless you type `yes`.

Screens, in order:

1. Main menu. Type `2` for AWS.
2. "AWS account id, 12 digits". Then "AWS region [us-east-1]".
3. Step 1 of 4: setup check. If AWS sign-in fails, it tells you to sign in (for example `aws sso login` or
   set `AWS_PROFILE`) and pick again.
4. Step 2 of 4: read. `extract-cloudwatch`. It stops before reading if the signed-in account is not the
   one you typed.
5. Step 3 of 4: convert, local files only. Alarms, SNS notifications, dashboards, then the remediation plan.
6. Step 4 of 4: preview. Counts of alert conditions, notification workflows and dashboards to create,
   and how many need attention. The New Relic account id (asked for if `NEW_RELIC_ACCOUNT_ID` is not set).
7. One question naming the account. Only `yes` continues. Conditions and workflows are created disabled,
   dashboards private.
8. Done screen. Reports are under `out/guided/cloudwatch-<timestamp>/`.

The menu does not write the Metric Streams Terraform and does not run `validate`. It creates in the US
data centre only. Use 3b or the manual way for those. More detail: [menu and rollback](menu-and-rollback.md).

### 3b. The one-command `migrate`

```sh
importer migrate --aws-account-id <aws-account-id> --nr-account-id <account-id> --no-open
```

It reads AWS (read-only), converts, writes a plan and `report.html` to `out/migration-<time>/`, and
also writes the ingest Terraform to `ingest/` (and `logs-ingest/` when needed). Then it asks whether to
create everything. Answer `y` to create. Without the question: `--yes`, or `--apply --confirm-account <account-id>`.
Without either, a script only makes the plan. By default it opens the report in a browser; `--no-open`
stops that. Defaults: `--region us-east-1`, `--nr-region us`. Walkthrough with screenshots of the report:
[getting started](../getting-started.md).

## 4. The manual way

Run from the repository root with the environment active. Use a new `--out` folder each time; the
tool refuses a folder that already holds JSON.

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer doctor --region us-east-1 [--profile <name>]` | environment | nothing | no |
| 2 | `importer extract-cloudwatch --account-id <aws-account-id> --region us-east-1 --out out/cloudwatch` | CloudWatch alarms, dashboards, SNS subscriptions. Runs only describe-alarms, list-dashboards, get-dashboard, sts get-caller-identity and SNS list calls | `out/cloudwatch/alarms`, `dashboards`, `notifications` | no |
| 3 | `importer convert --source cloudwatch --in out/cloudwatch/alarms --out out/cw [--incident-preference PER_CONDITION]` | step 2 | IR, NerdGraph JSON, `main.tf`, `report.md` | no |
| 4 | `importer convert-notifications --topics out/cloudwatch/notifications --alerts out/cw --out out/notifications [--warn-https-payload] [--muting-handling NOTIFY_ALL_ISSUES]` | steps 2, 3 | plans, Terraform, report | no |
| 5 | `importer convert-dashboards --in out/cloudwatch/dashboards --out out/dashboards` | step 2 | dashboard IR, NerdGraph JSON, `dashboards.tf`, report | no |
| 6 | `importer plan-remediation --in out/cw --out out/remediation` | step 3 | `remediation-plan.md`, `.csv` | no |
| 7 | `importer apply --in out/cw --account-id <account-id>` | step 3 | dry run | no |
| 8 | `importer apply --in out/cw --account-id <account-id> --apply --confirm-account <account-id>` | step 3 | `out/cw/manifest.json` | WRITES: policy `Imported by Conquest Importer`, disabled conditions |
| 9 | `importer apply-notifications --in out/notifications --account-id <account-id> [--apply --confirm-account <account-id>] [--include-needs-review]` | step 4 | `manifest.json` | WRITES with `--apply`: destinations, channels, disabled workflows |
| 10 | `importer apply-dashboards --in out/dashboards --account-id <account-id> [--apply --confirm-account <account-id>]` | step 5 | `manifest.json` | WRITES with `--apply`: private dashboards |
| 11 | `importer ingest-setup --alarms out/cloudwatch/alarms --out out/ingest [--aws-region us-east-1] [--nr-region us] [--percentile NAMESPACE:METRIC:p99]` | step 2 | Terraform for Metric Streams | no. Nothing is deployed |
| 12 | `importer ingest-logs-setup --alerts out/cw --out out/logs-ingest` | step 3 | Terraform for Firehose log forwarding | no |
| 13 | `importer validate --alarms out/cloudwatch/alarms --alerts out/cw --account-id <aws-account-id> --nr-account-id <account-id> --since <UTC-time>` | CloudWatch and New Relic (read-only) | optional `--out` markdown | reads only |
| 14 | `importer report <migrate run folder>` | a `migrate` run | `report.html` | no |
| 15 | `importer stats out/cw [--out stats.md]` | converted run | summary without names, safe to share | no |

Defaults: `--region` is `us-east-1` for AWS and `us` for New Relic. Policy name is
`Imported by Conquest Importer`. Notification prefix is `Imported:`. Every `apply*` command is a dry run
unless you give `--apply` and `--confirm-account` with the same account id, and the account is on
`NEW_RELIC_ALLOWED_ACCOUNT_IDS`. They only create. Existing names are skipped, never updated.

Notes:

- Order of creation: conditions, then notifications, then dashboards.
- Step 9 skips plans marked needs-review (Lambda, SQS, Firehose subscriptions) unless you pass
  `--include-needs-review`. A destination that takes a secret reads it from the environment variable
  named in the plan. A missing variable stops the run before the first write.
- Steps 3 and 5 write a Terraform provider block each. Never use the same `--out` for both.
- `convert --incident-preference` writes the preference on the Terraform policy. Without it nothing is written.
- `report` is for `migrate` run folders. The manual flow's `report.md` files are already in each output
  folder.

## 5. The data path

Converted conditions return nothing until CloudWatch metrics reach New Relic. The menu and `convert`
do not move data.

1. Generate the Terraform (step 11, or the `ingest/` folder `migrate` wrote). It sets up CloudWatch
   Metric Streams into New Relic, for only the namespaces your alarms use.
2. Review it. Deploy it yourself. The license key is never written to a file:

```sh
cd out/ingest
export TF_VAR_newrelic_license_key=...      # New Relic license (ingest) key. Never commit it.
export TF_VAR_newrelic_account_id=<account-id>
export TF_VAR_newrelic_api_key=$NEW_RELIC_API_KEY
terraform init && terraform plan            # read it, then: terraform apply
```

3. If you have alarms on log metric filters, do the same for the `logs-ingest/` folder (step 12). Those
   conditions return nothing until the logs arrive.
4. Percentile alarms need extra statistics in the stream. Use `--percentile` on `ingest-setup`.

Names in New Relic: `aws.<service>.<Metric>`, for example `aws.rds.CPUUtilization`. ECS service
metrics carry a `.byService` suffix. Lambda per-function metrics use `.byFunction`.
CloudWatch publishes one series per dimension set and New Relic stores all of them under one name, so
the converter pins the dimension set in each query.

Warnings:

- Do not run New Relic's AWS polling integration and Metric Streams for the same metrics. Totals double.
  `validate` can detect it. Nothing prevents it.
- The stream and CloudWatch lag by several minutes. Compare windows that ended at least ten minutes ago.
- Converted dashboards and alarms stay empty until data arrives. Names for services not listed in
  section 1 are assumed and can return nothing silently. Always run `validate`.

## 6. Verification checklist

- [ ] `report.html` or `report.md` read. Read "Migrated, but read these first".
- [ ] Dry run of every `apply*` command read.
- [ ] After `--apply`: in New Relic open Alerts, Alert conditions, policy `Imported by Conquest Importer`.
      Every condition is disabled. Open one and compare NRQL, window and threshold with the alarm.
- [ ] Dashboards exist and are private. Workflows exist and are disabled.
- [ ] Terraform deployed. After several minutes, run a query such as
      `SELECT count(*) FROM Metric WHERE metricName LIKE 'aws.%' SINCE 30 minutes ago`.
- [ ] Run `importer validate` with `--since` set to when ingestion started. It runs each converted NRQL
      and reads the same window from CloudWatch. Tolerance in our runs was 2%. Each mismatch is listed
      with a likely cause.
- [ ] Firing test: not done for CloudWatch. In a test account, enable one condition, push the metric over
      its threshold for longer than the duration, and expect an incident after the window, the
      duration and the aggregation delay (about 5 minutes in our Grafana test). Check with
      `FROM NrAiIncident SELECT * SINCE 1 hour ago`.
- [ ] Notifications: send a test to a throwaway address. Delivery is untested.
- [ ] Enable conditions one at a time, last.

## 7. Rollback and undo

- Last `migrate` run: `importer rollback`. It lists what would be deleted and asks before deleting.
  For an older run: `importer rollback --manifest out/migration-<time>/manifest.json`.
- Manual flow: `importer rollback --manifest out/cw/manifest.json --account-id <account-id>` previews.
  Add `--apply --confirm-account <account-id>` to delete. Each object is re-read and deleted only if
  its id still has the recorded name. A policy that still holds conditions that are not ours is kept.
  Notifications and dashboards have their own manifests (`out/notifications/manifest.json`,
  `out/dashboards/manifest.json`).
- Menu runs: menu item `5`, or `importer runs` to list and `importer rollback-run` to pick one. You see
  exactly what will be deleted and must type `yes`.
- Strictness. If the account was on `NEW_RELIC_ALLOWED_ACCOUNT_IDS` (or the list was not set), the
  run counts as sandbox. Rollback then deletes only names starting `ckg-`, `Imported:` or the policy
  `Imported by Conquest Importer`. Dashboards keep their CloudWatch names in a real migration. To remove
  those by kind, run `importer rollback-run --real-migration`. Each dashboard is re-read first. If
  its name or description changed, it is refused.
- Terraform you deployed is yours to remove: `terraform destroy` in the same folder.
- AWS is never changed by the importer.
- Dashboards can keep showing in New Relic search for a few minutes after deletion.

## 8. Known limits and the remediation plan

- Alarms: composite, anomaly bands on math, math functions, other statistics, periods under 60 s.
- Log metric filters convert only with simple patterns. Complex patterns stay manual.
- Metric names beyond the verified list are assumed. Unconfirmed: S3 storage metrics (daily), ECS per
  cluster, classic ELB `RequestCount` and `Latency`, other Lambda dimension sets.
- Existing objects are skipped, never updated. After a converter fix, roll back and apply again.
- `migrate` earlier stopped on API Gateway `5XX` alarms and on accounts with dashboards but no alarms
  (test run of 2026-11-04). Whether each is fixed on your build: UNVERIFIED. Watch the first run.

Remediation plan: `importer plan-remediation --in out/cw --out out/remediation` (the menu writes it for you).
It is offline and read-only.

- `remediation-plan.md`: the five fixes that unlock the most items, a table of groups, then a section
  per group with what CloudWatch did, the closest New Relic construct, what will differ, numbered steps,
  links and a checklist.
- `remediation-plan.csv`: one row per item, usable as a work list.
- Effort figures are a planning guide, not a measurement.

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `no AWS credentials found` | No keys and no profile | Export the AWS variables, or pass `--profile`. |
| `ExpiredToken`, `InvalidClientTokenId` | Temporary keys ran out | Get fresh keys. |
| `AccessDenied` on `DescribeAlarms` and others | Missing read action | Add the actions from [permissions](../permissions.md). |
| "the AWS credentials belong to account X, not Y; nothing was read" | Wrong account for `--account-id` | Use the right keys. The check is deliberate. |
| "0 metric alarms" then "No monitors or alarms found" | That region has no alarms | Try another `--region`. Alarms are per region. |
| Certificate error on a company network | Your network re-signs HTTPS | Set `SSL_CERT_FILE` to the corporate root PEM. Do not disable verification. |
| New Relic 401, or "Environment variable NEW_RELIC_API_KEY is not set" | No key, wrong key, or EU account | Use a User key. Add `--nr-region eu` (migrate) or `--region eu` (apply). |
| `NEW_RELIC_ALLOWED_ACCOUNT_IDS is not set` or account not in list | Write safety catch | Add the account id to the variable. |
| "`--confirm-account` must repeat the account id" | Missing or different | Pass the same id. |
| A converted condition shows no data | Stream not deployed, or the metric name or dimension set differs | Deploy the Terraform. Run `validate`. Check the name in the NRQL against `aws.*` metric names in your account. |
| Values are double CloudWatch's | Polling integration and Metric Streams both on | Turn one off. |
| `validate` shows sums too low | Compared too soon after a window closed | Use a window that ended at least ten minutes ago. |
| API Gateway `4XX`/`5XX` condition refused by NerdGraph | Names starting with a digit need backticks. Seen 2026-11-04. | Check the NRQL for `aws.apigateway.5XXError` unquoted. Quote it. Status of the fix on your build: UNVERIFIED. |
| `Custom/` alarm marked manual though it should convert | Namespaces with `-`, `_` or `.` were not recognised (2026-11-04) | Check the reason in the report. Status of the fix: UNVERIFIED. |
| Alert opens late (about 5 minutes) | Window, duration, 120 s aggregation delay, about 50 s processing | Expected. |
| Dashboards "left after rollback" | New Relic entity search lags | Check again after a few minutes. |
| Folder "already contains JSON files" | The importer never mixes runs | Use a new `--out`. |
| `importer menu` prints one line and exits | Input is piped | Run it in a terminal. |
