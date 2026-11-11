# Azure metric and log names: sourced, not derived

Written 2026-11-11 from public sources fetched with `curl`. No Azure call was made and no New
Relic account was queried (the read-only New Relic MCP connection failed to start, and the research
did not need it). **Nothing here has been seen arriving in a New Relic account.** "VERIFIED" in this note means
"printed in a fetched primary source" (documentation or source code). It does not mean "seen in
data"; section 6 lists what only a live link can confirm.

Data file: `src/importer/sources/azure/name_table.json`, loader `name_table.py`, consumer
`names.py`. Reasons that used to say "name UNVERIFIED" now say "listed in the New Relic
documentation" for names in the file.

## 1. Sources

| Key | Source | Commit or date |
|---|---|---|
| NR-MET | `newrelic/docs-website` `src/content/docs/infrastructure/microsoft-azure-integrations/get-started/azure-integration-metrics.mdx` (raw.githubusercontent.com, branch `develop`) | fetched 2026-11-11 |
| NR-AM | same folder, `azure-integrations-list/azure-monitor.mdx` | same |
| NR-SVC | same folder, `azure-integrations-list/azure-<service>-monitoring-integration.mdx` (storage, vms, sql-database, app-service, application-gateway, key-vault, service-bus, event-hub, cosmos-db-document-db, functions, load-balancer, virtual-network, containers, redis-cache) | same |
| NR-LOG | `docs/logs/forward-logs/azure-log-forwarding.mdx`; `docs/logs/log-api/introduction-log-api.mdx` (NR-LOGAPI) | same |
| NR-FWD | `newrelic/newrelic-azure-functions` `LogForwarder/index.js` (master) | same |
| OTEL-S / OTEL-M | `open-telemetry/opentelemetry-collector-contrib` `receiver/azuremonitorreceiver/scraper.go`, `internal/metadata/metrics.go`, `README.md`, `metadata.yaml` (main) | same |
| L-MET | Microsoft Learn `azure-monitor/reference/supported-metrics/<provider-type>-metrics` (36 resource types fetched) | same |
| L-RES | Learn `azure-monitor/essentials/resource-logs-schema` | same |
| L-ACT | Learn `azure-monitor/essentials/activity-log-schema` (section "Schema from storage account and event hubs") | same |
| L-TBL | Learn `azure-monitor/reference/tables/{azureactivity,azurediagnostics,perf,heartbeat,syslog,event}` | same |
| L-EH | Learn `azure-monitor/essentials/stream-monitoring-data-event-hubs` | same |

The Azure `azure-rest-api-specs` repository holds no metric definitions, as expected; Learn is the
source for names, units, aggregations and dimensions. The `azureeventhubreceiver` and the Azure
encoding extension were not needed: the New Relic route does not use the Collector (section 4).

## 2. Routes and naming rules

### Route A: New Relic Azure Monitor integration (what the converter targets)

