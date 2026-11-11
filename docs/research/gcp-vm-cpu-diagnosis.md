# Why VM CPU utilization does not reach New Relic from Google Cloud

Diagnosis written 2026-11-11. Read-only: NerdGraph NRQL and NerdGraph `cloud` queries against the
owner's New Relic sandbox, plus New Relic and Google public documentation. Nothing was read from or
changed in Google Cloud, and nothing was changed in New Relic. Status: the cause is NOT confirmed.
What follows is evidence, a ranked list of causes, and how to tell them apart.

## 1. Evidence from New Relic

Queried 2026-11-11 over `SINCE 30 days ago`.

- The test link no longer exists. `actor.account.cloud.linkedAccounts` lists only an AWS account. The
  Google data is a closed set: 72,730 `Metric` rows, first 2026-11-04, last 2026-11-07.
- Event types: no `Gcp*Sample` type exists (no `GcpVirtualMachineSample`). All Google data is
  dimensional `Metric` data with `instrumentation.provider = 'gcp'` and `collector.name =
  'gcp-cloud-monitoring'`. No `IntegrationError`, `IntegrationProviderReport` or
  `IntegrationDataFreshnessReport` row exists for provider gcp (the AWS ones are there), so New Relic
  exposed no error to us for the Google link.
- Names: 60 `gcp.*` metric names. By resource type: `pubsub_subscription` 18, `pubsub_topic` 20,
  `gce_instance` 15, `gcs_bucket` 9, `compute.googleapis.com/VpcNetwork` 6.
- The 15 Compute Engine VM and disk names that arrived (all with data for the whole window):
  `cpu.reserved_cores`, `disk.read_bytes_count`, `disk.read_ops_count`, `disk.write_bytes_count`,
  `disk.write_ops_count`, `disk.max_read_bytes_count`, `disk.max_read_ops_count`,
  `disk.max_write_bytes_count`, `disk.max_write_ops_count`, `disk.average_io_latency`,
  `disk.average_io_queue_depth`, `disk.provisioning.size`, `disk.performance_status`, and the firewall
  `dropped_bytes_count` and `dropped_packets_count`. Plus quota metrics on the VPC network.
- Searched for the CPU metric under any name: `metricName LIKE '%cpu%' OR '%uptime%' OR '%utiliz%'`
  over all metrics. The only `gcp.*` hit is `gcp.compute.instance.cpu.reserved_cores`. The rest are
  AWS, APM, Prometheus and Kubernetes names. There is no `cpuUtilization`, no alternate spelling, no
  `Gcp*` event, no `GcpVirtualMachineSample`. So this is not a naming difference.
