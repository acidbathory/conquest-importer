# GCP: native observability tools and what New Relic can receive

Research for the GCP source. Evidence level is marked on each claim:
**[docs]** read in the vendor's published documentation, **[unverified]** written from memory and
not yet checked. Live tests ran on 2026-11-05 and 2026-11-06; the results are in
[gcp-metric-names-verified.md](gcp-metric-names-verified.md). Claims below not marked as corrected
were written before those tests.

## How GCP data reaches New Relic

- **Metrics are polled, not streamed [docs].** New Relic reads the Cloud Monitoring API every
  5 minutes (1 minute in a limited preview for a few services). There is no equivalent of AWS
  Metric Streams and no documented Pub/Sub, OpenTelemetry or Prometheus path for GCP service
  metrics.
- **Three link methods [docs].** Workload Identity Federation (recommended, all GA and Beta metrics,
  dimensional `Metric` data only), a New Relic-managed service account, and a Google user account
  (the last two give a fixed set of metrics and the older `GcpXxxSample` events).
- **Cost [docs].** Google bills the Cloud Monitoring API calls to your own project. Workload
  Identity Federation ingest is typically higher than the service-account method.
- **Latency [docs].** First data 2-5 minutes after linking. After that Cloud Monitoring can delay
  some metrics by 2-3 minutes and New Relic adds 1-2 minutes.
- **Terraform [docs].** `newrelic_cloud_gcp_link_account` (with `use_workload_identity_federation`,
  `audience`, `service_account_email`), `newrelic_cloud_gcp_dm_integrations` (one block per service,
  `metrics_polling_interval` of 300 seconds or more), and on the Google side a service account,
  IAM bindings and a workload identity pool and provider (issuer `https://oidc.newrelic.com/r/gcp-cmp`).

## Metric names in NRQL [docs]

Dimensional names follow `gcp.<service>.<resource>.<metric>` in snake_case, for example
`compute.googleapis.com/instance/cpu/utilization` becomes `gcp.compute.instance.cpu.utilization`.
No suffix is added (confirmed on live data: Google's own counter names already end in `_count`). The service segment does not always match
Google's prefix (GKE is `gcp.kubernetes.io.*`, Cloud Run is `gcp.run.*`, Cloud Storage is
`gcp.storage.*`), so the converter must use New Relic's mapping table, not a rule.

Not documented, so to be confirmed on real data **[unverified]**: the dimension names for project,
zone and instance; names for Memorystore; whether custom (`custom.googleapis.com`), log-based
(`logging.googleapis.com/user`) and Managed Prometheus metrics arrive at all. Treat those as
unsupported until a test shows otherwise.

## Logs and traces

- **Logs [docs].** A Cloud Logging sink sends to Pub/Sub. From there either a push subscription to
  New Relic's headerless ingest URL (low volume) or the `PubsubToNewRelic` Dataflow template
  (higher volume, extra GCP cost). New Relic documents no Terraform for it; we would write it.
- **Traces.** No Cloud Trace ingest exists. The route is to re-instrument with OpenTelemetry.

## Google-native objects to read

All read through `monitoring.googleapis.com` and `logging.googleapis.com`; `roles/monitoring.viewer`
and a logging config viewer role cover reads **[unverified]**.

| Object | Notes | Maps to |
|---|---|---|
| Alert policies | Condition kinds: threshold, absence, log-match, MQL, PromQL, SQL, forecast; combiner AND/OR; `severity`; notification rate limit **[docs]** | NRQL condition; log-match to a `Log` query; absence to loss of signal |
| MQL | Ended in the console but existing MQL policies still work through the API **[docs]** | Needs its own translator |
| Notification channels | Secrets (tokens, passwords) are write-only, so they must be re-entered **[unverified]** | Destinations and channels |
| Dashboards (v1 API) | Mosaic or grid layout; xyChart, scorecard, text, logs panel, alert widgets **[unverified]** | Dashboard pages |
| Uptime checks | HTTP/TCP, regions, content matchers; passwords not returned **[unverified]** | Synthetics monitors |
| SLOs | Calendar periods have no New Relic equivalent **[unverified]** | Service levels |
| Log sinks, exclusions, log-based metrics | **[unverified]** | Log forwarding Terraform, drop rules, `Log` queries |
| Managed Prometheus, Ops Agent, custom metrics | Re-instrumentation, not conversion | Inventory only |

## Conversion gotchas to handle **[unverified]**

- Cumulative and delta metrics need rate or delta aligners; counters arrive as one change per 5 minute poll, and no suffix is added to the name.
- Many GCP utilisation metrics are fractions (0 to 1), not percent, so thresholds must be rescaled.
- Distribution metrics (latency) need percentile aligners; New Relic needs histogram data.
- Alignment periods are 60 seconds or more; evaluation lag differs from New Relic's windows.
- Cross-series reducers and `groupByFields` become `FACET`.

## Suggested order

1. Read-only scanner. Built: `extract-gcp` reads alert policies, notification channels and uptime
   checks. Dashboards, log sinks and log-based metrics are not read yet.
2. A verified metric-name table, checked against live New Relic data the way
   [the AWS note](metric-names-verified.md) was. Done for Compute Engine and Pub/Sub
   (`gcp_verified.json`).
3. Translators (built for threshold and log-match conditions), the link Terraform
   (`gcp-link-terraform`, built) and live verification (partly done). Still open: other services,
   dashboards, channels, uptime checks and SLOs.

Sources read: New Relic docs-website (`infrastructure/google-cloud-platform-integrations/*`,
`logs/forward-logs/google-cloud-platform-log-forwarding.mdx`), the New Relic Terraform provider
docs, and Google Cloud Monitoring API reference pages (alert policy condition types, MQL status).
