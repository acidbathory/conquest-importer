# GCP PromQL alert policy samples

Source: https://github.com/GoogleCloudPlatform/monitoring-dashboard-samples (branch `master`, licence
Apache-2.0, copy in that repository's `LICENSE`). Fetched 2026-11-11 over HTTPS from the public
repository archive. These are the 84 of the 276 JSON alert policies under `alerts/` that contain a
`conditionPrometheusQueryLanguage` condition (the count in `docs/research/gcp-alerting-verified.md`).
Files are unmodified; the name is `<product folder>__<file>`. Each is one policy object (not a list),
so tests wrap them with `GcpAlertPolicy.model_validate`. Some queries hold `${NAME}` placeholders
that the sample repository expects a person to fill in; they are kept as they are.

| Local file | Raw URL |
|---|---|
| cassandra__high-error-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/cassandra/high-error-rate.v1.json |
| elasticsearch-gke__high-jvm-memory-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch-gke/high-jvm-memory-usage.v1.json |
| elasticsearch__elasticsearch-high-jvm-memory-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/elasticsearch/elasticsearch-high-jvm-memory-usage.v1.json |
| flink__flink-high-jvm-memory-non-heap-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/flink/flink-high-jvm-memory-non-heap-usage.v1.json |
| flink__flink-high-jvm-memory-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/flink/flink-high-jvm-memory-usage.v1.json |
| google-bigquery__background-metadata-cache-slot-usage.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-bigquery/background-metadata-cache-slot-usage.v2.json |
| google-bigquery__column-metadata-index-percent-change.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-bigquery/column-metadata-index-percent-change.v1.json |
| google-bigquery__single-reservation-slot-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-bigquery/single-reservation-slot-usage.v1.json |
| google-cloud-agent-for-sap__location-constraint-detected.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-agent-for-sap/location-constraint-detected.v1.json |
| google-cloud-agent-for-sap__pacemaker-resource-stopped.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-agent-for-sap/pacemaker-resource-stopped.v1.json |
| google-cloud-agent-for-sap__replication-not-in-sync.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-agent-for-sap/replication-not-in-sync.v1.json |
| google-cloud-alerting__entries-exceeds.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-alerting/entries-exceeds.v1.json |
| google-cloud-alerting__promql-policy.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-alerting/promql-policy.v1.json |
| google-cloud-chronicle__ingestion-quota-limit-approaching.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/ingestion-quota-limit-approaching.v1.json |
| google-cloud-chronicle__ingestion-quota-limit-approaching.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/ingestion-quota-limit-approaching.v2.json |
| google-cloud-chronicle__ingestion-quota-rejects.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/ingestion-quota-rejects.v1.json |
| google-cloud-chronicle__ingestion-quota-rejects.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-cloud-chronicle/ingestion-quota-rejects.v2.json |
| google-dataproc__failed-job.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-dataproc/failed-job.v1.json |
| google-dataproc__long-running-job.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-dataproc/long-running-job.v1.json |
| google-dataproc__stale-job.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-dataproc/stale-job.v1.json |
| google-gce__reservation-utilization-too-high.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gce/reservation-utilization-too-high.v1.json |
| google-gce__reservation-utilization-too-low.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gce/reservation-utilization-too-low.v1.json |
| google-gke__cpu-limit-utilization-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/cpu-limit-utilization-entire-workload.v1.json |
| google-gke__cpu-request-utilization-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/cpu-request-utilization-entire-workload.v1.json |
| google-gke__gke-ingress-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/gke-ingress-server-errors-entire-workload.v1.json |
| google-gke__grpc-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/grpc-server-errors-entire-workload.v1.json |
| google-gke__http-gmp-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/http-gmp-server-errors-entire-workload.v1.json |
| google-gke__istio-gmp-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/istio-gmp-server-errors-entire-workload.v1.json |
| google-gke__istio-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/istio-server-errors-entire-workload.v1.json |
| google-gke__memory-limit-utilization-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/memory-limit-utilization-entire-workload.v1.json |
| google-gke__memory-request-utilization-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/memory-request-utilization-entire-workload.v1.json |
| google-gke__nginx-ingress-server-errors-entire-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/nginx-ingress-server-errors-entire-workload.v1.json |
| google-gke__utilization-container-cpu-within-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/utilization-container-cpu-within-workload.v1.json |
| google-gke__utilization-container-memory-within-workload.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-gke/utilization-container-memory-within-workload.v1.json |
| google-network-intelligence-center__interconnect-egress.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/interconnect-egress.v1.json |
| google-network-intelligence-center__interconnect-ingress.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/interconnect-ingress.v1.json |
| google-network-intelligence-center__packet-loss-high.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/packet-loss-high.v1.json |
| google-network-intelligence-center__packet-loss-high.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/packet-loss-high.v2.json |
| google-network-intelligence-center__vpn-tunnel-bps.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/vpn-tunnel-bps.v1.json |
| google-network-intelligence-center__vpn-tunnel-bps.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/vpn-tunnel-bps.v2.json |
| google-network-intelligence-center__vpn-tunnel-pps.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/vpn-tunnel-pps.v1.json |
| google-network-intelligence-center__vpn-tunnel-pps.v2.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-network-intelligence-center/vpn-tunnel-pps.v2.json |
| google-quotas__quota-usage-promql.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-quotas/quota-usage-promql.v1.json |
| google-vertex-ai__429-out-of-capacity.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-vertex-ai/429-out-of-capacity.v1.json |
| google-vertex-ai__agent-runtime-high-error-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-vertex-ai/agent-runtime-high-error-rate.v1.json |
| google-vertex-ai__pt-utilization-100.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-vertex-ai/pt-utilization-100.v1.json |
| google-vertex-ai__pt-utilization-80.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-vertex-ai/pt-utilization-80.v1.json |
| google-vertex-ai__pt-utilization-90.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-vertex-ai/pt-utilization-90.v1.json |
| google-workload-manager__sap-hana-availability-low.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-workload-manager/sap-hana-availability-low.v1.json |
| google-workload-manager__sap-hana-high-availability-low.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/google-workload-manager/sap-hana-high-availability-low.v1.json |
| hadoop__hadoop-low-available-capacity.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/hadoop/hadoop-low-available-capacity.v1.json |
| haproxy-gke__frontend-sessions-near-limit.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/haproxy-gke/frontend-sessions-near-limit.v1.json |
| hbase__authentication-errors.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/hbase/authentication-errors.v1.json |
| istio-proxy-gke__high-request-duration.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/istio-proxy-gke/high-request-duration.v1.json |
| jvm__heap-memory-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/jvm/heap-memory-usage.v1.json |
| kibana-gke__high-memory-usage.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/kibana-gke/high-memory-usage.v1.json |
| kube-state-gke__pod-disruption-budget-exceeded.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/kube-state-gke/pod-disruption-budget-exceeded.v1.json |
| kube-state-gke__volume-reaching-capacity.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/kube-state-gke/volume-reaching-capacity.v1.json |
| mongodb__high-cpu-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/mongodb/high-cpu-utilization.v1.json |
| mongodb__high-disk-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/mongodb/high-disk-utilization.v1.json |
| nginx-gke__connections-dropped.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/nginx-gke/connections-dropped.v1.json |
| nginx-ingress-gke__connections-dropped.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/nginx-ingress-gke/connections-dropped.v1.json |
| nginx__connections-dropped.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/nginx/connections-dropped.v1.json |
| node-exporter-gke__low-available-memory.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/node-exporter-gke/low-available-memory.v1.json |
| node-exporter-gke__low-filesystem-space.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/node-exporter-gke/low-filesystem-space.v1.json |
| oracledb__high-process-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/oracledb/high-process-utilization.v1.json |
| oracledb__high-session-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/oracledb/high-session-utilization.v1.json |
| oracledb__high-tablespace-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/oracledb/high-tablespace-utilization.v1.json |
| otel-genai__otel-genai-agent-model-error-rate-urn.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-model-error-rate-urn.v1.json |
| otel-genai__otel-genai-agent-model-error-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-model-error-rate.v1.json |
| otel-genai__otel-genai-agent-token-usage-high-urn.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-token-usage-high-urn.v1.json |
| otel-genai__otel-genai-agent-token-usage-high.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-token-usage-high.v1.json |
| otel-genai__otel-genai-agent-tool-error-rate-urn.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-tool-error-rate-urn.v1.json |
| otel-genai__otel-genai-agent-tool-error-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-agent-tool-error-rate.v1.json |
| otel-genai__otel-genai-workflow-error-rate-urn.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-workflow-error-rate-urn.v1.json |
| otel-genai__otel-genai-workflow-error-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/otel-genai/otel-genai-workflow-error-rate.v1.json |
| postgresql__high-connection-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/postgresql/high-connection-utilization.v1.json |
| postgresql__high-cpu-utilization.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/postgresql/high-cpu-utilization.v1.json |
| postgresql__high-db-size.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/postgresql/high-db-size.v1.json |
| scylla-gke__high-prepared-statement-eviction-rate.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/scylla-gke/high-prepared-statement-eviction-rate.v1.json |
| vault__vault-audit-failure-alert.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/vault/vault-audit-failure-alert.v1.json |
| vault__vault-token-renew-revoke-alert.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/vault/vault-token-renew-revoke-alert.v1.json |
| zookeeper-gke__low-free-file-descriptors.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/zookeeper-gke/low-free-file-descriptors.v1.json |
| zookeeper__low-open-file-descriptors.v1.json | https://raw.githubusercontent.com/GoogleCloudPlatform/monitoring-dashboard-samples/master/alerts/zookeeper/low-open-file-descriptors.v1.json |
