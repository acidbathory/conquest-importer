# Sources of the Azure workbook templates

Thirty real, public workbook templates, used to measure the dashboard converter. Nothing here was read from a tenant.

| Repository | Pinned commit | Licence |
|---|---|---|
| microsoft/Application-Insights-Workbooks | `cc8922da42fcf1ef5ae308e076d9a7dc69698655` | MIT, Copyright (c) Microsoft Corporation (see `LICENSE`) |

## What was changed

- Every `id` and `chartId` key was removed (374 keys, also inside content kept as a JSON string) and every remaining GUID in the text was replaced by `00000000-0000-0000-0000-000000000000` (24 places).
- The files were re-indented. Nothing else was changed; the item types, queries and parameters are as published.
- `LICENSE` is the upstream licence file, unchanged.

## Files

| File | Upstream path | SHA-256 of the upstream file |
|---|---|---|
| `adxcluster-atresourcetables.workbook` | `Workbooks/ADXCluster/AtResourceTables/ResourceTableDetails.workbook` | `972b7beadba09b286ba279cb4a60ff43f678ea1ff00c248904354b851662d2a2` |
| `aks-workload-details-divided.workbook` | `Workbooks/AKS/Workload Details Divided/Workload Details Divided.workbook` | `6b83306de667ef5f653d3ac918664dc774681971255f231ea61f50518045e094` |
| `activity-log-atscale.workbook` | `Workbooks/Activity Log/AtScale/ActivityLogsInsights.workbook` | `5e40abc828ded0c70cd9af1697ef7c58a5ace47a1f88483e5ba95eecce69d815` |
| `app-hub-storage-accounts.workbook` | `Workbooks/App Hub/Storage Accounts/Storage Accounts.workbook` | `e1a84e8731b6991f985b35e6165652120a6c64906ff4af1763e2af0ff48835ac` |
| `app-services-infrastructure-app-service-key-metrics.workbook` | `Workbooks/App Services - Infrastructure/App Service Key Metrics/App Service Key Metrics.workbook` | `1e12f2cdccfe30ae4e4970900e2148d4932cf3133d0a97d02356af35da1993fd` |
| `availability-downtime-outages.workbook` | `Workbooks/Availability/Downtime Outages/Downtime Outages.workbook` | `ca31c2d1e03e14e2c35d59973a1b3c53f3a77be6a66ae6e91b4e597e451ed16e` |
| `azure-api-management-analytics.workbook` | `Workbooks/Azure API Management/Analytics/Analytics.workbook` | `ebaf560463f7920914a8e3ff5fe32f7c677848572aefcd0689ada6cfe364ee80` |
| `azure-active-directory-domain-services-account-activity-report.workbook` | `Workbooks/Azure Active Directory Domain Services/Account Activity Report/AccountActivityReport.workbook` | `16c137994899c46dd1c96bb7bae47ba93f4f1d1e4402226f92c87293745076d7` |
| `azure-active-directory-troubleshooting-signinfailureanalysis.workbook` | `Workbooks/Azure Active Directory TroubleShooting/SignInFailureAnalysis/SignInFailureAnalysis.workbook` | `31a6eadd1ca12aae653f2b7fe57cd70aeb92e7d1e20a3790d9462c40c28d80d9` |
| `azure-logic-apps-sharedtemplates-singlelatemplate-singlelalogbasedruno.workbook` | `Workbooks/Azure Logic Apps/SharedTemplates/SingleLATemplate/SingleLALogBasedRunOverview/SingleLALogBasedRunOverview.workbook` | `b94310cc7607f93c422e65f08dc21e327aaa42995bfa2a6f6834d765bbee6965` |
| `azure-managed-instance-for-apache-cassandra-cassandra-mi-insights.workbook` | `Workbooks/Azure Managed Instance for Apache Cassandra/Cassandra MI Insights/Cassandra MI Insights.workbook` | `fda538cdb22a3b7adecb5f982d1bd39cab5827e6c5b7ed24bbf03bf2cced2ca5` |
| `azure-storage-cache-performance.workbook` | `Workbooks/Azure Storage Cache/Performance/Performance.workbook` | `7c04ae0b68742c8d3b5b1c9f803b4196fb949079c5b0c44441af15163bbdf636` |
| `communication-services-email-insights.workbook` | `Workbooks/Communication Services/Email_Insights/Email_Insights.workbook` | `92a5778408faa0f0927bfd602d7222c186080913b1ed93c085b7ca0af62ca26a` |
| `cosmosdb-fleet.workbook` | `Workbooks/CosmosDb Fleet/Overview.workbook` | `45f83037009b1a9ff4f736c4375413db5d11b4af5301a1ae7e721d9bec331eb5` |
| `cosmosdb-mongo-vcore-overview.workbook` | `Workbooks/CosmosDb Mongo vCore/Overview/Overview.workbook` | `e5fcc1f76c662960b8a6eeb1a97200c7bbad68b12702a7cea132bc6dc529f992` |
| `failures-new-failures-analysis.workbook` | `Workbooks/Failures/New Failures Analysis/New Failures Analysis.workbook` | `496f214b0e73a6e9dd663d8e8b246be65d8b26d9d1903e1542e449b053407b43` |
| `hdinsight-v2-cluster-trino.workbook` | `Workbooks/HDInsight/v2/Cluster/Trino/Trino.workbook` | `33859eba1fb16aa4244aefd27ce37a4576ae226ae6c74eb76dd50134f7da756a` |
| `hyperscale-citus-metrics-overview.workbook` | `Workbooks/Hyperscale Citus/Metrics Overview/Metrics Overview.workbook` | `42842639bc9f45d8514c073102a27574bb662f2013f17d52c3aa1b78cda41051` |
| `individual-storage-overview.workbook` | `Workbooks/Individual Storage/Overview/Overview.workbook` | `77db9204018c2e02f3098b2f5434deeadca9e61756e79f30eb3c362e990dae15` |
| `insights-rediscache.workbook` | `Workbooks/Insights/RedisCache/Insights.workbook` | `322741c74443df4338e7b155838b1d451f52cd7d70687f16c536f7d6e4834f75` |
| `mysql-flexible-server-overview.workbook` | `Workbooks/MySQL Flexible Server/Overview/Overview.workbook` | `52c8fa54d3cdeb5f48259921a5787bbac3349130af63c4b9aa918bca1ca81203` |
| `network-insights-natgatewayworkbooks-networkinsights-natgatewaymetrics.workbook` | `Workbooks/Network Insights/NatGatewayWorkbooks/NetworkInsights-NatGatewayMetrics-Minified/NetworkInsights-NatGatewayMetrics-Minified.workbook` | `779a73639826298c5a7e4f35662ea4386713d84e89c88fcac48d3c4bd69b03b0` |
| `postgresql-flexible-server-overview.workbook` | `Workbooks/PostgreSQL Flexible Server/Overview/Overview.workbook` | `d36bf7688f46708515a0fb0ee04aa57a03cf3b7ee6646307b015bd2c1e0a73cd` |
| `product-analytics-using-the-click-analytics-plugin-heart-analytics-sec.workbook` | `Workbooks/Product Analytics using the Click Analytics Plugin/HEART Analytics Section - Engagement/HEART Analytics Section - Engagement.workbook` | `032f6a33b58bfccb3664b6a1079ba7ba32480fb3f580039637b2220d0173d64c` |
| `resource-insights-rediscache.workbook` | `Workbooks/Resource Insights/RedisCache/ResourceInsights.workbook` | `8c99ecda16b0cfc49952f12a35774b92f27c616968da50f712d116813b308aef` |
| `scom-managed-instance-scom-detailed-insights.workbook` | `Workbooks/SCOM managed instance/SCOM detailed insights/SCOM detailed insights.workbook` | `57aac2cb03a69240abc38beac68f4658582e65c68ecba7d32a9aefaa708c6d04` |
| `sqldatabase-sql-database-metrics.workbook` | `Workbooks/SqlDatabase/SQL Database Metrics/SQL Database Metrics.workbook` | `cdc4af5eef64549991203be7015296fc3a603ab62632f8e1139957d156a1e0ff` |
| `storage-performance-blob-storage-classic-metrics.workbook` | `Workbooks/Storage-Performance/Blob Storage Classic Metrics/Blob Storage Classic Metrics.workbook` | `3989e1907102afdfd4b5986fc2463d229513f939bf58fa0a0d7f8c50b160f138` |
| `usage-segmented-funnel.workbook` | `Workbooks/Usage/Segmented Funnel/Segmented Funnel.workbook` | `18ba4552f3af61a24ab6c9ee3d3ba0a9f2f3bb211bf026effc9089a85b200cf7` |
| `workloads-sql-add-monitoring-virtual-machine.workbook` | `Workbooks/Workloads/SQL/Add monitoring virtual machine/Add monitoring virtual machine.workbook` | `d29ad82fd472f6ec876f87a4bceed439149053955330944eeb81e4034738acff` |