| Item | Rule | Status | Source |
|---|---|---|---|
| Metric name | resource type lower-cased, `/` to `.`, `Microsoft.` to `azure.`; plus `.` plus the Azure metric name with white space removed, case kept: `Microsoft.Compute/virtualMachines` + `Percentage CPU` is `azure.compute.virtualmachines.PercentageCPU` | VERIFIED (stated) | NR-MET |
| Names actually printed | 641 names in a "noncomprehensive" table; 359 of them join to a Learn metric (rows of `name_table.json`), the rest belong to types not fetched or to variants | VERIFIED | NR-MET + L-MET |
| Rule is complete | NOT established. Of 1292 Learn metrics for the 36 fetched types, 347 appear literally in the table; the rest follow the rule only by inference | UNVERIFIED | NR-MET |
| `/` in a metric name | Not stated. The table prints `DiskReadOperations.Sec` for the VM `Disk Read Operations/Sec` (slash to dot), but `DataDiskReadBytessec` for the scale set `Data Disk Read Bytes/sec` (slash dropped). Only the printed names are used; an unprinted name with `/` stays refused | VERIFIED per name, rule UNVERIFIED | NR-MET |
| Aggregation | One series per metric name. Data type is `summary`, `gauge` or `count` "depending on the data Azure returns". No per-aggregation suffix. Use `average()`, `sum()`, `min()`, `max()` on the name. `Count` is the Azure aggregation counting samples and the converter writes `count()` | name VERIFIED; which function equals which Azure aggregation UNVERIFIED | NR-AM |
| Unit | No conversion is documented. Legacy polling names carry a unit suffix equal to the Learn unit (checked on the joined rows: `Percent`, `Bytes`, `Milliseconds`, `Seconds`, `BytesPerSecond`, `CountPerSecond`). Scale 1.0 for every row | UNVERIFIED (no source says "unchanged") | NR-MET + L-MET |
| Dimensions | attribute `<type prefix>.<DimensionName>`, e.g. `azure.cache.redis.ShardId`. When the dimension name equals the metric name it is lower-cased: `azure.network.applicationgateways.tlsprotocol` | two examples VERIFIED; rest by inference UNVERIFIED | NR-MET |
| Same metric with and without a dimension | two series; filter with `IS NULL` / `IS NOT NULL` | VERIFIED | NR-MET |
| Standard attributes | `collector.name = 'azure-monitor'`, `instrumentation.provider = 'azure'`, `azure.region`, `azure.resourceId`, `azure.resourceType` | VERIFIED | NR-AM |
| Resource id casing | not stated anywhere. Azure returns mixed case in `scopes` and upper case in diagnostic-log `resourceId`; NRQL `=` is case sensitive | UNVERIFIED | NR-AM, L-RES |
| Resource tags | become attributes ("decorated with custom tags"); the attribute name format is not stated | UNVERIFIED | NR-AM |
| Entities | 29 services (list in `azure-datapath.md` section 2) | VERIFIED | NR-AM |

### Route B: older polling integrations (not generated)

Events named `Azure<Service><Thing>Sample` (list in section 3), `provider` value such as
`AzureStorageAccount`, attributes in camelCase with the unit as a suffix (`transactions`,
`availabilityPercent`, `successServerLatencyMilliseconds`). `name_table.json` stores the legacy
attribute for each row (`polling`) and the integration. NR-SVC. Deprecated in favour of Route A
(NR-AM).

### Route C: OpenTelemetry Collector `azure_monitor` receiver to OTLP

Read from code (OTEL-S, OTEL-M), VERIFIED as code, not seen in data:

- Metric name: `azure_` + lower-case(metric name, spaces to `_`) + `_` + lower-case(aggregation).
  One **gauge per aggregation** (`Average`, `Count`, `Maximum`, `Minimum`, `Total`), so
  `Percentage CPU` gives `azure_percentage_cpu_average`, `azure_percentage_cpu_maximum` and so on.
  The namespace is **not** in the name; two namespaces with the same metric name collide and are told
  apart only by the `type` attribute. A `/` is kept (`azure_disk_read_operations/sec_average`).
  `name_table.otel_metric_name()` implements this.
- Unit: the Azure unit string (`Percent`, `Bytes`, `Count`, `MilliSeconds`...) is the OTLP unit, values
  unchanged.
- Attributes on each point: `azuremonitor.resource_id` (the id exactly as the Resources List API
  returned it), `name`, `resource_group` (lower-cased), `type` (e.g. `Microsoft.Storage/storageAccounts`),
  `location`, `timegrain`, one `metadata_<dimension>` per dimension (dimension name as the metrics API
  returns it, case UNVERIFIED), one `tags_<tag>` per resource tag listed in `append_tags_as_attributes`.
  Optional resource attributes (off by default): `azuremonitor.subscription`,
  `azuremonitor.subscription_id`, `azuremonitor.tenant_id`.
- Storage sub-services (`blobServices`, `fileServices`, `queueServices`, `tableServices`) are scraped
  only when listed under `services`, as virtual resources with ids `.../blobServices/default`.
- A rule converted for Route A does not match Route C names. Pick one route per rule set.

### Route D: Event Hub diagnostic export

- Metrics (`AllMetrics`) arrive as records `{count, total, minimum, maximum, average, resourceId, time,
  metricName, timeGrain}` (L-EH, VERIFIED). The New Relic forwarder turns every record into a **Log**
  event (NR-FWD), so they are not `Metric` data and a metric alert cannot read them. Status: schema
  VERIFIED, behaviour in an account UNVERIFIED.
