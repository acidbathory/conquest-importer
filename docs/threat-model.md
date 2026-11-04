# Threat Model (v0.1, draft for review)

## Assets
- Datadog API/App keys (read-only, trial account)
- New Relic user key (alert-write, test account only)
- Generated alert configuration (incorrect output = missed or noisy alerts in a real environment)

## Trust boundaries
1. **Datadog API responses are untrusted input.** Monitor names, queries, messages and tags are free text authored by anyone with Datadog access.
2. **Generated NRQL/HCL/GraphQL is an output that is executed** by New Relic or Terraform, so it must be treated like code generation.
3. **The local machine and CI logs** can leak secrets if they are not redacted.

## Threats and mitigations

| # | Threat | Mitigation | Verified by |
|---|---|---|---|
| T1 | Secret leaks in logs, errors, output files, fixtures | Keys from env only; `SecretStr`; `RedactingFilter`; `.env` git-ignored; fixtures contain no credentials | `tests/test_secrets.py`; later: a scan over output and fixtures |
| T2 | Injection via monitor text into HCL (`"`, `${...}`, `%{...}`, heredoc terminators, newlines) | Central escape functions in the Terraform emitter; no string concatenation of untrusted text | Hostile-input golden tests (week 7) |
| T3 | Injection into NRQL (quote breaking, extra clauses) | Structured query building from the IR; escape string literals; reject unexpected tokens | Hostile-input tests (week 7) |
| T4 | Injection via GraphQL variables | Use GraphQL variables, never string-format untrusted values into the query document | Code review + tests (week 5) |
| T5 | Unintended writes to a real New Relic account | Dry-run by default (offline); writing needs `--apply`, `--confirm-account`, and the account in `NEW_RELIC_ALLOWED_ACCOUNT_IDS`; plan printed first; conditions created disabled; creates never retried; re-runs skip existing | `tests/test_apply.py` |
| T11 | Terminal escape sequences in monitor names printed by the CLI | `printable()` strips control characters before printing | `tests/test_apply.py` |
| T6 | Over-privileged keys | Read-only Datadog key; NR key scoped to alert write in a test account; documented in `.env.example` | Manual checklist |
| T7 | Silently wrong conversion | Confidence label + reason on every monitor; `unmapped` fields preserved; gap report | Golden tests; report review |
| T8 | Supply-chain compromise | Pinned lockfile (`uv.lock`); `pip-audit` in CI; few dependencies | CI |
| T9 | Denial of service / API abuse | Timeouts, bounded retries with backoff, respect rate limits | Tests with mocked HTTP (week 2) |
| T10 | Sensitive data in monitor messages (e.g. webhook URLs, tokens pasted into messages) | Scan messages for secret-like patterns and flag/redact in reports | Tests (week 6) |

## Out of scope for the prototype
Multi-user auth, hosted service deployment, migration of notification channel credentials (listed in the report as manual steps).

## Open questions
- Does company policy require a security review before running against competitor APIs?
- Where will generated artifacts be stored, and who may read them?
