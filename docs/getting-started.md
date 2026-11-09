# Getting started

This walks you from install to rollback in about ten minutes. Nothing is created in New Relic
until you answer `y` in step 5.

You need Python 3.12 or newer, [read-only AWS keys](permissions.md), and, from step 5, a New Relic
API key.

## Step 1: Install

```sh
git clone https://github.com/acidbathory/conquest-importer.git
cd conquest-importer
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
```

## Step 2: Add your settings

AWS uses the login you already have: environment variables, or a profile selected with
`--profile name`. For New Relic, copy the example file and fill it in:

```sh
cp .env.example .env
```

`importer` reads `.env` from the folder you run it in, and variables already set in your shell take
priority. You need three lines to create things in New Relic and none to make a plan:

```sh
NEW_RELIC_API_KEY=NRAK-...               # a User key, see permissions.md
NEW_RELIC_ACCOUNT_ID=1234567             # the account to migrate into
NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567    # the accounts the tool may write to
```

You can run `importer doctor` to check Python, your AWS login and the New Relic key (add
`--source gcp` to check a Google login instead of the AWS one). It's optional,
because `migrate` reports the same problems when it starts. If something fails, see
[troubleshooting](troubleshooting.md).

## Step 3: Run the migration

```sh
importer migrate
```

It goes through four stages. Along the way it also looks for log metric filters, EventBridge rules
and similar configuration (needs the optional permissions in [permissions](permissions.md)) and
prints what it found:

1. It names the AWS account you're signed in to and asks you to confirm it. Pass
   `--aws-account-id 123456789012` to skip the question, which a script has to do.
2. It reads that account (read-only) and writes the plan and report to a dated folder,
   `out/migration-<date>-<time>/`.
3. It opens `report.html` in your browser and prints the total completion. The report opens with a
   Preview banner, because nothing exists in New Relic yet.
4. It asks whether to create everything in New Relic. Press Enter to stop with nothing created, or
   answer `y` to create it (step 5).

Other options: `--region eu-west-1` for another region, `--profile name` for a named AWS profile,
`--nr-account-id 1234567` instead of `NEW_RELIC_ACCOUNT_ID`, `--out folder` to choose the folder,
and `--no-open` to keep the report from opening. A run covers one region.

The folder holds:

| File | What it is |
|---|---|
| `report.html` | Open this first. What migrated, what didn't, why, and the fix |
| `plan.md` | The same plan as plain text |
| `export/` | What was read from AWS. It contains real names, so keep it private |
| `alerts/`, `dashboards/`, `notifications/` | The converted objects (NerdGraph JSON and Terraform) |
| `ingest/` | Terraform that streams metrics into New Relic (step 6) |
| `manifest.json` | Written when something is created. It lets `rollback` undo exactly that |

## Step 4: Read the report

The report opens by itself. To open it again, use the `report.html` file in the run folder.

- The banner at the top says where you are. Preview means nothing exists in New Relic yet, so every
  number is a forecast, and the banner shows the command that creates the objects. Applied means the
  objects were created and the numbers describe what exists. Anything New Relic refused counts as
  not migrated.
- Total completion is migrated assets divided by all assets (alarms, dashboards and notification
  subscriptions). The bar and table show the split per type.
- "What was not migrated, and how to fix it" lists every miss grouped by problem, with manual
  steps. Open an asset to see why it failed.
- "Migrated, but read these first" lists the caveats on items that did convert. "Migrated, review"
  doesn't mean perfect, so read the notes before you enable anything.

What each status means is in [what migrates](what-migrates.md).

## Step 5: Create it in New Relic

Answer `y` at the end of step 3 once the report looks right. Use a test account first. The question
only appears if `NEW_RELIC_API_KEY` is set and the account is on `NEW_RELIC_ALLOWED_ACCOUNT_IDS`;
otherwise the run tells you which one is missing and stops with nothing created.

Answering `y` creates alert conditions and notification workflows disabled and dashboards private,
and records everything it made in `manifest.json`. Review them in New Relic, then enable them one
at a time. Existing objects with the same name are skipped and never changed. The report is
rewritten as Applied.

In a script there is nobody to ask, so pass `--yes` to create without the question (or
`--apply --confirm-account 1234567` to name the account explicitly). Without either, a script only
makes the plan.

If New Relic refuses an item, the run carries on with the rest, lists the failures at the end and
exits with an error. Fix the cause and run the same command again. Only the failed items are retried.

To undo the latest run:

