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

## Azure: now built

Azure Monitor alert rules have their own page: [runbook-azure.md](runbook-azure.md).

## What you can do now

| Your source | Guide |
|---|---|
| AWS CloudWatch | [runbook-aws-cloudwatch.md](runbook-aws-cloudwatch.md) |
| Google Cloud | [runbook-gcp.md](runbook-gcp.md) |
| Grafana | [runbook-grafana.md](runbook-grafana.md) |
| Dynatrace | [runbook-dynatrace.md](runbook-dynatrace.md) |

See the [roadmap section of the README](../../README.md#roadmap) for the planned order. Dates are not promised.
