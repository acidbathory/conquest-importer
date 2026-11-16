# Datadog fresh corpus manifest (2026-11-15)

Sample of public files, shaped for `importer convert --source datadog` / `convert-dashboards`. Corpus copies live in /tmp/community/datadog-corpus/{monitors,dashboards}; outputs in /tmp/community/datadog-out/. Sampling: seeded random (seed 20261115) from files whose base name is not already in tests/fixtures/datadog/real; scripts in /tmp/community-tools/ (sample.py, tf.py).

## Repositories (all shallow, sparse, blob-filtered clones in /tmp/community/datadog-src/)

| Repo | Licence (from its LICENSE file) | Commit | Used for |
|---|---|---|---|
| https://github.com/DataDog/integrations-core | BSD-3-Clause (root LICENSE has the no-endorsement clause) | 452093828d8d91fae1a7be29b62d9197a20b1aa0 | 55 monitor templates, 40 dashboards |
| https://github.com/DataDog/integrations-extras | BSD-3-Clause | b40475a629cce39f439cc20a95bdeeeb9b76643a | 20 monitor templates, 25 dashboards |
| https://github.com/claranet/terraform-datadog-monitors | Apache-2.0 | 157a201272506dd45fc2094028aa85b91de15231 | 40 monitors shaped from .tf (see caveat) |
| https://github.com/DataDog/datadog-api-client-python | Apache-2.0 | 317c3259edd3e988f282d136b07d87aee9c50dda | OpenAPI spec v1 (facts only), CHANGELOG |
| https://github.com/DataDog/effective-dashboards | Apache-2.0 | d7b3dd6e345ed5f1f4fac201e7cfeb265cf9d2fb | contains guidelines only, no dashboard JSON: no items |
| https://github.com/cloudposse/terraform-datadog-monitor | Apache-2.0 | be98a66d466536646b2029223c6a5f194fa7d4b8 | generic module, no monitor definitions: no items |

Caveat on the 40 terraform monitors: the .tf files hold variable interpolations. tf.py substitutes each variable with its declared default, `module.filter-tags` filters with `{*}`, and the service-check filter with `.over("*")`. Names are not reliable. These are approximations of what an export would hold, and claranet leans on nested `default(a/b, 0)` formulas that real exports may not.

Totals: 115 monitors (75 templates, 40 terraform-shaped), 65 dashboards.

## Items (file in corpus = <repo>__<integration>__<file> ; sha256 prefix of the copied bytes)

