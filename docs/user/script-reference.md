# Script and command reference

Two tables. The first lists every script under `scripts/`. The second lists every `importer`
command, grouped by source. Both were checked against the repository and `importer --help` on
2026-11-10.

How to read the "Safe?" column:

- Safe: reads local files, or reads a source read-only. Changes nothing outside `out/`.
- Asks: can create things, but only after you type `yes` or pass explicit flags.
- Creates cloud resources: costs money or changes an account. Developer and sandbox use only.

Use `uv run --active importer ...`, or activate `.venv` and type `importer ...`.

## Scripts under `scripts/`

| Script | What it does | Who runs it | Reads | Writes | Safe? |
|---|---|---|---|---|---|
| `scripts/migrate.sh` | Loads `.env`, then opens `importer menu`. Sets `SSL_CERT_FILE=/tmp/q/ca.pem` only if that file exists. | Practitioner. This is the one-command start. | `.env`, then whatever the menu reads | `out/guided/...` and, after you type `yes`, New Relic objects | Asks |
| `scripts/improve/gate.sh` | The quality gate: ruff, format check, mypy strict, vulture, pytest, `improve-measure`, and a check that golden files are untouched. | Contributor | repository | `/tmp/improve-measure.json` | Safe |
| `scripts/improve/overlap.sh` | Lists files changed by more than one branch, before merging parallel work. | Contributor | git history | prints only | Safe |
| `scripts/generate_cloudwatch_fixtures.py` | Writes synthetic CloudWatch alarm fixtures. | Contributor | nothing | `tests/fixtures/cloudwatch` | Safe. Regenerating fixtures can change golden tests: a human must review. |
| `scripts/generate_cloudwatch_dashboard_fixtures.py` | Writes synthetic CloudWatch dashboard fixtures. | Contributor | nothing | `tests/fixtures/cloudwatch-dashboards` | Safe. Same warning. |
| `scripts/analysis/promql_census.py` | Counts which PromQL constructs Grafana dashboards use and which block conversion. | Contributor | dashboard JSON files | markdown on screen, optional `--json` | Safe. No network. |
| `scripts/analysis/promql_constructs.py` | Library for the census: maps expressions to construct ids. | Contributor (imported, not run) | none | none | Safe |
| `scripts/analysis/promql_verdicts.py` | Library for the census: NRQL verdict per construct. | Contributor (imported, not run) | none | none | Safe |
| `scripts/live-test/suite.py` | Repeated end-to-end test against a real AWS test account and a real New Relic account. Builds resources, runs the importer, checks, tears down. | Contributor, with approval | AWS, New Relic | AWS and New Relic objects, `out/suite/` | Creates cloud resources. Read [testing migration](../testing-migration.md) first. |
| `scripts/live-test/measure_alarms.py` | Creates or deletes a tagged set of `ckr-` CloudWatch alarms in a test account. | Contributor | AWS | AWS alarms | Creates cloud resources (cents) |
| `scripts/live-test/metric_naming.py` | Helpers to compare New Relic metric names with real AWS data (`alarms`, `traffic`, `cleanup`). | Contributor | AWS | `ckv-` alarms, traffic | Creates cloud resources. The stack costs about $0.15 an hour. |
| `scripts/live-test/aws/` (`main.tf`, `handler.py`) | Terraform for the first AWS test stack (DynamoDB, Lambda and others, all prefixed and tagged). | Contributor | none | AWS resources on `terraform apply` | Creates cloud resources. `terraform destroy` removes. |
| `scripts/live-test/services/main.tf` | Terraform for common AWS services used to verify metric names. | Contributor | none | AWS resources | Creates cloud resources |
| `scripts/live-test/metric-naming/main.tf` | Terraform for RDS, ElastiCache, ECS, EBS, NLB, classic ELB and S3. About $0.15 an hour. | Contributor | none | AWS resources | Creates cloud resources. Destroy when done. |
| `scripts/live-test/gcp-link/main.tf` | Terraform that links a Google project to New Relic. | Contributor | none | Google and New Relic link | Creates cloud resources |
| `scripts/live-test/gcp-sample/main.tf` | Terraform for a tiny VM, Pub/Sub and a bucket that emit metrics. | Contributor | none | Google resources | Creates cloud resources. About $0.01 an hour for the VM. |
| `scripts/live-test/dynatrace/test_objects.py` | Creates, reads back and deletes a few inactive `ckg-` objects in a Dynatrace tenant, to get real JSON for fixtures. Subcommands: `plan`, `create`, `delete`. | Contributor | Dynatrace | `out/dynatrace-test/` and Dynatrace objects | Asks. The token needs write scopes: do not use your read-only token. |
| `scripts/live-test/grafana-sandbox/steps/1_publish_to_grafana.sh` | Puts 6 community dashboards into a Grafana folder `ckg-importer-test`. Create-only. | Contributor, sandbox | `out/live-grafana/src` | Grafana dashboards | Creates in Grafana. Sandbox stack only. |
| `scripts/live-test/grafana-sandbox/steps/2_show_in_grafana.sh` | Lists those dashboards with links. | Contributor | Grafana | prints | Safe (read-only) |
| `scripts/live-test/grafana-sandbox/steps/3_extract_and_convert.sh` | Reads the `ckg-` dashboards back and converts them. | Contributor | Grafana | `out/live-run/` | Safe |
| `scripts/live-test/grafana-sandbox/steps/4_preview_new_relic.sh` | Dry run of `apply-dashboards`. Sends nothing. | Contributor | `out/live-run/convert` | prints | Safe |
| `scripts/live-test/grafana-sandbox/steps/5_import_to_new_relic.sh` | Creates private `ckg-` dashboards in New Relic after you type `yes`. | Contributor | `.env` | New Relic dashboards, `manifest.json` | Asks |
| `scripts/live-test/grafana-sandbox/steps/6_undo.sh` | Previews, then removes what the steps created, in New Relic and in Grafana, each after `yes`. | Contributor | manifests | deletes `ckg-` objects only | Asks |
| `scripts/live-test/grafana-sandbox/steps/env.sh` | Sourced by the steps. Loads `.env`, never prints secrets. | Not run directly | `.env` | none | Safe |
| `scripts/live-test/grafana-sandbox/publish_dashboards.py` | The program behind step 1. | Contributor | Grafana env | Grafana dashboards | Creates in Grafana |
| `scripts/live-test/grafana-sandbox/cleanup_dashboards.py` | Deletes exactly the manifest entries. Refuses names without `ckg-`. Without `--yes` it only prints. | Contributor | manifest | deletes Grafana and New Relic dashboards | Asks |
| `scripts/live-test/grafana-sandbox/send_metrics.py` | Copies a short window of Prometheus series from Grafana to the New Relic Metric API. Without `--send` it posts nothing. | Contributor, with approval | Grafana, `.env` | New Relic metric points tagged `ckg_test=true` | Asks. Sends data and uses the license key. |
| `scripts/live-test/grafana-sandbox/synth_counter.py` | Sends a synthetic counter in two forms to the Metric API. Without `--send` it posts nothing. | Contributor | env file | metric points | Asks |
| `scripts/live-test/grafana-sandbox/node_exporter_roundtrip.py` | Scrapes a local node_exporter (127.0.0.1 only) and converts to Metric API points. Dry run without `--send`. | Contributor | localhost | raw file, metric points with `--send` | Asks |
| `scripts/live-test/grafana-sandbox/node_compare.py` | Runs converted NRQL for Node Exporter style PromQL and compares with raw scrapes. | Contributor | New Relic (NRQL), raw file | JSON result | Safe (read-only) |
| `scripts/live-test/grafana-sandbox/promql_baseline.py` | Sends PromQL expression text to New Relic's translate endpoint and compares. Nothing is created. | Contributor, with approval | `.env` | result files | Asks. Sends expression text only. |
| `scripts/live-test/grafana-sandbox/nrcommon.py` | Shared helpers for the round-trip scripts. | Not run directly | env | none | Safe |
| `scripts/live-test/grafana-sandbox/alert-fire/fire.py` | The firing test: `show`, `enable` (the one write, `ckg-` conditions only), `send`, `drive`, `nrql`. | Contributor, with approval | env file, manifest | metric points, enables conditions | Asks |
| `scripts/live-test/grafana-sandbox/alert-fire/rules.json` | The two synthetic Grafana rules used by the firing test. | Data file | none | none | Safe |