- Logs: section 5.

## 3. Per service: names for the estate and fixtures

"Listed" counts Learn metrics whose New Relic name is printed in NR-MET; "Learn" is all metrics of
the type on L-MET. "Polling event" is from NR-SVC. Status of every listed row: **VERIFIED (docs)**;
every unlisted metric: **UNVERIFIED (derived)**.

| Service | Azure type (alert `metricNamespace`) | Name prefix | Listed / Learn | Polling event | Notes |
|---|---|---|---|---|---|
| Storage account | `Microsoft.Storage/storageAccounts` | `azure.storage.storageaccounts` | 7 / 11 | `AzureStorageAccountSample` | Listed: `Availability`, `Egress`, `Ingress`, `SuccessE2ELatency`, `SuccessServerLatency`, `Transactions`, `UsedCapacity` (unit Count/Percent/Bytes/MilliSeconds; `UsedCapacity` Average only). Learn also lists `ContainerBlobCount`, `ContainerUsedSize`, ... (not printed, derived). Dimensions `ApiName`, `GeoType`, `Authentication`, `ResponseType`, `TransactionType` -> `azure.storage.storageaccounts.ApiName` ... (UNVERIFIED) |
| Storage blob, file, queue, table | `.../storageAccounts/blobServices` (and file, queue, table) | `azure.storage.storageaccounts.blobservices` ... | 9 / 11, 9 / 22, 9 / 9, 9 / 9 | same event | `BlobCapacity`, `BlobCount`, `ContainerCount`, `Transactions`, latencies per service. Account-level `Transactions` and blob-level `Transactions` are different names |
| Virtual machine | `Microsoft.Compute/virtualMachines` | `azure.compute.virtualmachines` | 7 / 64 | `AzureVirtualMachineSample` | Listed: `PercentageCPU`, `NetworkIn`, `NetworkOut`, `DiskReadBytes`, `DiskWriteBytes`, `DiskReadOperations.Sec`, `DiskWriteOperations.Sec`. Not listed: `Available Memory Bytes`, `CPU Credits ...`, the 50 disk burst and latency metrics. Guest metrics (`\Processor(_Total)\% Processor Time`) are custom namespace data and out of scope |
| VM scale set | `Microsoft.Compute/virtualMachineScaleSets` | `azure.compute.virtualmachinescalesets` | 27 / 64 | (none in NR-SVC) | slash names print without the dot (`DataDiskReadBytessec`) |
| SQL database | `Microsoft.Sql/servers/databases` | `azure.sql.servers.databases` | 18 / 58 | `AzureSqlDatabaseSample` | Underscores kept (`cpu_percent`, `dtu_consumption_percent`). Not listed: `sql_instance_cpu_percent`, `tempdb_*`, `wlg_*`. Table also has `dw_cpu_percent` (no Learn row) |
| SQL elastic pool, managed instance | `Microsoft.Sql/servers/elasticpools`, `Microsoft.Sql/managedInstances` | `azure.sql.servers.elasticpools`, `azure.sql.managedinstances` | 12 / 27, 7 / 10 | `AzureSqlElasticPoolSample` | |
| App Service, Functions | `Microsoft.Web/sites` | `azure.web.sites` | 22 / 56 | `AzureAppServiceWebAppSample`, `AzureFunctionsAppSample` | Listed names are the plain ones (`Http5xx` ...) **and** the table prints `...byWebApp` / `...byFunctionsApp` variants for five metrics; which one an alert reads is UNVERIFIED. Functions and web apps share the namespace |
| App Service plan | `Microsoft.Web/serverfarms` | `azure.web.serverfarms` | 6 / 20 | `AzureAppServicePlanSample` | `CpuPercentage`, `MemoryPercentage`, `DiskQueueLength`, `HttpQueueLength`, `BytesReceived`, `BytesSent` |
| AKS | `Microsoft.ContainerService/managedClusters` | `azure.containerservice.managedclusters` | 5 / 27 | `AzureContainersManagedClusterSample` | Only `kube_node_status_*` and `kube_pod_status_*` are printed. `node_cpu_usage_percentage` and the others stay derived (UNVERIFIED). Prometheus metrics: other route |
| Application Gateway | `Microsoft.Network/applicationgateways` | `azure.network.applicationgateways` | 25 / 35 | `AzureApplicationGatewaySample` | `UnhealthyHostCount`, `HealthyHostCount`, `TotalRequests`, `FailedRequests`, `Throughput`, `CpuUtilization`... Metric `TlsProtocol` with dimension `TlsProtocol` gives `azure.network.applicationgateways.tlsprotocol` |
| Key Vault | `Microsoft.KeyVault/vaults` | `azure.keyvault.vaults` | 5 / 5 | `AzureKeyVaultVaultSample` | all five listed: `Availability`, `SaturationShoebox`, `ServiceApiHit`, `ServiceApiLatency`, `ServiceApiResult` |
| Service Bus | `Microsoft.ServiceBus/Namespaces` | `azure.servicebus.namespaces` | 3 / 27 | `AzureServiceBusNamespaceSample`, `...QueueSample`, `...TopicSample`, `...SubscriptionSample` | The table prints `ActiveMessages.byNamespace`, `.byQueue`, `.byTopic` (3 variants each), never the plain name; the plain name stays derived. Which variant a namespace-level alert reads: UNVERIFIED |
| Event Hub | `Microsoft.EventHub/Namespaces` | `azure.eventhub.namespaces` | 17 / 38 | `AzureEventHubNamespaceSample`, `AzureEventHubClusterSample` | `IncomingMessages`, `OutgoingMessages`, `ThrottledRequests`, `ServerErrors`, `Size`, ... Legacy short names (`INMSGS`, `SUCCREQ`) not listed |
| Cosmos DB | `Microsoft.DocumentDB/DatabaseAccounts` | `azure.documentdb.databaseaccounts` | 0 / 108 | `AzureCosmosDbAccountSample` | Only `.byAccount`, `.byDatabase`, `.byCollection` variants are printed |
| Load balancer | `Microsoft.Network/loadBalancers` | `azure.network.loadbalancers` | 8 / 9 | `AzureLoadBalancerSample` | `VipAvailability`, `DipAvailability`, `ByteCount`, `PacketCount`, `SYNCount`; `GlobalBackendAvailability` not listed |
| Public IP, virtual network, NIC | `Microsoft.Network/publicIPAddresses`, `.../virtualNetworks`, `.../networkinterfaces` | `azure.network.publicipaddresses` ... | 21 / 29, 2 / 24, 0 / 20 | `AzureVirtualNetworksPublicIpAddressSample` | |
| Redis | `Microsoft.Cache/redis` | `azure.cache.redis` | 32 / 206 | `AzureRedisCacheSample`, `AzureRedisCacheShardSample` | Printed in lower case (`cachehits`, `usedmemory`) plus a `...0` twin (meaning not stated); shard dimension `azure.cache.redis.ShardId` |
| PostgreSQL / MySQL | `Microsoft.DBforPostgreSQL/flexibleServers`, `Microsoft.DBforMySQL/flexibleServers` | table prints `azure.dbforpostgresql.servers.*`, `azure.dbformysql.servers.*` (single server types) | 0 / 75, 0 / 55 | | flexible server names are not printed; stay derived |

