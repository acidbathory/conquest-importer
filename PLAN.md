# Conquest Importer: Weeks 1-2 Plan

Goal of the prototype: show that monitors from a Datadog account can be converted automatically into New Relic alert conditions (NerdGraph + Terraform), with an honest gap report for what can't be converted.

Scope: Datadog **monitors only**. Dashboards, SLOs and Dynatrace are out of scope for the 8-week proof.

## 1. Decisions

| Decision | Choice | Why |
|---|---|---|
| Language | Python 3.12+ | Fast iteration, readable tests, strong validation libraries |
| Models | `pydantic` v2 | Strict typed validation at every boundary |
| HTTP | `httpx` | Timeouts and retries are explicit |
| Templating | `jinja2` (HCL), plain dicts (NerdGraph) | HCL is deterministic once the IR is known |
| Tests | `pytest` + golden files | Non-coder-readable: input in, expected output out |
| Tooling | `uv`, `ruff`, `mypy --strict`, `pre-commit` | Catches errors before review |
| CLI | `typer` | Simple commands: `extract`, `translate`, `emit`, `report` |

## 2. Architecture

```
Datadog API -> Extractor -> Source models (raw, validated)
            -> Translator -> IR (vendor-neutral Monitor)
            -> Emitters   -> NerdGraph payloads | Terraform HCL
            -> Reporter   -> Gap report (converted / partial / unsupported + reasons)
```

Rules:
1. Each stage reads and writes files on disk. Any stage can be re-run and inspected on its own.
2. **Dry-run by default.** Nothing is sent to New Relic unless `--apply` is passed.
3. Every translated monitor gets a confidence label: `exact`, `approximate`, `manual`, `unsupported`, with a reason string.
4. Unknown fields are preserved in the IR under `unmapped` and listed in the report, never silently dropped.

Proposed layout:

```
killer-app/
  PLAN.md
  pyproject.toml
  src/importer/
    extract/datadog.py
    models/source.py        # Datadog shapes
    models/ir.py            # vendor-neutral monitor
    translate/monitors.py
    translate/queries.py    # Datadog query -> NRQL (the risky part)
    emit/nerdgraph.py
    emit/terraform.py
    report.py
    cli.py
  tests/
    fixtures/datadog/*.json
    golden/nrql/*.txt
    golden/terraform/*.tf
  docs/
    ir-spec.md
    threat-model.md
    decisions/              # short ADRs
```

## 3. Week 1: setup (you + me)

**You**
- [ ] Get approval from your manager (trial account, synthetic data only, public API only).
- [ ] Create a Datadog trial. Create a **read-only** API key + Application key (scope: monitors_read).
- [ ] Create a New Relic test account/sub-account. Create a user key limited to alert write in that account only.
- [ ] Create the sample monitors below, or ask me for a script that uses the Datadog API to create them.

**Me**
- [ ] Scaffold the repo, tooling and CI.
- [ ] Secrets handling: keys only from env vars (`DD_API_KEY`, `DD_APP_KEY`, `NEW_RELIC_API_KEY`); a redaction filter on logs; `.env` git-ignored.
- [ ] Write `docs/threat-model.md` (see section 6).

## 4. Sample monitors (target: 20 fixtures)

Keep these synthetic. Cover both the easy cases and the hard ones, since the hard ones determine the success rate.

| # | Type | Example | Expected difficulty |
|---|---|---|---|
| 1-4 | Metric threshold | CPU, memory, disk, load above X for 5m | Easy |
| 5-6 | Metric with grouping | avg by host, by service | Easy-medium |
| 7-8 | Metric with aggregation window / `last_5m` variants | min/max/sum/avg | Medium |
| 9-10 | Metric with arithmetic | `a / b * 100` | Medium |
| 11-12 | Log monitor | count of error logs above X | Medium |
| 13 | Service check | host up/down | Medium (maps to loss of signal / heartbeat) |
| 14 | APM / trace analytics | error rate, p95 latency | Medium-hard |
| 15 | Monitor with recovery threshold / warning + critical | two thresholds | Medium |
| 16 | No-data handling | notify on no data | Medium |
| 17 | Composite monitor | A && B | Hard |
| 18 | Anomaly | anomalies() | Hard, likely `manual` |
| 19 | Forecast / outlier | | Hard, likely `unsupported` |
| 20 | Template variables / tags in message | `{{host.name}}`, `@pagerduty-x` | Medium (notification channels can't be migrated 1:1) |

## 5. Week 2: IR spec (deliverable: `docs/ir-spec.md`)

The IR must express at least:
- `name`, `description`, `tags`, `source_id`
- `signal`: {kind: metric | log | apm | heartbeat, query (structured, not a string), aggregation, group_by, window}
- `conditions`: list of {severity: critical | warning, operator, threshold, duration}
- `no_data`: {behavior, timeout}
- `notification`: {message_template, targets[]}
- `confidence` + `confidence_reason`
- `unmapped`: original fields we couldn't place

Open questions to settle in the spec:
1. How do Datadog metric names map to NRQL? (Depends on whether data arrives via the NR infra agent or OTel; start by using a per-metric mapping file.)
2. How are notification channels handled? (Proposal: emit a placeholder and list required channels in the report. We don't migrate secrets.)
3. How are composite monitors represented?

## 6. Threat checklist (expanded in `docs/threat-model.md`)

- [ ] API keys never logged, never written to output, never in fixtures. Redaction test exists.
- [ ] Datadog key is read-only; New Relic key is write-limited to a test account.
- [ ] **Injection:** monitor names, queries and messages are untrusted. Emitters escape for HCL strings and NRQL strings. Tests include hostile inputs (quotes, `${}` interpolation, newlines, heredoc terminators).
- [ ] Generated output is validated: `terraform validate`, and NRQL checked by running it against the test account.
- [ ] `--apply` requires an explicit flag and prints a diff first.
- [ ] HTTP: timeouts, bounded retries with backoff, rate-limit handling, TLS verification on.
- [ ] Dependencies pinned with hashes; `pip-audit` in CI.
- [ ] No real customer data anywhere in the repo.

## 7. Definition of done for weeks 1-2

- Repo runs `extract` against the Datadog trial and saves the 20 monitors as fixtures.
- `docs/ir-spec.md` reviewed and approved by you.
- `docs/threat-model.md` reviewed.
- CI green (lint, types, tests, audit).
- A baseline hand-mapped NRQL for each of the 20 monitors, with a note on which look untranslatable. This gives us the expected success rate before writing the translator.
