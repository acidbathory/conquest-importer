# Datadog and Azure: what is not built

There is no runbook for Datadog or Azure because there is nothing to run. This page says so, so that
nobody plans a migration around a feature that does not exist.

State described here: the `integration/all-threads` branch, 2026-11-10.

## Datadog: paused

- Datadog support was built and then removed on 2026-11-05. Its source, fixtures, scripts and tests
  were deleted.
- It is paused until AWS, Google Cloud and Azure are covered.
- The importer has no Datadog command. `importer --help` lists none, and `--source datadog` is not a
  valid source for `convert`, `doctor` or `report`.
- The sources that exist are `cloudwatch`, `gcp`, `grafana`, `dynatrace` and `dynatrace-classic`.
- Datadog was also blocked on the project's own corporate network (the API host is blocked by a
  gateway). The fix would be an IT allow-list. Bypassing the block is not an option.

What to do instead today: nothing in this tool. Export your monitors and dashboards from Datadog and
plan the move by hand, or wait. The shared pieces (the intermediate format, the New Relic emitters, the
guarded `apply`, rollback) would be reused when Datadog returns.

## Azure: not started

- No Azure Monitor code, fixtures or research exist yet beyond a note in the roadmap that the New Relic
  Azure integration is the data route to study (streaming against polling).
- The importer has no Azure command and no `--source azure`.
- Azure comes next after AWS and Google Cloud in the plan, with the same shape: read alerts, convert,
  check the data, create disabled.

What to do instead today: nothing in this tool. For Azure metrics, New Relic's own Azure integration
is outside this project. A Dynatrace dashboard plan may name Azure metrics and their New Relic
counterparts. That is a plan, not an Azure migration.

## What you can do now

| Your source | Guide |
|---|---|
| AWS CloudWatch | [runbook-aws-cloudwatch.md](runbook-aws-cloudwatch.md) |
| Google Cloud | [runbook-gcp.md](runbook-gcp.md) |
| Grafana | [runbook-grafana.md](runbook-grafana.md) |
| Dynatrace | [runbook-dynatrace.md](runbook-dynatrace.md) |

See the [roadmap section of the README](../../README.md#roadmap) for the planned order. Dates are not promised.
