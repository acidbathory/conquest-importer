# HANDOFF: start here

Written 2026-10-02 so any person or AI assistant can continue without the chat history.
Read this, then `CLAUDE.md` (rules), `ROADMAP.md` (plan), `docs/verification-log.md` (what is proven).

## WHERE WE STOPPED (2026-10-05) - read this first
- All work is on branch `wip/cloudwatch-complete-2026-10-05` (not pushed). The CloudWatch golden diff was
  approved on 2026-11-04 and the suite is fully green (386 tests).
- Nothing is running in AWS or New Relic (verified). `.env` holds the New Relic license key.
- Open question for the owner, not for code: is the tool worth continuing without full coverage?
  Agreed next step (slowly): measure a sanitized real-customer export with `importer migrate` in plan
  mode (reads AWS, writes local files only) and ask the manager what failed before and what success
  means. Pause new features until that number exists.
- Gaps and their reasons (not built / needs verification / real mismatch / deliberate) are listed under
  "Known CloudWatch gaps" below and in the 2026-10-05 verification-log section.

## What this project is
"Conquest Importer": a prototype that migrates observability configuration from other platforms
into New Relic. The owner is a non-coder architect at New Relic who wants to understand the
system, so keep designs reviewable, explain decisions, and never "vibe code". Inspired by nri-flex.
Private repo: github.com/acidbathory/conquest-importer (branch `main`, all work committed).

Pipeline: source export -> typed source models -> **IR** (vendor-neutral) -> emitters
(NerdGraph JSON, Terraform) + gap report -> optional guarded `apply` to a New Relic account.

## State at handoff
| Area | State |
|---|---|
| CloudWatch alarms -> conditions | Built. 23 synthetic + 9 real-export alarms. Percentiles, anomaly, metric-math functions, composites not converted |
| Datadog monitors -> conditions | Built on **synthetic** input only (API blocked by corporate Cloudflare Gateway) |
| CloudWatch extract from an account | Built and run against a real account (`extract-cloudwatch`, also reads SNS subscriptions) |
| `migrate` (one go), `validate`, `rollback` | Built and run live in the sandbox. `migrate` does read, convert, plan, and guarded create; metric ingestion is generated as Terraform for a human to deploy (it holds a secret) |
| Dashboards -> New Relic | `apply-dashboards` verified live (create, read back, rollback) |
| Notifications (SNS -> workflows) | Email and https migrate to destinations/channels/DISABLED workflows (verified live with placeholder addresses); other protocols are reported |
| Percentile alarms | Converted (max/min of the per-minute `<metric>.pNN` gauge); needs `ingest-setup --percentile`; confirmed on API Gateway only |
| Ingestion setup (AWS Metric Streams Terraform) | Built; deployed and verified with real data on 2026-10-02, then destroyed |
| Guarded `apply` to New Relic | Built; verified in the sandbox |
| Dashboards: CloudWatch | Built offline on **synthetic** input (`convert-dashboards`); Terraform validates; not created in New Relic. Other sources not started |
| Grafana, Dynatrace, GCP, Azure | **Not started** |
| Tests | 386 (385 pass; 1 golden test waits for the owner to review a diff, see below) (ruff, mypy --strict, pytest, pip-audit, terraform validate) |

Conversion rates so far (synthetic data, so optimistic): Datadog 16/20 on the original sample set;
CloudWatch 15/23 synthetic, 6/9 real-export. Do not present these as customer results.

