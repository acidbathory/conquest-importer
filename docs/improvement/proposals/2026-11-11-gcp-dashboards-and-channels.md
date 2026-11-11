# Proposal: GCP dashboard widgets and notification channels

Branch `improve/gcp-dash-channels`. Pinned outputs that change, for a human to review.

## Pinned output that changes

`tests/fixtures/dashboard-output-hashes.json`, the four `gcp/` entries for the "Ops overview"
fixture (`gcp-dashboards`): `dashboard-ir/58944063589550.json`, `nerdgraph/58944063589550.json`,
`dashboards.tf`, `report.md`. Nothing else differs (the cloudwatch entries and the generated set
are untouched).

Why, in plain English: the fixture holds a logs panel ("Errors") and an alert chart ("Open
alerts"). Before, both were "dashboard widget type is not supported". Now the logs panel becomes a
table over `Log` (so it is a real widget instead of a placeholder), and the alert chart keeps a
placeholder with a more precise reason (no New Relic equivalent, with steps). The dashboard's
reason list and widget count lines change with them.

I did not regenerate the hashes. The GCP case of `test_every_output_file_is_byte_identical...` is
marked `xfail(strict=True)` so the gate passes while the diff waits for review; once you approve
the new output, regenerate the four hashes and remove the marker (the strict marker will fail the
suite if you forget).

## What to look at in the new output

- The logs panel table query: `SELECT timestamp, message FROM Log WHERE resource.type = 'gce_instance' LIMIT 200`
  (attribute names of forwarded Google Cloud logs are not confirmed; the widget says so).
- The alert chart placeholder text.

## Second pinned output: GCP notification plans

`tests/fixtures/emitter-output-hashes.json`, group `notifications/gcp` (the plan files
`notification-plans/set-*.json` and `report.md` of `tests/fixtures/gcp-notifications`).

Why: every known channel now adds a `manual_destinations` entry to its plan (service, the closest
New Relic destination type, the steps, and the logical name of the secret Google hides), and the plan
is marked `needs_review`. The report text for `sms`, `pubsub` and `campfire` channels changes from the
generic "channel type has no New Relic destination" to a specific reason (the generic text is kept for
unknown types). No secret value is read or written: only names such as `GCP_PAGERDUTY_SERVICE_KEY`.
The same strict `xfail` marker is on `test_output_bytes_are_unchanged[notifications/gcp]`.

## Numbers

Dashboards, 24 public sample dashboards (`tests/fixtures/gcp-dash`), 350 content widgets (layout
markers excluded; a placeholder never counts as converted): 16 converted before, 115 after.
Notification channel types in Google's descriptor list (10): 5 with a specific reason before, 10 after.
