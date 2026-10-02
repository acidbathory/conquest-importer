# HANDOFF: start here

Written 2026-10-02 so any person or AI assistant can continue without the chat history.
Read this, then `CLAUDE.md` (rules), `ROADMAP.md` (plan), `docs/verification-log.md` (what is proven).

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
| Ingestion setup (AWS Metric Streams Terraform) | Built; deployed and verified with real data on 2026-10-02, then destroyed |
| Guarded `apply` to New Relic | Built; verified in the sandbox |
| Dashboards (any source) | **Not started** |
| Grafana, Dynatrace, GCP, Azure | **Not started** |
| Tests | 186 pass (ruff, mypy --strict, pytest, pip-audit, terraform validate) |

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
# CloudWatch: input is the output of `aws cloudwatch describe-alarms --output json`
uv run importer convert --source cloudwatch --in tests/fixtures/cloudwatch-real --out out/cw
# Review out/<dir>/report.md, out/<dir>/main.tf, out/<dir>/nerdgraph/*.json

# Create in New Relic (dry run by default; writing needs all three guards)
uv run importer apply --in out/cw --account-id <id>
# Ingestion setup (writes Terraform only; nothing deployed)
uv run importer ingest-setup --alarms tests/fixtures/cloudwatch-real --out out/ingest
```
Golden files: `UPDATE_GOLDEN=1 uv run pytest tests/test_fixtures.py tests/test_cloudwatch.py -k golden`,
then **review the diff by hand** before committing.

## Repo map
- `src/importer/models/` source models (`source.py` Datadog, `cloudwatch.py`) and `ir.py` (the IR)
- `src/importer/translate/` queries + translators + mapping files (`metric_map.json`,
  `cloudwatch_map.json`); the translators never raise, they label `manual`/`unsupported`
- `src/importer/emit/` `nrql.py` (only place NRQL is built), `nerdgraph.py`, `terraform.py`,
  `ingest.py`, `common.py`
- `src/importer/apply.py` the only code that writes to New Relic; `secrets.py`, `tls.py`
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
  (`translate/metric_map.json`); Datadog API behavior (never seen live).

## Open to-dos
Security / hygiene (owner):
- [ ] Rotate the Datadog API and application keys (they were pasted into a chat)
- [ ] Replace the New Relic key copied from the other project with one limited to alert management
- [ ] Ask the manager: what failed in the earlier migration attempt, and the target
- [ ] Get Datadog API access allow-listed (or an approved non-corporate environment)

Engineering (see `ROADMAP.md` for the plan and order):
- [ ] Refactor into `core/` + `sources/<name>/` with a common source interface
- [ ] `importer stats`: aggregate conversion rates and failure reasons across runs
- [ ] Side-by-side validation (run original and converted queries, compare)
- [ ] Usage triage (skip unused dashboards/alerts)
- [ ] CloudWatch: percentiles, verify EC2/RDS/Lambda/ALB naming when resources are active
- [ ] Dashboards (CloudWatch first, then Grafana), then GCP/Azure alerts, then Datadog on real data
- [ ] Security hardening ideas: narrower IAM policy than `ReadOnlyAccess` for the link role;
      remote encrypted Terraform state guidance for real customers (state holds the ingest key)

## Lessons from building (so you do not repeat them)
- Tests on mocks hid real bugs: the live sandbox found that New Relic strips control characters
  from condition names (duplicate on re-run) and that the provider rejects `value_function`.
  Always verify against the real service and read results back.
- Review golden output by eye before blessing it; one review caught a metadata field wrongly
  counted as an unmigrated setting.
- A Python class and a field with the same name (`MetricStat`) clash in pydantic models.
- An empty-name policy search returns zero results; it is not "no policies".
- Read-only checks before any write; verify ownership (names/ids) before any delete; verify the
  final state afterwards.