## How to run things
```
uv sync                                   # install (Python 3.12 via uv)
uv run pytest -q                          # all tests (needs terraform for 3 validate tests)
uv run ruff check . && uv run mypy src tests scripts
uv run importer --help

# Datadog (fixtures): convert monitors already saved as one JSON file each
uv run importer convert --in tests/fixtures/datadog --out out/converted
# CloudWatch, straight from an AWS account (READ-ONLY, via the AWS CLI and its configured profile).
# Stops before reading if the signed-in account is not --account-id. Review the export before sharing.
uv run importer extract-cloudwatch --account-id 123456789012 --region us-east-1 --out out/cloudwatch
# ...then convert out/cloudwatch/alarms and out/cloudwatch/dashboards as below
# CloudWatch: input is the output of `aws cloudwatch describe-alarms --output json`
uv run importer convert --source cloudwatch --in tests/fixtures/cloudwatch-real --out out/cw
# Review out/<dir>/report.md, out/<dir>/main.tf, out/<dir>/nerdgraph/*.json

# Conversion rates and most common failure reasons across one or more convert outputs
uv run importer stats out/cw out/converted            # add --out out/stats.md to save it

# Dashboards (CloudWatch): input is `aws cloudwatch get-dashboard` output; use a separate --out
uv run importer convert-dashboards --in tests/fixtures/cloudwatch-dashboards --out out/dash

# Create in New Relic (dry run by default; writing needs all three guards)
uv run importer apply --in out/cw --account-id <id>
# ONE GO (plan only by default; reads AWS, writes local files). With --apply it creates everything
# in New Relic (disabled/private) and writes a manifest. See out/migration/plan.md afterwards.
uv run importer migrate --aws-account-id 123456789012 --nr-account-id 1234567
#   ... --apply --confirm-account 1234567        # creates; needs NEW_RELIC_ALLOWED_ACCOUNT_IDS
uv run importer validate --alarms out/migration/export/alarms --alerts out/migration/alerts \
    --account-id 123456789012 --nr-account-id 1234567 --since 2026-10-05T04:24:00Z   # side by side
uv run importer rollback --manifest out/migration/manifest.json --account-id 1234567  # dry run; add --apply --confirm-account
# Individual steps: extract-cloudwatch, convert, convert-dashboards, convert-notifications, apply,
# apply-dashboards, apply-notifications, ingest-setup [--percentile NS:METRIC:p99], stats

# Ingestion setup (writes Terraform only; nothing deployed)
uv run importer ingest-setup --alarms tests/fixtures/cloudwatch-real --out out/ingest
```
Golden files: `UPDATE_GOLDEN=1 uv run pytest tests/test_fixtures.py tests/test_cloudwatch.py -k golden`,
then **review the diff by hand** before committing.

## Repo map
- `src/importer/core/` shared by every source: `ir.py` (the IR), `report.py`, `dashboard_ir.py`, `dashboard_report.py`, `stats.py`, `text.py`, `apply.py` (the only
  code that writes to New Relic), `secrets.py`, `tls.py`, and `emit/` (`nrql.py` is the only place
  NRQL is built; also `nerdgraph.py`, `terraform.py`, `common.py`)
- `src/importer/sources/<name>/` one folder per source platform (`datadog/`, `cloudwatch/`): models,
  translator (never raises, labels `manual`/`unsupported`), mapping JSON (`metric_map.json`,
  `cloudwatch_map.json`). `sources/base.py` defines the interface: `load(path)` then
  `translate(raw)`; each source registers itself in `REGISTRY`, and the CLI looks sources up there.
  CloudWatch's `ingest.py` (Metric Streams Terraform) lives in its folder; Datadog's API `client.py`
  in its.
- `src/importer/core/doctor.py` preflight for `importer doctor`
- `src/importer/core/migration_summary.py` + `report_html.py`: the post-migration HTML report (`importer report`, also written by `migrate`); `remediation.py`: manual steps per kind of failure (a test fails when an unconverted fixture reason has none); `sources/cloudwatch/composite.py`: parses composite-alarm rules and writes specific rebuild steps
- `scripts/` fixture generators and the Datadog sample-monitor creator (writes to Datadog)
- `tests/fixtures/` synthetic and real-sanitized inputs; `tests/golden/` expected results
- `docs/` IR spec, threat model, verification log, decisions

