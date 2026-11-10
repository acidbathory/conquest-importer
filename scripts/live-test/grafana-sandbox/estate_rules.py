"""Alert rule shapes for the ckg- test estate (paraphrased from public mixins; Apache-2.0).

Sources: kubernetes-mixin, node-exporter mixin, prometheus mixin, alertmanager mixin,
prometheus-operator and kube-prometheus rules. Expressions are rewritten in the Grafana
query-reduce-threshold form; each rule keeps the PromQL construct that makes it hard.
Tuple: title, group, expr, op, threshold, for, noDataState, execErrState, severity.
"""

N = "NoData"
SIMPLE = [
    # kubernetes-mixin: apps / resources / storage / system
    ("ckg-KubePodCrashLooping", "kubernetes-apps", 'max_over_time(kube_pod_container_status_waiting_reason{reason="CrashLoopBackOff", job="kube-state-metrics", namespace=~".*"}[5m])', "gte", 1, "15m", N, "Error", "warning"),
    ("ckg-KubePodNotReady", "kubernetes-apps", 'sum by (namespace, pod, cluster) (max by (namespace, pod, cluster) (kube_pod_status_phase{job="kube-state-metrics", phase=~"Pending|Unknown|Failed"}) * on (namespace, pod, cluster) group_left(owner_kind) topk by (namespace, pod, cluster) (1, max by (namespace, pod, owner_kind, cluster) (kube_pod_owner{owner_kind!="Job"})))', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-KubeDeploymentReplicasMismatch", "kubernetes-apps", '(kube_deployment_spec_replicas{job="kube-state-metrics"} > kube_deployment_status_replicas_available{job="kube-state-metrics"}) and (changes(kube_deployment_status_replicas_updated{job="kube-state-metrics"}[10m]) == 0)', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-KubeStatefulSetReplicasMismatch", "kubernetes-apps", 'kube_statefulset_status_replicas_ready{job="kube-state-metrics"} != kube_statefulset_status_replicas{job="kube-state-metrics"}', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-KubeJobFailed", "kubernetes-apps", 'kube_job_failed{job="kube-state-metrics"}', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-KubeHpaMaxedOut", "kubernetes-apps", 'kube_horizontalpodautoscaler_status_current_replicas{job="kube-state-metrics"} == kube_horizontalpodautoscaler_spec_max_replicas{job="kube-state-metrics"}', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-CPUThrottlingHigh", "kubernetes-resources", 'sum(increase(container_cpu_cfs_throttled_periods_total{container!="", }[5m])) by (cluster, container, pod, namespace) / sum(increase(container_cpu_cfs_periods_total{}[5m])) by (cluster, container, pod, namespace)', "gt", 0.25, "15m", N, "Error", "info"),
    ("ckg-KubeCPUOvercommit", "kubernetes-resources", 'sum(namespace_cpu:kube_pod_container_resource_requests:sum{}) by (cluster) - (sum(kube_node_status_allocatable{resource="cpu"}) by (cluster) - max(kube_node_status_allocatable{resource="cpu"}) by (cluster))', "gt", 0, "10m", N, "Error", "warning"),
    ("ckg-KubeQuotaAlmostFull", "kubernetes-resources", 'kube_resourcequota{job="kube-state-metrics", type="used"} / ignoring(instance, job, type) (kube_resourcequota{job="kube-state-metrics", type="hard"} > 0)', "gt", 0.9, "15m", N, "Error", "info"),
    ("ckg-KubePersistentVolumeFillingUp", "kubernetes-storage", '(kubelet_volume_stats_available_bytes{job="kubelet", metrics_path="/metrics"} / kubelet_volume_stats_capacity_bytes{job="kubelet", metrics_path="/metrics"})', "lt", 0.03, "1m", N, "Error", "critical"),
    ("ckg-KubePersistentVolumeFillingUpPredicted", "kubernetes-storage", 'predict_linear(kubelet_volume_stats_available_bytes{job="kubelet", metrics_path="/metrics"}[6h], 4 * 24 * 3600)', "lt", 0, "1h", N, "Error", "warning"),
    ("ckg-KubeNodeNotReady", "kubernetes-system", 'kube_node_status_condition{job="kube-state-metrics",condition="Ready",status="true"} == 0', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-KubeletTooManyPods", "kubernetes-system", 'count by (cluster, node) ((kube_pod_status_phase{job="kube-state-metrics", phase="Running"} == 1) * on (instance, pod, namespace, cluster) group_left(node) topk by (instance, pod, namespace, cluster) (1, kube_pod_info{job="kube-state-metrics"})) / max by (cluster, node) (kube_node_status_capacity{job="kube-state-metrics", resource="pods"} != 1)', "gt", 0.95, "15m", N, "Error", "info"),
    ("ckg-KubeAPIErrorBudgetBurn", "kubernetes-system", 'sum by (cluster) (apiserver_request:burnrate1h) > (14.40 * 0.01000) and sum by (cluster) (apiserver_request:burnrate5m) > (14.40 * 0.01000)', "gt", 0, "2m", N, "Error", "critical"),
    ("ckg-KubeAPILatencyHigh", "kubernetes-system", 'histogram_quantile(0.99, sum by (le, verb) (rate(apiserver_request_duration_seconds_bucket{job="apiserver", verb!~"WATCH|CONNECT"}[5m])))', "gt", 1, "5m", N, "Error", "warning"),
    ("ckg-KubeClientCertificateExpiration", "kubernetes-system", 'apiserver_client_certificate_expiration_seconds_count{job="apiserver"} > 0 and on (job) histogram_quantile(0.01, sum by (job, le) (rate(apiserver_client_certificate_expiration_seconds_bucket{job="apiserver"}[5m]))) < 604800', "gt", 0, "0s", N, "Error", "warning"),
    # node-exporter mixin
    ("ckg-NodeFilesystemSpaceFillingUp", "node-exporter", '(node_filesystem_avail_bytes{job="node-exporter",fstype!="",mountpoint!=""} / node_filesystem_size_bytes{job="node-exporter",fstype!="",mountpoint!=""} * 100 < 15 and predict_linear(node_filesystem_avail_bytes{job="node-exporter",fstype!="",mountpoint!=""}[6h], 24*60*60) < 0 and node_filesystem_readonly{job="node-exporter",fstype!="",mountpoint!=""} == 0)', "gt", 0, "1h", N, "Error", "warning"),
    ("ckg-NodeFilesystemAlmostOutOfFiles", "node-exporter", 'node_filesystem_files_free{job="node-exporter",fstype!="",mountpoint!=""} / node_filesystem_files{job="node-exporter",fstype!="",mountpoint!=""} * 100', "lt", 5, "1h", N, "Error", "warning"),
    ("ckg-NodeNetworkReceiveErrs", "node-exporter", 'rate(node_network_receive_errs_total{job="node-exporter"}[2m]) / rate(node_network_receive_packets_total{job="node-exporter"}[2m])', "gt", 0.01, "1h", N, "Error", "warning"),
    ("ckg-NodeHighNumberConntrackEntriesUsed", "node-exporter", '(node_nf_conntrack_entries{job="node-exporter"} / node_nf_conntrack_entries_limit)', "gt", 0.75, "0s", N, "Error", "warning"),
    ("ckg-NodeClockSkewDetected", "node-exporter", '(node_timex_offset_seconds{job="node-exporter"} > 0.05 and deriv(node_timex_offset_seconds{job="node-exporter"}[5m]) >= 0)', "gt", 0, "10m", N, "Error", "warning"),
    ("ckg-NodeMemoryMajorPagesFaults", "node-exporter", 'rate(node_vmstat_pgmajfault{job="node-exporter"}[5m])', "gt", 500, "15m", N, "Error", "warning"),
    ("ckg-NodeCPUHighUsage", "node-exporter", 'sum without(mode) (avg without (cpu) (rate(node_cpu_seconds_total{job="node-exporter", mode!="idle"}[2m]))) * 100', "gt", 90, "15m", N, "Error", "info"),
    ("ckg-NodeSystemSaturation", "node-exporter", 'node_load1{job="node-exporter"} / count without (cpu, mode) (node_cpu_seconds_total{job="node-exporter", mode="idle"})', "gt", 2, "15m", N, "Error", "warning"),
    ("ckg-NodeMemoryHighUtilization", "node-exporter", '100 - (node_memory_MemAvailable_bytes{job="node-exporter"} / node_memory_MemTotal_bytes{job="node-exporter"} * 100)', "gt", 90, "15m", N, "Error", "warning"),
    ("ckg-NodeDiskIOSaturation", "node-exporter", 'rate(node_disk_io_time_weighted_seconds_total{job="node-exporter", device!=""}[5m])', "gt", 10, "30m", N, "Error", "warning"),
    # prometheus mixin and operator
    ("ckg-PrometheusTargetDown", "prometheus", 'up{job=~"prometheus|node-exporter|kubelet"}', "lt", 1, "5m", "Alerting", "Error", "critical"),
    ("ckg-PrometheusAbsent", "prometheus", 'absent(up{job="prometheus"})', "gt", 0, "10m", "Alerting", "Error", "critical"),
    ("ckg-PrometheusNotIngestingSamples", "prometheus", 'rate(prometheus_tsdb_head_samples_appended_total{job="prometheus"}[5m])', "lte", 0, "10m", N, "Error", "warning"),
    ("ckg-PrometheusRemoteWriteBehind", "prometheus", '(max_over_time(prometheus_remote_storage_highest_timestamp_in_seconds{job="prometheus"}[5m]) - ignoring(remote_name, url) group_right max_over_time(prometheus_remote_storage_queue_highest_sent_timestamp_seconds{job="prometheus"}[5m]))', "gt", 120, "15m", N, "Error", "critical"),
    ("ckg-PrometheusRuleFailures", "prometheus", 'increase(prometheus_rule_evaluation_failures_total{job="prometheus"}[5m])', "gt", 0, "15m", N, "Error", "critical"),
    ("ckg-PrometheusTSDBReloadsFailing", "prometheus", 'increase(prometheus_tsdb_reloads_failures_total{job="prometheus"}[3h])', "gt", 0, "4h", N, "Error", "warning"),
    ("ckg-PrometheusNotificationQueueRunningFull", "prometheus", 'predict_linear(prometheus_notifications_queue_length{job="prometheus"}[5m], 60 * 30) > min_over_time(prometheus_notifications_queue_capacity{job="prometheus"}[5m])', "gt", 0, "15m", N, "Error", "warning"),
    ("ckg-AlertmanagerFailedToSendAlerts", "alertmanager", '(rate(alertmanager_notifications_failed_total{job="alertmanager"}[5m]) / rate(alertmanager_notifications_total{job="alertmanager"}[5m]))', "gt", 0.01, "5m", N, "Error", "warning"),
    ("ckg-AlertmanagerClusterDown", "alertmanager", '(count by (cluster) (avg_over_time(up{job="alertmanager"}[5m]) < 0.5) / count by (cluster) (up{job="alertmanager"}))', "gte", 0.5, "5m", N, "Error", "critical"),
    ("ckg-PrometheusOperatorReconcileErrors", "prometheus-operator", 'sum by (controller, namespace) (rate(prometheus_operator_reconcile_errors_total{job="prometheus-operator"}[5m])) / sum by (controller, namespace) (rate(prometheus_operator_reconcile_operations_total{job="prometheus-operator"}[5m]))', "gt", 0.1, "10m", N, "Error", "warning"),
    # application SLO shapes
    ("ckg-HighErrorRatio5xx", "app-slo", 'sum(rate(http_requests_total{job="api",code=~"5.."}[5m])) / sum(rate(http_requests_total{job="api"}[5m]))', "gt", 0.05, "10m", N, "Error", "critical"),
    ("ckg-LatencyP99Regression", "app-slo", 'histogram_quantile(0.99, sum by (le, handler) (rate(http_request_duration_seconds_bucket{job="api"}[5m]))) / histogram_quantile(0.99, sum by (le, handler) (rate(http_request_duration_seconds_bucket{job="api"}[5m] offset 1d)))', "gt", 1.5, "15m", N, "Error", "warning"),
    ("ckg-JVMHeapNearLimit", "app-slo", 'sum by (instance) (jvm_memory_used_bytes{area="heap"}) / sum by (instance) (jvm_memory_max_bytes{area="heap"})', "gt", 0.9, "5m", N, "Error", "warning"),
    ("ckg-KafkaConsumerLag", "app-slo", 'sum by (consumergroup, topic) (kafka_consumergroup_lag)', "gt", 10000, "10m", N, "Error", "warning"),
    ("ckg-PostgresConnectionsHigh", "app-slo", 'sum by (instance) (pg_stat_activity_count) / max by (instance) (pg_settings_max_connections)', "gt", 0.8, "5m", N, "Error", "warning"),
    ("ckg-RedisMemoryHigh", "app-slo", 'redis_memory_used_bytes / redis_memory_max_bytes', "gt", 0.85, "5m", N, "Error", "warning"),
    ("ckg-BlackboxProbeFailed", "app-slo", 'probe_success{job="blackbox"}', "lt", 1, "2m", "Alerting", "Alerting", "critical"),
    ("ckg-BlackboxSslExpiry", "app-slo", '(probe_ssl_earliest_cert_expiry{job="blackbox"} - time()) / 86400', "lt", 14, "0s", N, "Error", "warning"),
    ("ckg-TopkNoisyPods", "app-slo", 'topk(5, sum by (pod) (rate(container_cpu_usage_seconds_total{container!=""}[5m])))', "gt", 1, "10m", N, "Error", "info"),
    ("ckg-RecordingRuleCpu", "app-slo", 'node_namespace_pod_container:container_cpu_usage_seconds_total:sum_irate{namespace="prod"}', "gt", 4, "10m", N, "Error", "warning"),
    ("ckg-RecordingRuleUnavailable", "app-slo", 'job:request_latency_seconds:mean5m{job="api"}', "gt", 2, "5m", "OK", "OK", "info"),
    ("ckg-LokiErrorRate", "loki-logs", 'sum by (app) (rate({namespace="prod"} |= "level=error" [5m]))', "gt", 5, "5m", N, "Error", "warning"),
    ("ckg-LokiPanicBytes", "loki-logs", 'sum(bytes_over_time({app="api"} |~ "panic|fatal" [10m]))', "gt", 0, "0s", N, "Error", "critical"),
    ("ckg-LokiNoLogs", "loki-logs", 'sum(count_over_time({app="api"}[15m]))', "lt", 1, "5m", "Alerting", "Error", "warning"),
]


def multi() -> list[dict]:
    """Rules that are not a plain query-reduce-threshold chain (built by publish_estate_alerts)."""
    return [
        {"title": "ckg-MultiCondErrorsAndTraffic", "group": "multi-condition", "kind": "and",
         "a": 'sum(rate(http_requests_total{code=~"5.."}[5m]))', "b": 'sum(rate(http_requests_total[5m]))',
         "for": "10m", "noData": "NoData"},
        {"title": "ckg-MathRatioExpression", "group": "multi-condition", "kind": "math",
         "a": 'sum(rate(http_requests_total{code=~"5.."}[5m]))', "b": 'sum(rate(http_requests_total[5m]))',
         "for": "5m", "noData": "NoData"},
        {"title": "ckg-WithinRangeTemperature", "group": "multi-condition", "kind": "range",
         "a": 'avg(temperature_celsius)', "for": "5m", "noData": "OK"},
        {"title": "ckg-ThresholdOnReduceMean", "group": "multi-condition", "kind": "mean",
         "a": 'avg by (instance) (node_load5)', "for": "10m", "noData": "KeepLast"},
        {"title": "ckg-CrossDatasourceMath", "group": "multi-condition", "kind": "cross",
         "a": 'sum(rate(errors_total[5m]))', "b": 'sum(rate({app="api"} |= "error" [5m]))',
         "for": "5m", "noData": "NoData"},
    ]