Per-row unit, aggregations and dimension names (from L-MET) and the legacy polling attribute are in
`name_table.json`.

### Unit and aggregation notes (Average, Total, Count, Min, Max)

- Azure `timeAggregation` `Average`, `Minimum`, `Maximum` map to `average()`, `min()`, `max()` of the
  metric name; `Total` to `sum()`; `Count` is `count()` of the samples (as `metric.py` writes). All
  four New Relic functions exist on a `summary` metric; whether a polled series carries min and max
  (rather than only a last value as a `gauge`) is UNVERIFIED and is the first thing to check live.
- Many storage metrics list only some aggregations (`UsedCapacity`: Average only; `Transactions`:
  Total). An Azure rule on an aggregation the metric does not list cannot exist, so the converter
  needs no check; the row's `aggregations` field exists for a later guard.
- Threshold scaling: no source documents a unit conversion, so every `scale` is 1.0 and the
  threshold is carried unchanged (percent stays 0 to 100, bytes stay bytes, latencies stay in the
  Azure unit: `SuccessServerLatency` is `MilliSeconds`). The loader and `MetricName.scale` exist so
  a confirmed factor can be added to a row without code changes. No threshold-scaling fix was found.

## 4. Why the Collector log receivers were not needed

The documented New Relic log route is the Azure Function forwarder (NR-LOG, NR-FWD), not a
Collector. The Collector `azureeventhubreceiver` is a separate way to read the same hub and its
output would be shaped by the OTel Azure encoding extension, not by the attribute names below.
Neither was used as a basis for the names in section 5.

