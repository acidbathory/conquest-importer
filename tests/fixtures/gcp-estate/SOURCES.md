# GCP test estate: sources and licences

A corpus for measuring how much of a Google Cloud Monitoring estate the importer converts. It is
read by `scripts/analysis/gcp_estate_measure.py`; the numbers are in
`docs/research/gcp-estate-baseline.md`. Nothing here came from a customer or from a live Google
Cloud project. `manifest.json` lists every file with its origin and licence.

## What is in it

| Folder | Files | Origin |
|---|---|---|
| `policies/public/` | 276 alert policies | PUBLIC, one repository (below) |
| `policies/synthetic/` | 71 alert policies | SYNTHETIC, modelled on the AlertPolicy API reference |
| `dashboards/public/` | 96 dashboards | PUBLIC, one repository (below) |
| `dashboards/synthetic/` | 13 dashboards | SYNTHETIC, modelled on the Dashboard API reference |
| `channels/channels.json` | 20 notification channels | SYNTHETIC |
| `uptime-checks/uptime-checks.json` | 12 uptime checks | SYNTHETIC |

## Public source and licence

- Repository: `GoogleCloudPlatform/monitoring-dashboard-samples`, branch `master`
  (git tree `2ef05daf347a39a9c9afe9accba464799a06b637`, fetched 2026-11-11 over HTTPS from
  `raw.githubusercontent.com`).
- Licence: Apache License 2.0 (the repository's `LICENSE` file). The files are copied unchanged
  except for the edits below, which keeps the licence's requirement to state changes.
- `alerts/<integration>/*.json` became `policies/public/<integration>__<file>.json` (all 276 files).
- `dashboards/<integration>/*.json`: 258 dashboards exist; one per integration folder was kept, the
  largest one that is at most 12,000 bytes when compact (96 files). That keeps the corpus small
  and is a size filter, not a quality filter.
- Edits: keys sorted and indented (formatting only); three personal sandbox project ids in the
  upstream files were replaced with `example-project`. No email addresses, tokens or keys were
  found in the files (searched for addresses, `AIza`, `xox`, `ghp_`, `BEGIN` blocks).
- The public policies are templates: they have no `name`, no notification channels, and some carry
  `${PROJECT_ID}`-style placeholders. The measuring script gives them resource names the way the
  API would (the converter keys conditions by name) and nothing else.

Other public collections were looked for (terraform-google-modules, professional-services, the
Cloud Ops sandbox, community repositories). The GitHub tree API refused further unauthenticated
requests (rate limit) after the first repository and no raw paths were guessed, so this corpus
has ONE public source. Add more files under `policies/public/` or `dashboards/public/` and a row
to `manifest.json`; the script reads whatever is in the folders.

## Synthetic files

Marked `"origin": "SYNTHETIC"` in the manifest and kept under `synthetic/` folders or in the
synthetic-only files above. They exist because the public samples have no ratio condition with a
real denominator beyond one, no forecast, no SLO burn rate, almost no MQL, no AND policies, no
channels and no uptime checks. Shapes covered by synthetic files:

- Policies: threshold on verified Compute Engine and Pub/Sub metrics, counters with `ALIGN_RATE`
  and `ALIGN_DELTA`, percentile aligners, ratio with `denominatorFilter`, absence, matched log
  (severity, text, JSON payload, regex, OR/NOT), MQL (six shapes), PromQL (five shapes), SQL,
  forecast, uptime-check alerts, SLO burn rate and budget, AND / OR / AND_WITH_MATCHING_RESOURCE
  with several conditions, auto-close, notification rate limit, severity, documentation, disabled
  policies, filter functions (`one_of`, `starts_with`, `monitoring.regex.full_match`, OR groups),
  second-stage aggregation, trigger by count and percent, custom / agent / Prometheus /
  log-based metrics, odd periods and durations, and a condition kind the importer has not seen.
- Dashboards: grid, column and row layouts; MQL, PromQL and ratio queries; alert chart, logs
  panel, pie chart, time series table, incident list, section header, groups, error reporting;
  scorecard views and thresholds; secondary aggregation; non-Google-service metrics; dashboard
  filters; an empty dashboard.
- Channels: email, Slack, PagerDuty, webhook (token and basic auth), Pub/Sub, SMS, mobile, plus the
  retired types Campfire and HipChat. Every address, number, token, key and host is a placeholder
  (`EMAIL_PLACEHOLDER`, `TOKEN_PLACEHOLDER`, `example.com`). Project id is `example-project`.
- Uptime checks: HTTP (GET, POST with auth and body, headers, content matchers, regex), TCP,
  group, synthetic monitor, private checkers, periods from 30s to one hour. Hosts, credentials
  and headers are placeholders.

A synthetic file shows that a shape is handled or not; it says nothing about how common the shape
is in real estates. The headline percentages in the baseline note are therefore reported for the
whole corpus and for the public part separately.
