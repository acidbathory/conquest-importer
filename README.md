# Conquest Importer (prototype)

Converts observability configuration from other platforms into New Relic: **AWS CloudWatch alarms,
dashboards and SNS notifications** today (Datadog alerts exist but are only tested on synthetic
input). Grafana, Dynatrace, GCP and Azure are not started.

**Status: prototype for feedback.** Every converted item is labelled exact, approximate, manual or
unsupported, with a reason. Nothing is created in New Relic unless you pass `--apply` plus an
account confirmation, and everything it creates is disabled or private. We want your failures.

## Try it (read-only, writes only local files)
Needs [uv](https://docs.astral.sh/uv/) and the AWS CLI signed in to the account you want to read
(`ReadOnlyAccess` is enough). Terraform is optional.

```sh
uv sync
uv run importer doctor                                     # checks tools and AWS sign-in
uv run importer extract-cloudwatch --account-id <12-digit id> --region <region> --out out/export
uv run importer convert --source cloudwatch --in out/export/alarms --out out/cw
uv run importer stats out/cw --out out/stats.md            # the summary to send back
```
One region per run. The command stops before reading if the signed-in account is not `--account-id`.
For the one-command version (read, convert, plan; no writes) see `importer migrate --help`.

## The migration report
`importer migrate` writes `report.html` next to its plan (also standalone:
`importer report out/migration`). One self-contained page, no network needed: what migrated and
what did not (counts and percentages per asset type), and for every unmigrated asset the reason
and the manual steps to fix it, grouped by problem. It contains real names: review before sharing.

## What to send back
| Send | Contains | Notes |
|---|---|---|
| `out/stats.md` | conversion rate and failure reasons | Names, ARNs and account ids are stripped (a test enforces it). Safe to share. |
| Any `Error:` text from the CLI | the error message | Check it for account ids first. |
| `out/export/`, `out/cw/report.md` | **real alarm names, ARNs, account ids, email addresses** | Do **not** share without reviewing and sanitizing. |

Never send credentials or `.env`.

Please also tell us: how many alarms you expected vs what was found, which failure reasons surprised
you, and anything you would have expected to convert that did not.

## Known gaps
Not converted: composite and anomaly-detection alarms, some metric-math syntax, periods under 60s,
statistics other than Sum/Average/Min/Max/pNN. Not handled: logs, traces, SLOs, synthetics, non-SNS
alarm actions. Metric names are verified against real data only for DynamoDB, Lambda, Kinesis, SQS,
SNS, API Gateway, EC2 (CPU) and ALB; for other services a converted query may return nothing, so run
`importer validate` after data is flowing. Full list: `HANDOFF.md`.

## For contributors
`AGENTS.md` and `CLAUDE.md` hold the rules; `docs/dev/` has linting, DRY and testing guidance;
`HANDOFF.md` has the current state; `ROADMAP.md` the plan.
