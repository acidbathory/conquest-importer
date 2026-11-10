# Report branding: what changes in the pinned outputs

Every report now says where it came from. The header comes from one function,
`importer.core.report_brand.header_lines(title, source)`, so it can be changed or switched off in one
place. Nothing else in any report changed. Hashes were NOT regenerated; a person must review and pin.

## Before and after (Grafana conversion report)

Before:

    # Conversion report

    Monitors read: **12**. ...

After:

    # Conversion report: Grafana to New Relic

    **source: Grafana** → **target: New Relic**

    Monitors read: **12**. ...

Pairs: Grafana, Datadog, Dynatrace (also classic), AWS CloudWatch, Google Cloud Monitoring, each "to
New Relic". The badge uses bold text only (no remote images, no backticks).

## Pinned hashes that change (header lines only)

- `tests/fixtures/emitter-output-hashes.json`, file `report.md` in the groups `convert/cloudwatch`,
  `convert/cloudwatch-real`, `convert/gcp`, `convert/dynatrace`, `convert/grafana`,
  `notifications/aws`, `notifications/dynatrace`, `notifications/gcp`.
- The dashboard output hashes used by `tests/test_dashboard_ir_byte_identity.py`: `cloudwatch/report.md`
  and `gcp/report.md`.
- The AWS notification hash checked by `tests/test_dynatrace_notifications.py`
  (`test_the_aws_notification_output_is_byte_identical`): `report.md`.

No `.tf`, JSON, IR or NerdGraph file changes.

## Test edit

`tests/test_grafana_dashboards_hostile.py` pinned the exact heading of the Grafana coverage plan; it
now expects `# Grafana dashboards: coverage plan: Grafana to New Relic`.

## HTML report (not hashed)

Page title and heading read "Grafana to New Relic migration report" (and so on), with inline SVG marks
and an arrow under the masthead. See `docs/brand/README.md`.

## For the remediation branch

`header_lines("Remediation plan", source)` returns the heading line, a blank line and the badge line;
add the usual blank line after it. `core/remediation*` was not touched.
