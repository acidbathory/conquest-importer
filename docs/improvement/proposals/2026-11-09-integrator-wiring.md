# Proposal: integrator wiring

Branch `improve/integrator-wiring`, from `integration/all-threads` (tip 8d1c9ac). Nothing was run
against a cloud, New Relic or Terraform. No golden file, baseline or emitter hash changed.

## What changed

| Area | Change | Tests |
|---|---|---|
| `apply-notifications` | Passes `--include-needs-review` to core; plans needing review are skipped and listed. Prints `plan_violations` and `skipped_review`. Reads secrets from environment variables named like the `SecretRef` (only for plans that will be created; values never printed; a missing one stops the run before the first write). Help text now covers CloudWatch plans with Lambda, SQS or Firehose. | `tests/test_cli_wiring.py` |
| `convert-notifications` | Prints the violations from `notifications_terraform_result`; `--warn-https-payload` (default off) reaches `translate_topic`, and the note appears in `report.md`; `--muting-handling` builds an `EmitDefaults`. | same |
| `convert` | `--incident-preference` builds an `EmitDefaults` (checked against `INCIDENT_PREFERENCES`). Defaults write the same bytes. | same, and the existing hash tests |
| `core/emit/common.py` | `emit_defaults_replaced(**changes)` context manager; `SOURCE_LABELS` and `RESOURCE_PREFIXES` entries for `dynatrace-classic` (`dtc`). `core/emit/notifications.py`: `secret_names` is now public. | `tests/test_source_profiles.py` |
| `core/report_html.py` | `PROFILES["dynatrace-classic"]`. Every name in `REGISTRY` now has a profile. | `tests/test_source_profiles.py` |
| `core/doctor.py` | `DOCTOR_SOURCES`; `doctor --source` accepts `dynatrace-classic` (platform or classic tenant address) and `grafana` (Cloud address, or a self-hosted address with a hint to use `--allow-host`). Offline; the token is never shown. | `tests/test_doctor_sources.py` |
| `core/data_check.py` | `run_data_check` and a `DataCheckSource` Protocol replace four copies (GCP and Dynatrace in `cli.py`, `commands/dynatrace_classic.py`, `commands/grafana_notifications.py`). One body, one error list. | `tests/test_data_check_shared.py` and the existing four command tests |
| `core/dynatrace_env.py` (new) | `PLATFORM_HOST`, `CLASSIC_HOST`, `environment_url`, `is_platform_url`, `is_plain_token` and `env_token`. The three Dynatrace extractors import them and keep their public names; messages are unchanged. | `tests/test_dynatrace_env.py` |
| Source tests | `tests/test_sources.py` and `tests/test_improve.py` take the source list from the registry and the fixture tables, with a required core of `cloudwatch` and `gcp`. | themselves |
| Docs | README, `docs/getting-started.md`, `docs/dev/parallel-work.md` (ownership rows for the Grafana streams, the integrator and the self-registering sub-packages). | |

## Behaviour changes to know about

- `apply-notifications --apply` no longer exits 1 when a plan needs review and the flag is missing.
  It creates the other plans and lists the skipped ones, as the new core does. One existing test
  (`tests/test_dynatrace_round2.py::test_a_plan_that_needs_review_...`) described the old exit and
  was rewritten to say so.
- The shared data check now also treats a negative `--nr-account-id` as missing, and the Google and
  Dynatrace commands now report a missing `NEW_RELIC_API_KEY` as `Error: ...` (exit 1) instead of
  an uncaught exception.
- `convert-notifications` and `convert` run inside `emit_defaults_replaced`, which swaps a module
  global for the duration of the command. It is restored on exit, including on error. Not thread
  safe; the CLI is single threaded.

## Could not wire, and why

- `migrate` still reads CloudWatch only. It has no `--source`; the request to make it accept all
  sources would need an extract step per source in `migrate`. `report` accepts every source now.
- `SOURCE_LABELS` and `RESOURCE_PREFIXES` for `dynatrace` and `grafana`: adding them changes the
  description text and resource names in `convert/dynatrace` and `convert/grafana`, which the hash
  test pins. They keep the fallback (the source name, prefix `src`). A person decides whether to
  take the flip and regenerate the hashes. `dynatrace-classic` is not pinned, so it has its entries.
- `--incident-preference` is on `convert` only and `--muting-handling` on `convert-notifications`
  only: each setting has no effect on the other command, so a no-op option would mislead.
- `convert-muting` (nr-emitters request 1): no source produces `MutingRule` yet.
- `PageBudget` is copied in the Google and Dynatrace inventory extractors. They differ, so I left
  them. Third use would justify a shared one.
- Not touched, as instructed: `core/safe_http.py` and JSON loading (the `decode_json`
  `RecursionError` request, the `want` parameter for lists), `core/report.md_cell` (bidi and
  zero-width characters), `core/emit/nrql.py` (`Num` precision, `identifier` `fullmatch`),
  `--metric-map` on `convert`, `core/dashboard_ir.py` requests, the baseline decision for dashboard
  plans. Those remain open.
- Checked but not changed: the Dynatrace profile still says classic alerting profiles are not read.
  That is true of the `dynatrace` source; the classic source has its own profile.

## Honest notes on method

- Test first was kept for the CLI wiring (the first run failed on the review flag with the old
  exit), the doctor, the profile and the data check. I slipped on `core/dynatrace_env.py`: I wrote
  the module before its test, then wrote the test, which failed only on the identity of the
  re-exported names (`ENV_URL is PLATFORM_HOST`) until the extractors were edited. The message
  tests are characterisation tests and passed at once.
- `tests/test_cli_wiring.py` uses a spy for `apply_notifications` and the client. It shows what the
  CLI passes, not that New Relic accepts it; the core behaviour is tested in `tests/test_nr_apply.py`.
- Wording of the `dynatrace-classic` profile follows the classic proposal; nothing in it was
  checked on a tenant.

## Result

`rm -rf .mypy_cache; sh scripts/improve/gate.sh` printed GATE PASSED: 4695 passed, 24 skipped.
Measure unchanged.