## 5. Logs

### What the forwarder does (NR-FWD, VERIFIED as code)

- Reads each Event Hub message; if it has a `records` array (or is an array of such messages), each
  record becomes one log entry; plain strings become `{message: ...}`.
- Adds to every entry: `plugin.type = azure` (`SELECT * FROM Log WHERE plugin.type = 'azure'`),
  `plugin.version`, `azure.forwardername`, `azure.invocationid`, `tags.*` from the `NR_TAGS` setting.
- Adds per record: `timestamp` (parsed from `time` or `timeStamp`), and when `resourceId` starts with
  `/subscriptions/`: `metadata.subscriptionId`, `metadata.resourceGroup`, `metadata.source`, all
  **lower-cased**; `source` is the provider with `microsoft.` replaced by `azure.` (`azure.storage`).
- Nested objects are stored with dotted names (NR-LOGAPI example `user.id`), so `properties.statusCode`.
  That the record's own top-level keys become attributes is UNVERIFIED in an account (the code posts
  them as the log object; the Logs API documents `attributes` and `message` as the usual carriers).

### Resource logs and AzureDiagnostics (L-RES, L-TBL)

| Log Analytics column (AzureDiagnostics) | Event Hub record field -> New Relic attribute | Status |
|---|---|---|
| `TimeGenerated` | `time` (and `timestamp` set by the forwarder) | VERIFIED |
| `ResourceId` | `resourceId` (UPPER case in the Learn samples) | VERIFIED field, casing varies, compare case-insensitively |
| `OperationName` | `operationName` | VERIFIED |
| `Category` | `category` | VERIFIED |
| `ResultType` | `resultType` (Started, Succeeded, Failed, ...) | VERIFIED |
| `ResultSignature` | `resultSignature` (HTTP status for REST calls) | VERIFIED |
| `ResultDescription`, `DurationMs`, `CallerIPAddress`, `CorrelationId`, `Level`, `Location` | `resultDescription`, `durationMs`, `callerIpAddress`, `correlationId`, `level` (Informational, Warning, Error, Critical), `location` | VERIFIED |
| `<field>_s`, `<field>_d` (e.g. `requestUri_s`, `httpStatus_d`) | `properties.<field>`; the Log Analytics type suffix does not exist in the record | field path UNVERIFIED |
| `SubscriptionId`, `ResourceGroup`, `ResourceProvider` | `metadata.subscriptionId`, `metadata.resourceGroup`, `metadata.source` (forwarder, lower case) | VERIFIED as code |

Resource-specific tables (`StorageBlobLogs`, `AGWAccessLogs`, ...) are a Log Analytics concept;
the hub record has the same shape either way. A KQL translator should therefore rewrite both
`AzureDiagnostics | where Category == "X"` and a resource-specific table to
`FROM Log WHERE plugin.type = 'azure' AND category = 'X'` (category value case as Azure writes it).

### Activity Log (L-ACT mapping table, VERIFIED as documentation)