A walkthrough of the Grafana dashboard steps is in
[the steps README](../../scripts/live-test/grafana-sandbox/steps/README.md).

## Commands by source

"NR write" means the command can create or delete objects in New Relic. Every create is a dry run
unless you give `--apply` and `--confirm-account <account-id>` and the account is on
`NEW_RELIC_ALLOWED_ACCOUNT_IDS`.

### Shared (any source)

| Command | What it does | Reads | Writes | NR write |
|---|---|---|---|---|
| `importer menu [--no-open]` | Numbered guided menu: GCP, AWS, Grafana, Dynatrace, roll back, check setup, sample, last run. Opens the HTML report before and after the create unless `--no-open`, `IMPORTER_NO_BROWSER=1` or no terminal. | the sources, `.env` | `out/guided/`, run records in `out/runs/` | Yes, after `yes` |
| `importer doctor [--source ...]` | Checks tools and sign-in. | environment (offline except AWS) | nothing | No |
| `importer version` | Prints the tool version. | none | none | No |
| `importer convert --source <s>` | Converts extracted alerts to IR, NerdGraph JSON, Terraform and a report. `--source`: `cloudwatch` (default), `dynatrace`, `dynatrace-classic`, `gcp`, `grafana`. | extract folder | `--out` | No |
| `importer report <run> --source <s>` | Writes `report.html`. | a run folder (reads its `alerts/`) | `report.html` | No |
| `importer stats <runs>...` | Conversion rates and top reasons, no names. | converted folders | optional markdown | No |
| `importer plan-remediation --in <folder>` | Every item that did not convert, grouped by reason, with steps. | converted IR | `remediation-plan.md`, `.csv` | No |
| `importer apply` | Creates converted conditions (disabled). | converted folder | `manifest.json` | Yes with `--apply` |
| `importer apply-dashboards` | Creates converted dashboards (private). | dashboard folder | `manifest.json` | Yes with `--apply` |
| `importer apply-notifications` | Creates destinations, channels, disabled workflows. | plans folder | `manifest.json` | Yes with `--apply` |
| `importer convert-dashboards [--source cloudwatch\|gcp\|grafana]` | Converts exported dashboards. | export folder | `--out` | No |
| `importer convert-notifications [--source cloudwatch\|dynatrace]` | Plans destinations, channels and workflows. | export folder | `--out` | No |
| `importer rollback` | Deletes exactly what a manifest lists. Preview unless `--apply`. | manifest | none | Yes (deletes) |
| `importer runs` | Lists recorded runs, newest first. | `out/runs/` | none | No |
| `importer rollback-run [--real-migration]` | Picks a recorded run, previews, deletes after `yes`. | run record | run record | Yes (deletes) |