```sh
importer rollback
```

It lists what would be deleted and asks before deleting. For an older run, pass
`--manifest out/migration-<date>-<time>/manifest.json`. Dashboards can keep showing in New Relic
search for a while after they're deleted.

## Step 6: Stream the metrics

Converted conditions return nothing until the metrics reach New Relic. `migrate` writes the
Terraform for AWS Metric Streams to `out/migration-<date>-<time>/ingest/`. Running it needs Terraform and your
New Relic license key, so the importer generates it but doesn't run it:

```sh
cd out/migration-<date>-<time>/ingest
export TF_VAR_newrelic_license_key=...      # your New Relic license (ingest) key
export TF_VAR_newrelic_account_id=1234567
export TF_VAR_newrelic_api_key=$NEW_RELIC_API_KEY
terraform init && terraform plan            # read it, then: terraform apply
```

If the run also wrote `logs-ingest/`, your plan has alarms that count log events. Deploy that
folder the same way (`terraform init && terraform plan`, then apply) so the log groups reach New
Relic. Those conditions return nothing until the logs arrive.

Don't run New Relic's AWS polling integration and Metric Streams for the same metrics, or the
totals double.

## Step 7: Check the numbers match

After data has flowed for a while, compare each converted query with CloudWatch's own numbers:

```sh
importer validate --alarms out/migration-<date>-<time>/export/alarms --alerts out/migration-<date>-<time>/alerts \
    --account-id 123456789012 --nr-account-id 1234567 --since 2026-01-01T00:00:00Z
```

Set `--since` to the time ingestion started. Each mismatch is listed with a likely cause.

## Other commands

| Command | What it does |
|---|---|
| `importer report out/migration-<date>-<time>` | Rebuilds `report.html` from an existing run |
| `importer stats out/migration-<date>-<time>/alerts` | Prints a summary without names, safe to share |
| `importer extract-cloudwatch`, `convert`, `convert-dashboards` and others | The individual steps behind `migrate` |

Other platforms have the same shape: `extract-<platform>`, then `convert --source <platform>`, then
`report <folder> --source <platform>`. Offline checks of the sign-in settings:
`importer doctor --source cloudwatch|gcp|dynatrace|dynatrace-classic|grafana`.

| Platform | Commands |
|---|---|
| Google Cloud | `extract-gcp`, `convert --source gcp`, `gcp-link-terraform`, `convert-gcp-notifications`, `convert-gcp-uptime`, `check-gcp-data` |
| Dynatrace (workflows, SLOs) | `extract-dynatrace`, `convert --source dynatrace`, `convert-notifications --source dynatrace`, `plan-service-levels`, `check-dynatrace-data` |
| Dynatrace (classic alerting) | `extract-dynatrace-classic`, `convert --source dynatrace-classic`, `check-dynatrace-metrics`, `summarize-dynatrace-classic`, `validate-dynatrace-map` |
| Dynatrace (dashboards, synthetics, inventory) | `extract-dynatrace-inventory`, `plan-dynatrace-dashboards`, `plan-dynatrace-synthetics`, `plan-dynatrace-reinstrumentation` |
| Grafana | `extract-grafana`, `convert --source grafana`, `extract-grafana-dashboards`, `plan-grafana-dashboards`, `extract-grafana-notifications`, `plan-grafana-datapath`, `check-grafana-data` |

See [what migrates](what-migrates.md#other-platforms) for what each one converts.

### Notifications

`convert-notifications` plans destinations and (disabled) workflows; `apply-notifications` creates
them, as a dry run unless you add `--apply --confirm-account`.

- A plan marked `needs_review` (Dynatrace, or an SNS topic with a Lambda, SQS or Firehose
  subscription) is skipped and listed. Check it, then add `--include-needs-review`.
- A destination that takes a secret (a PagerDuty key, a token, a password) reads it from the
  environment variable that has the name of the secret in the plan, for example `PD_KEY`. The value
  is never printed. A missing variable stops the run before anything is written.
- `convert-notifications` prints problems across the plans, such as too many workflows or two
  plans for one topic. `--warn-https-payload` adds a note to https plans: New Relic sends its own
  payload, not the SNS envelope. `--muting-handling` sets what workflows do for muted issues.
- `convert --incident-preference PER_POLICY|PER_CONDITION|PER_CONDITION_AND_TARGET` writes the
  preference on the Terraform policy. Without it nothing is written, as before.

Run `importer --help` for the full list.