| `AzureActivity` column | Event Hub / New Relic attribute | Status |
|---|---|---|
| `TimeGenerated`, `EventSubmissionTimestamp` | `time` | VERIFIED |
| `ResourceId` (and `SubscriptionId`, `ResourceGroup`, resource type, inferred from it) | `resourceId`; `metadata.subscriptionId`, `metadata.resourceGroup`, `metadata.source` | VERIFIED |
| `OperationNameValue` | `operationName` | VERIFIED |
| `CategoryValue` | `properties.eventCategory` (when absent the category is "Administrative"; the page says "Always Administrative" yet its own sample has `category: "Write"`: test both) | VERIFIED, page inconsistent |
| `ActivityStatusValue` | `resultType` (Started, Succeeded, Failed) | VERIFIED |
| `ActivitySubstatusValue` | `resultSignature` | VERIFIED |
| `Description` | `resultDescription` | VERIFIED |
| `CallerIpAddress` | `callerIpAddress` | VERIFIED |
| `CorrelationId` | `correlationId` | VERIFIED |
| `Level` | `level` | VERIFIED |
| `Authorization` | `identity.authorization.action`, `.scope`, `.evidence.*` | VERIFIED |
| `Claims` | `identity.claims.*` | VERIFIED |
| `Caller` | no field. A user's name is the claim `identity.claims.http://schemas.xmlsoap.org/ws/2005/05/identity/claims/upn` (dots inside the claim URL: needs backticks in NRQL); a service principal has `identity.authorization.evidence.principalId` | UNVERIFIED |
| `OperationId`, `EventDataId` | `properties.operationId`, `properties.eventName` | VERIFIED / UNVERIFIED |
| `Properties` | `properties.*` (sample: `properties.statusCode`, `properties.serviceRequestId`) | VERIFIED |
| `HTTPRequest` | only `callerIpAddress` survives | UNVERIFIED |

Activity log categories forwarded by the ARM template: Administrative, Alert, Autoscale, Policy,
Recommendation, Resource Health, Security, Service Health (NR-LOG; all default `false`). Severity:
Critical, Error, Warning, Informational (L-ACT). The `status`/`substatus` strings are those of the
Activity Log (`Succeeded`, `Failed`, `Started`).

### Agent tables (Perf, Heartbeat, Syslog, Event)

The Azure Monitor agent writes these to a Log Analytics workspace. No fetched source shows them
leaving the workspace for New Relic, so as a route this is UNVERIFIED (`name_table.json`
`logs.agent_tables` holds the column lists from L-TBL). A translator should treat these tables as
needing the New Relic infrastructure agent or an OTel Collector on the VM:

| Table | Key columns | Closest New Relic signal (UNVERIFIED mapping) |
|---|---|---|
| `Perf` | `Computer`, `ObjectName`, `CounterName`, `InstanceName`, `CounterValue` | `SystemSample` (`cpuPercent`, `memoryUsedPercent`, `diskUsedPercent`) or OTel `hostmetrics`; map per counter |
| `Heartbeat` | `Computer`, `TimeGenerated`, `OSType`, `Version`, `ResourceId` | loss of signal on `SystemSample` faceted by `hostname` |
| `Syslog` | `Computer`, `Facility`, `SeverityLevel`, `SyslogMessage`, `ProcessName`, `HostName` | `Log` from the infrastructure agent `syslog` source or the OTel `syslog` receiver |
| `Event` | `Computer`, `EventLog`, `EventID`, `EventLevelName`, `Source`, `RenderedDescription` | `Log` from the infrastructure agent `winlog` source (collect or exclude event ids) or OTel `windowseventlog` |

## 6. What only a live New Relic Azure link can confirm

Linking needs the service principal credentials stored in New Relic; that is the owner's decision.
Nothing below was attempted.

1. That the names printed in NR-MET really arrive (all 359 rows are documentation, not data), and
   which of the unprinted Learn metrics (AKS `node_*`, VM disk and burst metrics, Cosmos DB, Service
   Bus plain names) follow the derived rule.
2. The exact `azure.resourceId` value and its casing, and whether `azure.resourceType` and
   `azure.region` are present on every series. The converter filters with an exact, case-sensitive
   match on the scope string from the alert.
3. Which NRQL function equals which Azure aggregation: whether a polled series is `summary`
   (min, max, sum, count available) or a plain `gauge`, and what `sum()` means for a `Total` metric
   across polling intervals.
4. Units: that percent is 0 to 100, latency is milliseconds and bytes are bytes with no conversion
   (every `scale` is 1.0 on that assumption).
5. Dimension attribute names and case (`azure.storage.storageaccounts.ApiName`?), tags as attributes,
   and the `.byWebApp`, `.byQueue`, `.byAccount` variants.
6. Whether metrics sent through an Event Hub diagnostic setting are usable (they should land as Log).
7. Whether the forwarder's top-level record keys (`operationName`, `category`, `resultType`,
   `properties.*`) are stored as attributes under those exact names, and their case.
8. How long the first data takes and the polling delay that decides the alert window.