### AWS CloudWatch

| Command | What it does | Reads | Writes | NR write |
|---|---|---|---|---|
| `importer extract-cloudwatch` | Reads alarms, dashboards, SNS subscriptions. | AWS (read-only) | `--out` | No |
| `importer migrate` | Read, convert, plan, ask, create, with a manifest. | AWS, New Relic | `out/migration-<time>/` | Yes after `y` / `--yes` |
| `importer ingest-setup` | Terraform for CloudWatch Metric Streams. | alarms JSON | `--out` | No |
| `importer ingest-logs-setup` | Terraform for CloudWatch Logs forwarding (Firehose). | converted alerts | `--out` | No |
| `importer validate` | Compares converted NRQL with CloudWatch's own numbers. | CloudWatch, New Relic | optional markdown | No |
| `importer convert`, `convert-notifications`, `convert-dashboards`, `apply*`, `rollback`, `report`, `stats` | See Shared. | | | |

### Google Cloud

| Command | What it does | Reads | Writes | NR write |
|---|---|---|---|---|
| `importer extract-gcp` | Reads policies, channels, uptime checks, dashboards. | Google Cloud Monitoring | `--out` | No |
| `importer convert --source gcp` | Alert policies to conditions. | extract folder | `--out` | No |
| `importer convert-gcp-notifications` | Notification routing and manual steps. | channels, converted alerts | `--out` | No |
| `importer convert-gcp-uptime` | Lists uptime checks and the monitor to create. | uptime checks | `--out` | No |
| `importer convert-dashboards --source gcp` | Dashboards to widgets. | dashboards | `--out` | No |
| `importer gcp-link-terraform` | Writes Terraform that links the project to New Relic. | converted alerts | `main.tf` | No (a person runs Terraform) |
| `importer check-gcp-data` | Does each converted condition's metric arrive? Exit 2 if not. | New Relic (NRQL) | optional markdown | No |

