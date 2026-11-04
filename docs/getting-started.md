# Getting started

This walks you from install to rollback in about ten minutes. Nothing is created in New Relic
until step 5.

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

## Step 2: Set your AWS keys and run the check

```sh
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...     # only for temporary keys
importer doctor
```

You need `ok` next to python and aws keys. `warn` lines for New Relic and Terraform are fine at
this point. If something fails, see [troubleshooting](troubleshooting.md).

## Step 3: Make a plan

This reads AWS and writes local files only.

```sh
importer migrate --aws-account-id 123456789012 --nr-account-id 1234567
```

- `--aws-account-id` is the account you expect. If your keys belong to a different one, the tool
  stops before reading anything.
- `--nr-account-id` is the New Relic account the plan is for.
- Add `--region eu-west-1` for another region, or `--profile name` to use a named AWS profile. A
  run covers one region.

It prints the total completion, writes everything to `out/migration/`, and finishes by asking
whether to create it in New Relic. Answer `n` for now (or press Enter) so you can read the report
first. Nothing is created in New Relic until you answer `y`, which is step 5.

The files:

| File | What it is |
|---|---|
| `report.html` | Open this first. What migrated, what didn't, why, and the fix |
| `plan.md` | The same plan as plain text |
| `export/` | What was read from AWS. It contains real names, so keep it private |
| `alerts/`, `dashboards/`, `notifications/` | The converted objects (NerdGraph JSON and Terraform) |
| `ingest/` | Terraform that streams metrics into New Relic (step 6) |

## Step 4: Read the report

Open `out/migration/report.html` in a browser.

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

Do this once the plan looks right, and use a test account first. Set the key and the allow-list,
then run `migrate` again into a new folder and answer `y` at the end:

```sh
export NEW_RELIC_API_KEY=NRAK-...
export NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567
importer migrate --aws-account-id 123456789012 --nr-account-id 1234567 --out out/migration-applied
```

In a script there is nobody to ask, so pass `--yes` to create without the question, or
`--apply --confirm-account 1234567` to name the account explicitly. Without either, a script only
makes the plan.

Answering `y` creates alert conditions and notification workflows disabled and dashboards private,
and records everything it made in `manifest.json`. Review them in New Relic, then enable them one
at a time. Existing objects with the same name are skipped and never changed.

If New Relic refuses an item, the run carries on with the rest, lists the failures at the end and
exits with an error. Fix the cause and run the same command again. Only the failed items are retried.

To undo what a run created:

```sh
importer rollback --manifest out/migration-applied/manifest.json --account-id 1234567            # preview
importer rollback --manifest out/migration-applied/manifest.json --account-id 1234567 \
    --apply --confirm-account 1234567                                                            # do it
```

## Step 6: Stream the metrics

Converted conditions return nothing until the metrics reach New Relic. `migrate` writes the
Terraform for AWS Metric Streams to `out/migration/ingest/`. Running it needs Terraform and your
New Relic license key, so the importer generates it but doesn't run it:

```sh
cd out/migration/ingest
export TF_VAR_newrelic_license_key=...      # your New Relic license (ingest) key
export TF_VAR_newrelic_account_id=1234567
export TF_VAR_newrelic_api_key=$NEW_RELIC_API_KEY
terraform init && terraform plan            # read it, then: terraform apply
```

Don't run New Relic's AWS polling integration and Metric Streams for the same metrics, or the
totals double.

## Step 7: Check the numbers match

After data has flowed for a while, compare each converted query with CloudWatch's own numbers:

```sh
importer validate --alarms out/migration/export/alarms --alerts out/migration/alerts \
    --account-id 123456789012 --nr-account-id 1234567 --since 2026-01-01T00:00:00Z
```

Set `--since` to the time ingestion started. Each mismatch is listed with a likely cause.

## Other commands

| Command | What it does |
|---|---|
| `importer report out/migration` | Rebuilds `report.html` from an existing run |
| `importer stats out/migration/alerts` | Prints a summary without names, safe to share |
| `importer extract-cloudwatch`, `convert`, `convert-dashboards` and others | The individual steps behind `migrate` |

Run `importer --help` for the full list.
