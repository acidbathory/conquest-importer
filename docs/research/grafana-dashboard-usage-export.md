# Grafana dashboard usage data for `triage-grafana-dashboards --usage`

Checked 2026-11-10 against public Grafana docs. Nothing here was run against a stack.

## What the importer reads
A JSON or CSV file, one row per dashboard. Columns (case-insensitive, aliases accepted):
`uid` (or `dashboard_uid`), `views_30d` (or `views`), `last_viewed` (ISO date or datetime).
JSON may be a list of rows or `{"dashboards": [...]}`. Rows without a uid or a readable count are ignored.

## Where Grafana keeps the numbers
| Source | Status | Notes |
|---|---|---|
| Usage Insights dashboards in Grafana Cloud ("Usage Insights - 1 - Overview" and others) | Verified in docs (grafana.com/docs/grafana-cloud/account-management/usage-insights/) and present in the sandbox extract | Only on select Cloud plans. Open the Overview dashboard, set the time range to 30 days, use a table panel's Inspect > Data > Download CSV, then rename columns to the ones above. Exact panel and column names UNVERIFIED (not opened) |
| Usage Insights Loki data source `grafanacloud-<stack>-usage-insights` | Verified in docs | Holds the usage events as logs; query dashboard-view events for 30 days and export counts per dashboard uid. The event field names are UNVERIFIED |
| "Sort dashboards by insights data" in dashboard search (Grafana Enterprise / Cloud) | Verified in docs (grafana.com/docs/grafana/latest/dashboards/assess-dashboard-usage/) | UI only; shows most viewed ordering, no counts to export |
| `/api/usage-insights` or any REST endpoint returning per-dashboard view counts | NOT FOUND in public docs; treat as UNVERIFIED and do not assume it exists | The HTTP API dashboard page lists no such endpoint |

The open-source Grafana does not record per-dashboard views at all; there the only data is whatever
a reverse proxy or the Grafana server log kept (`GET /d/<uid>/...` requests).