### Grafana

| Command | What it does | Reads | Writes | NR write |
|---|---|---|---|---|
| `importer extract-grafana` | Reads alert rules. | Grafana (GET) | `--out` | No |
| `importer convert --source grafana` | Rules to conditions. | extract folder | `--out` | No |
| `importer extract-grafana-notifications` | Contact points, policies, mute timings, templates, ruler rules. No secrets. | Grafana (GET) | `--out` | No |
| `importer convert-grafana-notifications` | Plans destinations, workflows, muting rules. | notifications export | `--out` | No |
| `importer extract-grafana-dashboards` | Dashboards, folders, library panels. | Grafana (GET) | `--out` | No |
| `importer plan-grafana-dashboards` | How much of a set of dashboards could convert. | dashboards | `report.md`, `plan.json` | No |
| `importer triage-grafana-dashboards` | Ranks dashboards: migrate now, later, quickstart, skip. | dashboards, optional usage file | report | No |
| `importer plan-grafana-quickstarts` | Which dashboards a New Relic quickstart could replace. | dashboards | plan | No |
| `importer grafana-prom-hints` | Exact metric types and series labels from Prometheus. | Prometheus via Grafana (GET) | hints file | No |
| `importer convert-grafana-dashboards` (or `convert-dashboards --source grafana`) | Dashboards to widgets. | dashboards | `--out` | No |
| `importer plan-grafana-datapath` | Writes remote write, Alloy and collector guidance. Sends nothing. | optional converted folders | guide folder | No |
| `importer check-grafana-data` | Does each converted condition's data arrive? Exit 2 if not. | New Relic (NRQL) | optional markdown | No |

### Dynatrace

| Command | What it does | Reads | Writes | NR write |
|---|---|---|---|---|
| `importer extract-dynatrace` | Reads workflows and SLOs. | Dynatrace (GET) | `--out` | No |
| `importer convert --source dynatrace` | Scheduled log-count workflows to conditions. | extract folder | `--out` | No |
| `importer convert-notifications --source dynatrace` | Plans for Davis problem workflows. All need review. | extract folder | `--out` | No |
| `importer plan-service-levels` | Plans service levels from SLOs. Queries left as TODO. | extract folder | `--out` | No |
| `importer check-dynatrace-data` | Do matching `Log` events arrive? Exit 2 if none. | New Relic (NRQL) | optional markdown | No |
| `importer extract-dynatrace-classic` | Reads metric events, Davis detectors, alerting settings. | Dynatrace (GET) | `--out` | No |
| `importer convert --source dynatrace-classic` | Metric events and detectors to conditions. | classic export | `--out` | No |
| `importer summarize-dynatrace-classic` | Conversion rate, with listed-only rows on their own line. | converted alerts | prints | No |
| `importer validate-dynatrace-map` | Checks your own metric map file. | map file | prints | No |
| `importer check-dynatrace-metrics` | Does the signal behind each classic condition have data? Exit 2 if not. | New Relic (NRQL) | optional markdown | No |
| `importer extract-dynatrace-inventory` | Reads dashboards, synthetic monitors, entity inventory. | Dynatrace (GET) | `--out` | No |
| `importer plan-dynatrace-dashboards` | Which tiles and variables convert. Creates nothing. | inventory | `report.md`, `plan.json` | No |
| `importer plan-dynatrace-synthetics` | Maps monitors to New Relic monitor types. Creates nothing. | inventory | `report.md`, `plan.json` | No |
| `importer plan-dynatrace-reinstrumentation` | Names the New Relic agent for each Dynatrace technology. Installs nothing. | inventory | `report.md`, `plan.json` | No |

### Datadog and Azure

No commands. See [runbook-datadog-and-azure.md](runbook-datadog-and-azure.md).

### Internal

`importer improve-measure` is used by the quality gate and is not listed in `importer --help`.