- Also absent, and not recorded before: `gcp.compute.instance.network.received_bytes_count`,
  `sent_bytes_count`, `received_packets_count`, `sent_packets_count` (all in New Relic's mapping
  table), and `gcp.compute.instance.memory.balloon.ram_used` (in New Relic's compute page). Together
  with `cpu.utilization`, `cpu.usage_time` and `uptime` the missing group is: every hypervisor-reported
  per-VM metric except `cpu.reserved_cores`. `disk.throttled_*` is also absent, but Google only emits
  those when a disk is throttled, so that is expected.
- Attributes on what did arrive (the CPU metric would carry the same): `gcp.projectId`,
  `gcp.projectName`, `gcp.projectNumber`, `gcp.zone`, `gcp.region`, `gcp.instance_id`, `gcp.id`,
  `metric.instance_name`, `entity.name` (`ckg-vm`), `entity.type` `HOST`, `entity.guid`,
  `fullResourceName` (`//compute.googleapis.com/projects/<p>/zones/<z>/instances/<id>`),
  `gcp.resource.type` `gce_instance`, `gcp.labels.<key>`, `gcp.machineType`, `gcp.status`,
  `gcp.cpuPlatform`, `providerAccountName`. Metric points are `gauge` or `summary` (count, sum, min,
  max). Two instance ids appear (the two test runs); the last point is 2026-11-07 about 14:00 UTC.
- Timing: first data about 4 minutes after linking; a point per 5-minute poll.

## 2. What the documentation says

- New Relic, Compute Engine page: "All Compute Engine metrics available in GCP Cloud Monitoring are
  collected as dimensional metrics ... Additional metrics beyond this table are collected
  automatically." Its key-metrics table for VM Instance lists `gcp.compute.instance.cpu.utilization`,
  `cpu.usage_time`, `memory.balloon.ram_used`, `network.received_bytes_count`, `network.sent_bytes_count`
  and `uptime`. So the metric is documented as collected, and our result contradicts the page.
- New Relic polls Cloud Monitoring (about 5 minutes; 300 seconds is the minimum for Compute Engine;
  60 seconds is a Limited Preview only for AlloyDB, BigQuery, Dataflow, Dataproc, GKE, load balancing,
  Managed Kafka, Pub/Sub and Spanner). The only setting per service in the Terraform provider is
  `metrics_polling_interval`. There is no metric filter or service-level option that could drop one
  metric.
- Required APIs for the Workload Identity Federation link: IAM, Security Token Service, Service Account
  Credentials, Cloud Monitoring (Stackdriver Monitoring), Cloud Asset and Cloud Resource Manager.
  Required roles: `roles/viewer`, `roles/serviceusage.serviceUsageConsumer`, `roles/cloudasset.viewer`
  (+ `roles/iam.workloadIdentityUser` on the service account for the pool). Compute Viewer and
  Monitoring Viewer are covered by `roles/viewer`; a custom role is the only case where per-service
  permissions matter. A permission error stops New Relic retrying for one hour.
- New Relic troubleshooting page: missing metrics for one service are blamed on the service not being
  enabled, the service not generating metrics, or custom IAM roles. Stale data is blamed on the Cloud
  Monitoring quota "Time series queries per minute". None of these produces a partial result on one
  service while the same service's disk metrics arrive.
- Google, Compute Engine metrics list: `instance/cpu/utilization` (GA, GAUGE DOUBLE, `gce_instance`),
  `instance/cpu/usage_time` (DELTA DOUBLE), `instance/network/*` (DELTA INT64) and `instance/disk/*`
  (DELTA INT64) are all sampled every 60 seconds and "not visible for up to 240 seconds". CPU, network
  and uptime are "reported by the hypervisor". Same resource type and same label `instance_name` as the
  disk metrics that did arrive. Nothing in the metric definitions separates the two groups.
- Legacy naming: New Relic's metric table pairs `gcp.compute.instance.cpu.utilization` with the old
  event attribute `instance.cpu.Utilization` (event `GcpVirtualMachineSample`). That is the only
  alternate name that exists. It belongs to the old polling integration, which we do not use and which
  is not present in this account.

## 3. Most likely causes, ranked

1. New Relic collector behaviour for hypervisor-reported VM metrics (a defect, a per-metric filter, or
   a limit on our link we cannot see). Likelihood: high. For: it reproduced on two separate links
   (different pool, different VM, 40+ minutes and more than an hour); disk, firewall, quota and
   `reserved_cores` from the same VM, service account, API and poll arrived, so auth, roles, API
   enablement, polling and quota are all working; the missing set is the same group both times; New
   Relic documents these as collected. Against: no New Relic error row to point at.
   To confirm: open a New Relic support case with the evidence in section 1 (account, linked account
   name `conquest-test-<project>`, the time window, the missing names). Ask directly whether
   `instance/cpu/utilization`, `cpu/usage_time`, `uptime` and `network/*` are filtered out for the
   dimensional (WIF) integration, and whether the old `GcpVirtualMachineSample` integration is the
   expected route for them. A positive answer from them ends the question.
2. Ingestion-delay window miss. Likelihood: low. Google says these points appear up to 240 seconds
   after sampling. If the collector queries a window that closes too early, a metric with a long delay
   could be missed forever. For: CPU is the metric type Google itself explains as delayed. Against: disk
   metrics have the same 240-second text and arrived, and a miss of that kind would show some points,
   not zero in two runs. To confirm: none we can run alone; ask New Relic, or compare the Cloud
   Monitoring API result for a window that ended 10 minutes ago against the same window in NRQL on a
   long-lived VM.
3. The VM was too small or too short-lived (e2-micro, destroyed after a few hours). Likelihood: low.
   `cpu.reserved_cores` for the same VM arrived, so the VM was discovered. To confirm: link again with
   a different machine type (for example e2-small), leave it for an hour, and run
   `check-gcp-data`. Costs a few cents.
4. A permission gap in the service account. Likelihood: very low. `roles/viewer` includes
   `monitoring.timeSeries.list`, which is per project, not per metric. A gap would hide every metric,
   not seven. To confirm: in Google Cloud, Cloud Audit Logs and the Monitoring API dashboard
   (APIs & Services, Cloud Monitoring API, Metrics, split by method and response code) for 403 or 429
   on `ListTimeSeries` from `newrelic-gcp-integration@<project>`. A clean dashboard rules this out.
5. A quota limit ("Time series queries per minute"). Likelihood: very low on a one-VM project. To
   confirm: same dashboard, look for 429.
6. A wrong or different name. Ruled out: no CPU-like `gcp.*` name, no `Gcp*` event type exists.

## 4. What our generated Terraform and runbook do

`src/importer/sources/gcp/link.tf.tmpl` and `link_terraform.py` generate: the seven APIs (IAM,
IAM credentials, STS, Resource Manager, Cloud Asset, Service Usage, Monitoring), the three roles above,
the pool and OIDC provider, the link, and one block per service in `newrelic_cloud_gcp_dm_integrations`.
`compute.googleapis.com` maps to `virtual_machines { metrics_polling_interval = 300 }`, exactly the
form in the provider documentation, and the live test (`scripts/live-test/gcp-link/main.tf`) used the
same block. So compute IS in the service list and the settings are the documented ones. There is no
other option in the provider to change. Conclusion: no change to the generated Terraform can fix this.
Proposed changes are documentation only: say so in the runbook, and give the owner the support route
and a workaround (below). We did not change the Terraform.

## 5. Converter behaviour

- No name-map entry. The metric does not arrive under another name, so there is nothing to map. A
  map entry for `GcpVirtualMachineSample.instance.cpu.Utilization` would point conditions at data this
  link never produces.
- The converter already marks `cpu.utilization`, `cpu.usage_time` and `uptime` as "never arrived"
  (`translate.py`, `gcp_verified.json` `documented_not_received`), and `check-gcp-data` exits with 2 when
  the metric is missing. Keep both. The network and `memory.balloon` names should be added to
  `documented_not_received` only after a run that shows Google has them for the VM (we did not check
  Google, so we do not claim it).
- If New Relic confirms a route, then update the table: either they fix the collector (nothing to
  map) or they name another metric (then add a name-map entry, test first).

## 6. Workarounds for a customer today

- Install the New Relic infrastructure agent on the VM and alert on `SystemSample.cpuPercent`. This
  needs a change inside the VM and is a different data source: UNVERIFIED here.
- Alert on a Compute Engine metric that does arrive and tracks load (for example
  `gcp.compute.instance.disk.write_ops_count`) only if it makes sense for that workload.
- Keep the Google alert policy in Google until a route is confirmed.

## 7. Steps for the owner (nothing here is required to be done in the Google project now)

1. Open the New Relic support case described under cause 1, attach section 1.
2. Optional, read-only in Google Cloud: after the next test link, look at APIs & Services, Cloud
   Monitoring API, Metrics, for errors by method. This rules out causes 4 and 5.
3. Optional, costs a few cents: a second test with a larger VM (cause 3), then run
   `importer check-gcp-data` and a query by `entity.type = 'HOST'`.
4. Do not enable the legacy service-account integration as a fix: it needs a key, which this project
   does not create.