| Kind | Repo | Path in repo | sha256[:16] |
|---|---|---|---|
| monitors | integrations-core | kubernetes/assets/monitors/monitor_node_unavailable.json | c2573eb31915ccba |
| monitors | integrations-core | adyen/assets/monitors/notification_of_fraud.json | f60076f0db5b7174 |
| monitors | integrations-core | singlestore/assets/monitors/write_failures.json | f45cc0c338575b7d |
| monitors | integrations-core | elastic/assets/monitors/elastic_indexing_load.json | 7d3a06ba12288f71 |
| monitors | integrations-core | marklogic/assets/monitors/marklogic_long_requests.json | dd59e6ba12ee2999 |
| monitors | integrations-core | foundationdb/assets/monitors/conflicts.json | f98acd949288152e |
| monitors | integrations-core | ray/assets/monitors/cpu_utilization.json | 3d89b266d6a8b1bd |
| monitors | integrations-core | foundationdb/assets/monitors/transaction_start_latency.json | 31b3f410fd8c25d9 |
| monitors | integrations-core | calico/assets/monitors/iptables_save_errors.json | df8cc002e006a218 |
| monitors | integrations-core | argo_rollouts/assets/monitors/rollout_phase.json | 911e29841e8563df |
| monitors | integrations-core | ibm_spectrum_lsf/assets/monitors/no_gpu_slots_available.json | 6578e5349469bd52 |
| monitors | integrations-core | squid/assets/monitors/high_latency_requests.json | ff8d1039d0a45565 |
| monitors | integrations-core | foundationdb/assets/monitors/rejections.json | db72b8de34b89175 |
| monitors | integrations-core | ecs_fargate/assets/monitors/ecs_fargate_cpu_usage.json | f55c8832ab51d025 |
| monitors | integrations-core | azure_iot_edge/assets/monitors/edgehub_retries.json | b24813ca46203cd3 |
| monitors | integrations-core | tomcat/assets/monitors/processing_time.json | 527e1a4ac859092a |
| monitors | integrations-core | sonatype_nexus/assets/monitors/high_number_of_failed_unique_user_authentication_detected.json | dd27b3b6635a107d |
| monitors | integrations-core | elastic/assets/monitors/elastic_average_search_latency.json | 22cee439afebdab4 |
| monitors | integrations-core | sqlserver/assets/monitors/sqlserver_high_processes_blocked.json | c57699ed89d08a7e |
| monitors | integrations-core | kyverno/assets/monitors/controller_drops.json | 9c8991dafc705b1c |
| monitors | integrations-core | litellm/assets/monitors/high_failure_response.json | c413f5c9c83531c8 |
| monitors | integrations-core | coredns/assets/monitors/coredns_request_duration_high.json | ac59918b64ce9b34 |
| monitors | integrations-core | mongo/assets/monitors/high_replication_lag.json | 3cca2427def2cfc3 |
| monitors | integrations-core | resend/assets/monitors/high_transactional_email_delivery_delayed.json | be33337374482f6c |
| monitors | integrations-core | mongo/assets/monitors/low_oplog_window.json | 83ea0153e21950df |
| monitors | integrations-core | fly_io/assets/monitors/app_suspended.json | 86975ca141dab134 |
| monitors | integrations-core | arangodb/assets/monitors/high_server_user_mode.json | 47d6d27da8f281eb |
| monitors | integrations-core | prefect/assets/monitors/backlog_age_above_threshold.json | 80af98c9eadda6ae |
| monitors | integrations-core | adyen/assets/monitors/expired_payout_detected.json | ee1be72f9ba7bd1c |
| monitors | integrations-core | mongo/assets/monitors/high_queued_readers.json | 3528e7348eb14681 |
| monitors | integrations-core | traffic_server/assets/monitors/5xx.json | 89185eae049bc290 |
| monitors | integrations-core | kubernetes/assets/monitors/monitor_deployments_replicas.json | 41a2870be59f9645 |
| monitors | integrations-core | kueue/assets/monitors/preemption_eviction_thrashing.json | 7aa4cb3a34a5df28 |
| monitors | integrations-core | n8n/assets/monitors/workflow_duration_exceeds_slo__p95_.json | 941f1198c365bcba |
| monitors | integrations-core | brevo/assets/monitors/brevo_high_error_rate_by_subject.json | dbf53442598800b8 |
| monitors | integrations-core | haproxy/assets/monitors/frontend_dreq.json | 3841997f582b1f21 |
| monitors | integrations-core | cisco_aci/assets/monitors/cpu_high.json | c7695a42cd68eaf9 |
| monitors | integrations-core | anyscale/assets/monitors/mem_utilization.json | 70af68bd03197b14 |
| monitors | integrations-core | nvidia_triton/assets/monitors/cpu_memory.json | 7039442d3f8f8d06 |
| monitors | integrations-core | rabbitmq/assets/monitors/disk_usage.json | a9f5196216526524 |
| monitors | integrations-core | rabbitmq/assets/monitors/message_unack_prometheus.json | ae87f457bdb6ad51 |
| monitors | integrations-core | lustre/assets/monitors/low_filesystem_capacity.json | 7c1218c68be9a4aa |
| monitors | integrations-core | teradata/assets/monitors/high_disk_space.json | b42e19d690a45b68 |
| monitors | integrations-core | confluent_platform/assets/monitors/unused_partition.json | a78ee2de8d4af021 |
| monitors | integrations-core | hugging_face_tgi/assets/monitors/token_generation_slow.json | 3535b71665be1535 |
| monitors | integrations-core | cisco_sdwan/assets/monitors/device_unreachable.json | c2879af3517b245a |
| monitors | integrations-core | kubernetes/assets/monitors/monitor_pods_failed_state.json | fb03bfad9e58e915 |
| monitors | integrations-core | iboss/assets/monitors/high_rate_of_unprevented_threats_detected.json | 8a5e6f2aaba27842 |
| monitors | integrations-core | marklogic/assets/monitors/marklogic_high_load.json | c8d83e037d9fd852 |
| monitors | integrations-core | active_directory/assets/monitors/ldap_binding.json | 8276e89943793af9 |
| monitors | integrations-core | adyen/assets/monitors/notification_of_chargeback.json | 04512be3d839602a |
| monitors | integrations-core | infiniband/assets/monitors/port_state.json | 95e988e6c0f88d5a |
| monitors | integrations-core | control_m/assets/monitors/job_failures_exceed_threshold.json | b6cc4d0597775166 |
| monitors | integrations-core | kubernetes/assets/monitors/monitor_pods_restarting.json | b0670a70853b4222 |
| monitors | integrations-core | keda/assets/monitors/scaler_errors.json | 3e95de6d91b731a7 |
| monitors | integrations-extras | upbound_uxp/assets/monitors/controller_runtime_reconcile_error.json | 36f96f216b5bc10b |
| monitors | integrations-extras | external_secrets/assets/monitors/sync_errors.json | e96bd2d9af8a8efe |
| monitors | integrations-extras | syncthing/assets/monitors/syncthing_system_error.json | 4276f491016d80db |
| monitors | integrations-extras | watchtower_ziris/assets/monitors/mainframe_cpu_contention_monitor.json | fb0118fa5d6f57d5 |
| monitors | integrations-extras | zilliz_cloud_zilliz_cloud/assets/monitors/cluster_cu_computation_usage_is_higher_than_expected.json | eac50d70efc59314 |
| monitors | integrations-extras | nomad/assets/monitors/nomad_excessive_leadership_losses.json | ec71c9064cf3386e |
| monitors | integrations-extras | zenoh_router/assets/monitors/zenoh_router_disconnected.json | 9d3874b824a6db97 |
| monitors | integrations-extras | syncthing/assets/monitors/syncthing_folder_error.json | e90cc5fb708a94fd |
| monitors | integrations-extras | tailscale/assets/monitors/physical_traffic_received.json | 55077ee5809b96b8 |
| monitors | integrations-extras | external_secrets/assets/monitors/workqueue_depth.json | 18e9471d0bedd752 |
| monitors | integrations-extras | nomad/assets/monitors/nomad_heartbeats_received.json | e15158d8f50f8e17 |
| monitors | integrations-extras | syncthing/assets/monitors/syncthing_disconnected.json | c8b6d3ddd799c601 |
| monitors | integrations-extras | nomad/assets/monitors/nomad_no_jobs_running.json | dbbfc450251b7477 |
| monitors | integrations-extras | external_secrets/assets/monitors/external_secrets_not_ready.json | 8ee54dbcbeb305d6 |
| monitors | integrations-extras | resilience4j/assets/monitors/circuitbreaker_state_open_slow_calls.json | be8c45ea6bfa52e3 |
| monitors | integrations-extras | federatorai/assets/monitors/federatorai_node_cpu_prediction.json | bb5f2b6dece4ca59 |
| monitors | integrations-extras | nn_sdwan/assets/monitors/packet-loss-monitor.json | daf60088e483c992 |
| monitors | integrations-extras | php_opcache/assets/monitors/php-opcache_expunges.json | f012040a6ac330d6 |
| monitors | integrations-extras | tailscale/assets/monitors/virtual_traffic_received.json | b531d1132c883af6 |
| monitors | integrations-extras | mongodb_atlas/assets/monitors/write_latency.json | aa4541527656a6d1 |
| dashboards | integrations-core | ossec_security/assets/dashboards/ossec_windows.json | 2f2d2848b424224d |
| dashboards | integrations-core | carbon_black_cloud/assets/dashboards/carbon_black_cloud_auth_events.json | 20ee66a1e69f6e22 |
| dashboards | integrations-core | sonicwall_firewall/assets/dashboards/sonicwall_firewall_security_services.json | f95c5100e1cfd04f |
| dashboards | integrations-core | wlan/assets/dashboards/wlan_overview.json | a577392003aceeb9 |
| dashboards | integrations-core | symantec_endpoint_protection/assets/dashboards/symantec_endpoint_protection_system.json | 987595b1ba9175cf |
| dashboards | integrations-core | palo_alto_panorama/assets/dashboards/palo_alto_panorama_threat.json | c887e8106820b654 |
| dashboards | integrations-core | intercom/assets/dashboards/intercom_ticket.json | 408268afa1a813e0 |
| dashboards | integrations-core | ping_federate/assets/dashboards/ping_federate_admin.json | 6006614c22c3ad53 |
| dashboards | integrations-core | cassandra/assets/dashboards/cassandra_overview.json | b9f67c5b53f70c44 |
| dashboards | integrations-core | zk/assets/dashboards/zookeeper_dashboard.json | b8d5fa93b736e0c0 |
| dashboards | integrations-core | beyondtrust_password_safe/assets/dashboards/beyondtrust_password_safe_management_and_secret_safe_insights.json | 634a22c456dfe8ca |
| dashboards | integrations-core | jboss_wildfly/assets/dashboards/jboss_wildfly.json | 7ef1fea95a456116 |
| dashboards | integrations-core | kong/assets/dashboards/kong_api.json | a3b15cff6208bf9c |
| dashboards | integrations-core | cisco_sdwan/assets/dashboards/cisco_sd-wan.json | c6f7e353490e9bbe |
| dashboards | integrations-core | checkpoint_quantum_firewall/assets/dashboards/checkpoint_quantum_firewall_anti_spam_and_email_security.json | 7b733cf4376724e8 |
| dashboards | integrations-core | cisco_secure_firewall/assets/dashboards/cisco_secure_firewall_user_authentication.json | 1dd37ed9446c2b16 |
| dashboards | integrations-core | cisco_secure_client/assets/dashboards/cisco_secure_client_vpn_connection_insights.json | 97c310820072eb46 |
| dashboards | integrations-core | ossec_security/assets/dashboards/ossec_overview.json | f2d8c6a962d17785 |
| dashboards | integrations-core | yarn/assets/dashboards/yarn_dashboard.json | dfc7b64a1fb6aef7 |
| dashboards | integrations-core | envoy/assets/dashboards/envoy_overview.json | 6bf09ac1a902409c |
| dashboards | integrations-core | wazuh/assets/dashboards/wazuh_MITRE_ATT&CK.json | a7470cdacc0ab1c5 |
| dashboards | integrations-core | arctic_wolf_aurora_endpoint_security/assets/dashboards/arctic_wolf_aurora_endpoint_security_network_insights.json | 21de32dc136eb131 |
| dashboards | integrations-core | kafka/assets/dashboards/kafka_kraft_dashboard.json | 05a3022613ad53e5 |
| dashboards | integrations-core | falco/assets/dashboards/falco_metrics.json | 38b19096770053b9 |
| dashboards | integrations-core | cisco_secure_firewall/assets/dashboards/cisco_secure_firewall_failover.json | 67c7907ee760d4a5 |
| dashboards | integrations-core | watchguard_firebox/assets/dashboards/watchguard_firebox_proxy_policy.json | 264cb2a2c1a8884f |
| dashboards | integrations-core | ivanti_connect_secure/assets/dashboards/ivanti_connect_secure_authentication.json | 1ff5193863e1dfa0 |
| dashboards | integrations-core | arangodb/assets/dashboards/arangodb_overview.json | 55b5fcb0e4773ce5 |
| dashboards | integrations-core | spark/assets/dashboards/databricks_overview.json | 042b2a6e084d3a14 |
| dashboards | integrations-core | calico/assets/dashboards/calico_overview.json | 697d348a410f70e7 |
| dashboards | integrations-core | ossec_security/assets/dashboards/ossec_ftpd.json | 0f378f8fa6c46686 |
| dashboards | integrations-core | bitdefender/assets/dashboards/bitdefender_firewall_event_details.json | 458bc8fef4e63f51 |
| dashboards | integrations-core | openvpn/assets/dashboards/openvpn_overview.json | 79687e87b68ccf49 |
| dashboards | integrations-core | cisco_secure_firewall/assets/dashboards/cisco_secure_firewall_vpn_failover.json | abdc37beee46c6a6 |
| dashboards | integrations-core | ping_federate/assets/dashboards/ping_federate_audit.json | f988cce2e7a0f478 |
| dashboards | integrations-core | delinea_privilege_manager/assets/dashboards/delinea_privilege_manager_application_control_events.json | 5071e785915484c3 |
| dashboards | integrations-core | anyscale/assets/dashboards/anyscale_overview.json | c4d468e6fa61b3dd |
| dashboards | integrations-core | delinea_privilege_manager/assets/dashboards/delinea_privilege_manager_local_security_events.json | 7cdd28118154c2b4 |
| dashboards | integrations-core | checkpoint_quantum_firewall/assets/dashboards/checkpoint_quantum_firewall_anti_bot.json | 00cd6fd33f016bf8 |
| dashboards | integrations-core | keeper/assets/dashboards/keeper_user_activity.json | 1effa916d33806cf |
| dashboards | integrations-extras | redis_cloud/assets/dashboards/redis_cloud_networking.json | 826a90bd10c00e33 |
| dashboards | integrations-extras | ngrok/assets/dashboards/ngrok_http_events.json | 4e51227422ec80e5 |
| dashboards | integrations-extras | redis_cloud/assets/dashboards/redis_cloud_active-active.json | 72b9be48043e6abf |
| dashboards | integrations-extras | cloudnatix/assets/dashboards/cloudnatix_overview.json | 062072817ecd4bb4 |
| dashboards | integrations-extras | emnify/assets/dashboards/emnify_dashboard.json | bce4237ff3dd7c51 |
| dashboards | integrations-extras | perimeterx/assets/dashboards/PerimeterX_Bot_Defender_Dashboard.json | 1be384b7927fddcf |
| dashboards | integrations-extras | superwise/assets/dashboards/superwise.json | e41a3603a79ca616 |
| dashboards | integrations-extras | redis_enterprise_prometheus/assets/dashboards/redis_enterprise-prometheus_cloud-database.json | 889c9914ee7ce72a |
| dashboards | integrations-extras | akamai_datastream_2/assets/dashboards/akamai_datastream_2_overview.json | 7706711403471522 |
| dashboards | integrations-extras | n2ws/assets/dashboards/N2WSBackup&Recovery-EntityTypesDetails.json | ae620636f3aeece3 |
| dashboards | integrations-extras | harness_cloud_cost_management/assets/dashboards/harness_cloud_cost_management_overview.json | d67818d71a39ea1f |
| dashboards | integrations-extras | contrastsecurity/assets/dashboards/contrast_security_protect.json | a86bc1417e35cb04 |
| dashboards | integrations-extras | algorithmia/assets/dashboards/algorithmia.json | 2678d72c1f137a17 |
| dashboards | integrations-extras | redisenterprise/assets/dashboards/redisenterprise_cluster_top_view.json | f995636399cb588f |
| dashboards | integrations-extras | zilliz_cloud_zilliz_cloud/assets/dashboards/zilliz_cloud_dashboard.json | 51bdcef6464f8f81 |
| dashboards | integrations-extras | redis_enterprise_prometheus/assets/dashboards/redis_enterprise-prometheus_database.json | 1a58c5e51c5a7fdf |
| dashboards | integrations-extras | zscaler/assets/dashboards/zscaler_tunnel.json | 496dcd416344c08b |
| dashboards | integrations-extras | typingdna_activelock/assets/dashboards/TypingDNAActiveLock.json | 90681716f9c9ec5b |
| dashboards | integrations-extras | gigamon/assets/dashboards/gigamon_overview.json | df7619b74063909f |
| dashboards | integrations-extras | reflectiz/assets/dashboards/WebExposureAlertsDashboard.json | 3c734440bb27b861 |
| dashboards | integrations-extras | unitq/assets/dashboards/unitq_overview.json | 08d7ec28f48bc733 |
| dashboards | integrations-extras | syncthing/assets/dashboards/syncthing_overview.json | 047003cb06e4184a |
| dashboards | integrations-extras | neo4j/assets/dashboards/Neo4j5ClusterDashboard.json | ee465f746030d33c |
| dashboards | integrations-extras | redis_enterprise_prometheus/assets/dashboards/redis_enterprise-prometheus_node.json | fdc9f24a5c1a029c |
| dashboards | integrations-extras | 1e/assets/dashboards/1EDashboard.json | 0e0c8d775344c197 |
| monitors | terraform-datadog-monitors | database/redis/monitors-redis.tf#memory_used | a76aa10255558062 |
| monitors | terraform-datadog-monitors | database/proxysql/monitors-proxysql.tf#proxysql_slow | aef712b336831f02 |
| monitors | terraform-datadog-monitors | database/redis/monitors-redis.tf#not_responding | c57415462a2207ad |
| monitors | terraform-datadog-monitors | cloud/aws/ecs/ec2-cluster/monitors-ecs-ec2-cluster.tf#cluster_cpu_utilization | 51e897384105b0bd |
| monitors | terraform-datadog-monitors | cloud/gcp/gce/instance/monitors-gce-instance.tf#cpu_utilization | f3f12f49b5beb3cd |
| monitors | terraform-datadog-monitors | database/postgresql/monitors-postgresql.tf#postgresql_replication_delay | a861c1942971fe10 |
| monitors | terraform-datadog-monitors | cloud/azure/functions/monitors-functions.tf#function_http_5xx_errors_rate | f70dadbc3a6e5310 |
| monitors | terraform-datadog-monitors | cloud/aws/ecs/common/monitors-ecs-common.tf#service_missing_tasks | 5a3a507ddef78b5c |
| monitors | terraform-datadog-monitors | cloud/aws/mq/monitors-mq.tf#cpu_utilization | e87cec7f0da0b2f9 |
| monitors | terraform-datadog-monitors | cloud/aws/ecs/common/monitors-ecs-common.tf#service_cpu_utilization | 2955433444f4fbd8 |
| monitors | terraform-datadog-monitors | cloud/gcp/big-query/monitors-big-query.tf#concurrent_queries | 34ca4756619d3801 |
| monitors | terraform-datadog-monitors | caas/kubernetes/workload/monitors-k8s-workload.tf#pod_disruption_budget_not_respected | ed3847166490adee |
| monitors | terraform-datadog-monitors | cloud/azure/apimanagement/monitors-azure-apimanagement.tf#apimgt_failed_requests | 313b825b25d13e70 |
| monitors | terraform-datadog-monitors | cloud/aws/rds/common/monitors-rds-common.tf#rds_replica_lag | 7d6f9fbe93664c91 |
| monitors | terraform-datadog-monitors | caas/docker/monitors-docker.tf#memory_used | cd516f0014be56f2 |
| monitors | terraform-datadog-monitors | caas/kubernetes/node/monitors-k8s-node.tf#volume_space | 475f0e38c8f77111 |
| monitors | terraform-datadog-monitors | cloud/azure/cosmosdb/monitors-cosmosdb.tf#cosmos_db_4xx_requests | f40acbc7a9f62be6 |
| monitors | terraform-datadog-monitors | database/elasticsearch/monitors-elasticsearch.tf#query_cache_evictions_change | 256fb8e3851d3834 |
| monitors | terraform-datadog-monitors | database/mysql/monitors-mysql.tf#mysql_connection | 11b27123880ccdce |
| monitors | terraform-datadog-monitors | cloud/aws/elasticache/memcached/monitors-memcached.tf#memcached_get_hits | 215b4652f4f23d7b |
| monitors | terraform-datadog-monitors | cloud/azure/storage/monitors-azure-storage.tf#blob_throttling_error_requests | 324b51971b8f5a22 |
| monitors | terraform-datadog-monitors | cloud/azure/app-gateway/monitors-app-gateway.tf#appgateway_healthy_host_ratio | 84ad8eeb62695a67 |
| monitors | terraform-datadog-monitors | cloud/azure/servicebus/monitors-service-bus.tf#service_bus_no_active_connections | 8b72bdd5ab0f0024 |
| monitors | terraform-datadog-monitors | cloud/azure/redis/monitors-azure-redis.tf#evictedkeys | 503472bc648035e8 |
| monitors | terraform-datadog-monitors | cloud/gcp/lb/monitors-lb.tf#backend_latency_bucket | 7cefd7b051a921aa |
| monitors | terraform-datadog-monitors | database/mysql/monitors-mysql.tf#mysql_questions_anomaly | dd1420a6db634b51 |
| monitors | terraform-datadog-monitors | caas/kubernetes/workload/monitors-k8s-workload.tf#deployments_replica_too_low | 94604054b0878306 |
| monitors | terraform-datadog-monitors | database/mongodb/monitors-mongo.tf#mongodb_replication | 63b2a99110093147 |
| monitors | terraform-datadog-monitors | cloud/azure/storage/monitors-azure-storage.tf#table_throttling_error_requests | b2baae3b11724060 |
| monitors | terraform-datadog-monitors | cloud/gcp/pubsub/subscription/monitors-subscription.tf#oldest_unacked_message_age | 8bb8b3f86e957746 |
| monitors | terraform-datadog-monitors | cloud/azure/stream-analytics/monitors-stream-analytics.tf#su_utilization | 8d9831ec6c618057 |
| monitors | terraform-datadog-monitors | cloud/azure/sql-elasticpool/monitors-sql-elasticpool.tf#sql_elasticpool_free_space_low | 838861822314b4fd |
| monitors | terraform-datadog-monitors | cloud/azure/storage/monitors-azure-storage.tf#file_timeout_error_requests | d82d8d86a5f00943 |
| monitors | terraform-datadog-monitors | cloud/azure/backup-vault/monitors-backup-vault.tf#backup_vault_restore_unhealthy_event | 4bea4aed14587bf1 |
| monitors | terraform-datadog-monitors | caas/kubernetes/node/monitors-k8s-node.tf#disk_pressure | 76e9b622fc3448a0 |
| monitors | terraform-datadog-monitors | cloud/aws/beanstalk/monitors-beanstalk.tf#health | 7f27a3966b744f73 |
| monitors | terraform-datadog-monitors | database/elasticsearch/monitors-elasticsearch.tf#indexing_latency | 14b81b881eecc27e |
| monitors | terraform-datadog-monitors | cloud/gcp/big-query/monitors-big-query.tf#uploaded_bytes_billed | cab9ed0cff1040f4 |
| monitors | terraform-datadog-monitors | database/elasticsearch/monitors-elasticsearch.tf#http_connections_anomaly | d725e45df0162017 |
| monitors | terraform-datadog-monitors | cloud/aws/mq/monitors-mq.tf#consumer_count | 303e28c5bc450567 |
