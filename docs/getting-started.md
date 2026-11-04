# Getting started

A walkthrough from install to rollback. It takes about ten minutes, and nothing is created in New
Relic until Step 5.

You need Python 3.12+, [read-only AWS keys](permissions.md), and (from Step 5) a New Relic API key.

## Step 1: Install

```sh
git clone https://github.com/acidbathory/conquest-importer.git
cd conquest-importer
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install .
```

## Step 2: Give it your AWS keys and check

```sh
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
export AWS_SESSION_TOKEN=...     # only for temporary keys
importer doctor
```

You want `ok` next to *python* and *aws keys*. `warn` lines for New Relic and Terraform are fine
for now. If something fails, see [troubleshooting](troubleshooting.md).

## Step 3: Make a plan (reads AWS, writes local files only)

```sh
importer migrate --aws-account-id 123456789012 --nr-account-id 1234567
```

- `--aws-account-id` is the account you expect. The tool stops before reading anything if your keys
  belong to a different one.
- `--nr-account-id` is the New Relic account the plan is for.
- Add `--region eu-west-1` for another region, `--profile name` to use a named AWS profile.
  One region per run.

It prints your **total completion** and writes everything to `out/migration/`:

| File | What it is |
|---|---|
| `report.html` | **Start here.** What migrated, what did not, why, and the fix |
| `plan.md` | The same plan as plain text |
| `export/` | What was read from AWS. Contains real names: keep it private |
| `alerts/`, `dashboards/`, `notifications/` | The converted objects (NerdGraph JSON and Terraform) |
| `ingest/` | Terraform that streams metrics into New Relic (Step 6) |

## Step 4: Read the report

Open `out/migration/report.html` in a browser.

1. **Total completion** is migrated assets divided by all assets (alarms, dashboards and
   notification subscriptions). The bar and table show the split per type.
2. **What was not migrated, and how to fix it** lists every miss grouped by problem, each with
   manual steps. Open an asset to see why it failed.
3. **Migrated, but read these first** lists the caveats on things that did convert. "Migrated,
   review" is not "perfect": read the notes before you enable anything.

What each status means is in [what migrates](what-migrates.md).

## Step 5: Create it in New Relic

Only when you are happy with the plan. Use a **test account first**.

```sh
export NEW_RELIC_API_KEY=NRAK-...
export NEW_RELIC_ALLOWED_ACCOUNT_IDS=1234567
importer migrate --aws-account-id 123456789012 --nr-account-id 1234567 \
    --out out/migration-apply --apply --confirm-account 1234567
```

This creates alert conditions and notification workflows **disabled** and dashboards **private**,
and records everything it made in `manifest.json`. Review them in New Relic, then enable them one
at a time. Existing objects with the same name are skipped, never changed.

**To undo exactly what a run created:**

```sh
importer rollback --manifest out/migration-apply/manifest.json --account-id 1234567            # preview
importer rollback --manifest out/migration-apply/manifest.json --account-id 1234567 \
    --apply --confirm-account 1234567                                                          # do it
```

## Step 6: Stream the metrics

Converted conditions return nothing until the metrics are in New Relic. `migrate` writes the
Terraform for AWS Metric Streams to `out/migration/ingest/`. It needs Terraform and your New Relic
**license key**, which is why the importer generates it but does not run it:

```sh
cd out/migration/ingest
export TF_VAR_newrelic_license_key=...      # your New Relic license (ingest) key
export TF_VAR_newrelic_account_id=1234567
export TF_VAR_newrelic_api_key=$NEW_RELIC_API_KEY
terraform init && terraform plan            # read it, then: terraform apply
```

Do not run New Relic's AWS polling integration and Metric Streams for the same metrics: the
totals double.

## Step 7: Check the numbers match

Once data has flowed for a while, compare each converted query with CloudWatch's own numbers:

```sh
importer validate --alarms out/migration/export/alarms --alerts out/migration/alerts \
    --account-id 123456789012 --nr-account-id 1234567 --since 2026-01-01T00:00:00Z
```

Use the time ingestion started for `--since`. Any mismatch is listed with a likely cause.

## Other commands

| Command | Does |
|---|---|
| `importer report out/migration` | Rebuild `report.html` from an existing run |
| `importer stats out/migration/alerts` | Name-free summary, safe to share |
| `importer extract-cloudwatch`, `convert`, `convert-dashboards`, … | The individual steps behind `migrate` |

Run `importer --help` for all of them.