## Rules you must follow (from the owner)
1. **Never add Claude/AI as author, co-author or contributor** (no `Co-Authored-By`, no "Generated
   with" lines, anywhere). Check: `git log --format=%B | grep -ci co-authored` must print 0.
2. **In the New Relic sandbox and the test AWS account: only create new things and remove your own.
   Never modify or delete pre-existing entities.** Check for name clashes with a correct read-only
   query first (an empty-name search returns nothing, so list all and compare).
3. **Never bypass network controls.** `api.datadoghq.com` is blocked by the company's Cloudflare
   Gateway; the fix is an IT allow-list, not a workaround.
4. **Never print, log, commit or paste secret values.** Keys live in `.env` (git-ignored). Load them
   into the environment without echoing. Owner pasted keys in chat earlier; see security to-dos.
5. **Authorizations were specific to the moment they were given.** Deploying infrastructure
   (Terraform apply, AWS resources) and creating cloud resources were approved for the 2026-10-02
   tests only. Ask again before any new deployment or any write to AWS/Datadog/New Relic.
6. Keep everything disabled-by-default, dry-run-by-default, and labelled honestly.
7. Commit and push only when asked; the repo is private under the owner's personal GitHub account
   (ask the owner before moving it to a New Relic org or making it public).

## Environment facts
- macOS; `uv` for Python; Terraform installed via `brew install hashicorp/tap/terraform`; AWS CLI
  configured with a `default` profile for the test AWS account (region us-east-1).
- The company network re-signs TLS; code uses the OS trust store (`truststore`) so verification
  stays on. There is no `timeout` command on macOS; use in-script loops.
- Secrets locations (names only): `.env` here has `DD_API_KEY`, `DD_APP_KEY`, `DD_SITE`,
  `NEW_RELIC_API_KEY` (a user key copied from the AI Routing demo project), `NEW_RELIC_ACCOUNT_ID`,
  `NEW_RELIC_ALLOWED_ACCOUNT_IDS`. The ingest (license) key needed for Firehose lives only in the
  AI Routing demo project's `.env` and was read for the test only. `.env.example` lists the names.
- Git identity for commits is set locally to the owner's GitHub login and noreply address.

## Verified vs assumed (details in `docs/verification-log.md`)
- Verified with real systems: New Relic condition creation via NerdGraph and Terraform (no drift,
  idempotent), AWS `describe-alarms` parsing, Metric Streams ingestion end to end, metric naming
  `aws.<service>.<Metric>` + dimension attribute `aws.<service>.<Dimension>` for **DynamoDB**.
- Assumed: naming for every other AWS service; all Datadog metric mappings
  (`sources/datadog/metric_map.json`); Datadog API behavior (never seen live).

## Open to-dos
Security / hygiene (owner):
- [ ] Rotate the Datadog API and application keys (they were pasted into a chat)
- [ ] Replace the New Relic key copied from the other project with one limited to alert management
- [ ] Ask the manager: what failed in the earlier migration attempt, and the target
- [ ] Get Datadog API access allow-listed (or an approved non-corporate environment)

Engineering (see `ROADMAP.md` for the plan and order):
- [x] Refactor into `core/` + `sources/<name>/` with a common source interface (done 2026-10-03;
      output verified byte-identical to before on all fixtures)
- [x] `importer stats`: aggregate conversion rates and failure reasons across runs (`core/stats.py`;
      reasons are grouped by mechanical pattern, and also by category)
- [x] Failure taxonomy for alerts (`core/taxonomy.py`: ordered keyword rules; a test fails if a
      fixture reason is uncategorised). Dashboard-specific categories (visualization, plugin) come
      with dashboards
- [x] Side-by-side validation for CloudWatch (`importer validate`, verified live). Datadog/others
      still need live access to the source
- [ ] Usage triage (skip unused dashboards/alerts): BLOCKED, needs usage data (dashboard views,
      alarm history) that the exports do not contain
- [x] CloudWatch percentiles: verified on API Gateway 2026-10-05 (see the log). Earlier note: Metric Streams send count/sum/min/max
      unless "additional statistics" are configured, and how New Relic stores them is unverified.
      Needs a stream with a percentile configured plus a query of the real data (asks for
      approval: deploys to AWS). Do not guess a mapping; changing the reason text also needs a
      human-reviewed golden regeneration
- [~] CloudWatch naming verified live: DynamoDB, Lambda (per function), Kinesis, SQS, SNS, API Gateway,
      EC2 (CPU), ALB (fixed-response only). STILL TO VERIFY: RDS, S3, ECS, ElastiCache, EBS, NLB, classic ELB,
      other Lambda dimension sets, and the dimension lists in `cloudwatch_dimensions.json`
- [x] CloudWatch dashboards, offline (synthetic fixtures). TODO: `apply` for dashboards (writes to
      New Relic; needs owner approval), real exports, `stats` support for dashboard runs,
      dashboard-specific taxonomy categories (visualization, plugin)
- [ ] Grafana dashboards, then GCP/Azure alerts, then Datadog on real data
- [ ] Security hardening ideas: narrower IAM policy than `ReadOnlyAccess` for the link role;
      remote encrypted Terraform state guidance for real customers (state holds the ingest key)

## Live test resources: ALL REMOVED (2026-10-05, again after the second round)
Nothing created by this project is running. The Terraform for both AWS test stacks is kept
(`scripts/live-test/aws`, `scripts/live-test/services`); `importer ingest-setup` regenerates the
ingestion stack. The pre-existing `conti-*` resources and `conti-nr-metricstream` were never touched.
`NEW_RELIC_LICENSE_KEY` is in the git-ignored `.env` (copied by the owner); remove it when done.

## Known CloudWatch gaps (what "migrate everything in one go" still does NOT do)
- Logs, X-Ray traces, SLOs, synthetics, EventBridge: not handled at all (metrics, alarms, dashboards only)
- Not converted: anomaly-detection alarms, composite alarms, metric-math functions, statistics
  other than Sum/Average/Min/Max/pNN, high-resolution (< 60s) periods, log/alarm/explorer widgets
- Alarm actions other than SNS (EC2/ASG/SSM actions) are not migrated; SNS protocols other than
  email and https are reported, not migrated
- Metric names are only verified for the services listed above; everything else is assumed and can
  return nothing silently: run `validate` after ingestion, always
- One region and one account per run; the ingestion Terraform is deployed by a human
- Duplicate data: if New Relic's polling AWS integration and Metric Streams both run, totals double;
  `validate` detects it, nothing prevents it
- Existing objects are skipped, never updated (re-running after a converter fix means rolling back
  and re-applying)

## Lessons from building (so you do not repeat them)
- CloudWatch publishes a metric once per dimension set and New Relic stores all sets under one
  metric name: always pin the dimension set (`IS NULL` for the other known dimensions).
- A delete call that does not raise may not have deleted: re-read. An id filter that is typed `ID`
  rejects a `String` variable. Read the full GraphQL error (the client truncates at 300 characters;
  use a probe call that fails validation before it can create anything).
- The live test found what offline tests could not: metric names depend on the dimension set
  (Lambda `.byFunction`), and the converted query ran without error but returned 0. Always compare a
  converted query with the source platform's own numbers, side by side.
- Tests on mocks hid real bugs: the live sandbox found that New Relic strips control characters
  from condition names (duplicate on re-run) and that the provider rejects `value_function`.
  Always verify against the real service and read results back.
- Review golden output by eye before blessing it; one review caught a metadata field wrongly
  counted as an unmigrated setting.
- A Python class and a field with the same name (`MetricStat`) clash in pydantic models.
- An empty-name policy search returns zero results; it is not "no policies".
- Read-only checks before any write; verify ownership (names/ids) before any delete; verify the
  final state afterwards.
