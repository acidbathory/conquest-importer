# Sources of the GCP dashboard samples

Public sample dashboards from https://github.com/GoogleCloudPlatform/monitoring-dashboard-samples
(branch `master`, licence Apache-2.0), fetched on 2026-11-11 with `curl` over HTTPS and kept unchanged.
One dashboard per product folder, 24 files; the file name is `<folder>__<file>`. The list of raw URLs
is in `docs/research/gcp-alerting-verified.md`, section 5.2.

They are used by `tests/test_gcp_dashboard_widgets.py` to measure how many widgets convert.
