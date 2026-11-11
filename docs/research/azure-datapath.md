# Azure data path: how Azure telemetry reaches New Relic

Research for an Azure source. Written 2026-11-11 from public sources only. No Azure tenant, no New
Relic account and no API of either was called. All sources were fetched with `curl` on 2026-11-11.

A converted alert reads data. If the data is not in New Relic the condition never fires, so this
note lists the routes, the names the data gets, the credentials each route needs, and what this
tool could generate offline for a person to deploy.

Evidence marks:

- NR k: New Relic `docs-website` MDX (key below), commit `1e2c595ef12c92dfef455ef25dd6d819065dad52`.
- OTEL: README of the upstream OpenTelemetry Collector contrib `azuremonitorreceiver` (main branch, commit `c64840337cf400792cb684037ed998428b268197`).
- NRFN: the `newrelic/newrelic-azure-functions` ARM template (master, commit `1200f1dc52d9ecf6d8cc2d01f846ba378dd6f7d2`).
- LEARN k: Microsoft Learn page (key below).
- REPO file: this repository.
- UNVERIFIED: memory or inference, not confirmed in a fetched source.

| Key | URL |
|---|---|
| NR-ACT | https://raw.githubusercontent.com/newrelic/docs-website/1e2c595ef12c92dfef455ef25dd6d819065dad52/src/content/docs/infrastructure/microsoft-azure-integrations/get-started/activate-azure-integrations.mdx |
| NR-AM | .../src/content/docs/infrastructure/microsoft-azure-integrations/azure-integrations-list/azure-monitor.mdx |
| NR-MET | .../src/content/docs/infrastructure/microsoft-azure-integrations/get-started/azure-integration-metrics.mdx |
| NR-VS | .../src/content/docs/infrastructure/microsoft-azure-integrations/get-started/azure-monitor-vs-azure-polling.mdx |
| NR-NAT | .../src/content/docs/infrastructure/microsoft-azure-integrations/get-started/azure-native.mdx |
| NR-POLL | .../src/content/docs/infrastructure/microsoft-azure-integrations/getting-started/polling-intervals-azure-integrations.mdx |
| NR-SQL | .../src/content/docs/infrastructure/microsoft-azure-integrations/azure-integrations-list/azure-sql-database-monitoring-integration.mdx |
| NR-APP | .../src/content/docs/infrastructure/microsoft-azure-integrations/azure-integrations-list/azure-app-service-monitoring-integration.mdx |
| NR-LOG | .../src/content/docs/logs/forward-logs/azure-log-forwarding.mdx |
| NR-RW | .../src/content/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration.mdx |
| NR-OTLP | .../src/content/docs/opentelemetry/best-practices/opentelemetry-otlp.mdx |
| OTEL | https://raw.githubusercontent.com/open-telemetry/opentelemetry-collector-contrib/main/receiver/azuremonitorreceiver/README.md |
| NRFN | https://raw.githubusercontent.com/newrelic/newrelic-azure-functions/master/armTemplates/azuredeploy-eventhubforwarder.json |
| L-DIAG | https://learn.microsoft.com/en-us/azure/azure-monitor/platform/diagnostic-settings |
| L-AKS | https://learn.microsoft.com/en-us/azure/azure-monitor/containers/kubernetes-monitoring-enable |
| L-AMW | https://learn.microsoft.com/en-us/azure/azure-monitor/metrics/azure-monitor-workspace-overview |
| L-PRICE | https://azure.microsoft.com/en-us/pricing/details/monitor/ |

The `...` stands for the `docs-website` raw base given in NR-ACT.

## 1. Answers first

- There are four routes: the New Relic Azure Monitor integration (polling the Azure Monitor metrics
  API), the older per-service polling integrations, log forwarding through an Event Hub, and the
  OpenTelemetry Collector. AKS managed Prometheus has no documented export to New Relic; the
  working route is a second collection path with Prometheus remote write.
- "Azure Monitor metric streams" is not an Azure product name. I found no page in the fetched New
  Relic docs that describes streaming Azure metrics to New Relic through an Event Hub. The Azure
  mechanism for streaming platform metrics to an Event Hub is a diagnostic setting with the
  `AllMetrics` category (LEARN L-DIAG). What New Relic does with metrics arriving that way is
  UNVERIFIED; the New Relic Event Hub forwarder is documented for logs (NR-LOG). A search of the whole
  `docs-website` repository was not possible (the unauthenticated code search is not available), so
  "not documented" means "not found in the pages I fetched".
- The Azure Monitor integration is the one New Relic recommends. NR-VS: "Use Azure Monitor to collect
  Azure metrics at scale. Azure Polling will be deprecated soon." It polls every 1 minute at best,
  against 5 minutes for polling.
- The integrations cannot collect data from Azure Government or from classic deployment model
  resources (NR-ACT, "We cannot get data from Azure resources that: Are located in Azure
  Government; Were created through the classic deployment model").
- New Relic needs a service principal with `Reader` at subscription scope, and `Monitoring Reader`
  at subscription scope to use the metrics batch API (NR-ACT steps 4 and 9). That is the same
  read-only principal this tool needs (see `docs/user/azure-credentials.md`), but a different
  principal is a better idea (section 6).
- A converted metric alert reads `Metric` rows named by a documented rule (section 3). A converted
  activity log or KQL alert reads `Log` rows, which exist only if the Event Hub forwarder and
  diagnostic settings are deployed (section 4).

## 2. Route A: Azure Monitor integration (recommended)

What it does (NR-AM): one integration for all resources that Azure Monitor supports; metrics arrive
as dimensional metrics; entities are created for a list of 29 services; custom metrics are not
supported; settings are the metric polling interval (minimum 1 minute), the metadata and tags
polling interval, and filters (`Limit to resource types`, `Limit to resource group`, `Resource tags
to include`, `Resource tags to exclude`, the last wins).

How it is switched on (NR-ACT):

1. Take the subscription id and tenant id from `az account show` (fields `id` and `tenantId`).
2. Register an app (NR recommends the name `NewRelic-Integrations`; Redirect URI type Web with
   `https://newrelic.com`), copy the Application (client) ID.
3. Create a client secret (up to 2 years; copy the value once).
4. In the subscription: `Resource providers`, find `microsoft.insights`, register it if
   `NotRegistered`. Then Access control (IAM): add role `Reader`, then role `Monitoring Reader`, both
   to the app, `Monitoring Reader` at subscription level. With it New Relic uses the Azure Monitor
   metrics batch API instead of per-resource calls.
5. In New Relic: `Infrastructure > Azure > Add an Azure account`, enter subscription id, tenant id,
   application id, client secret, then enable the `Azure Monitor` integration.
6. Rotate the secret before expiry in the same UI or through the Cloud Integrations API (NR-ACT).
   Unlinking needs the user who registered it or an administrator.

Automation exists: NR-ACT points at the Terraform "cloud integrations guide" on the Terraform
registry, and at the NerdGraph Cloud Integrations API tutorial
(`/docs/apis/nerdgraph/examples/nerdgraph-cloud-integrations-api-tutorial/`). I did not fetch either.
The Terraform resource names for Azure linking are UNVERIFIED (from memory they are
`newrelic_cloud_azure_link_account` and `newrelic_cloud_azure_integrations`); read the guide before
generating them.

Names in New Relic (NR-MET, NR-AM):

| Item | Rule or value |
|---|---|
| Data type | dimensional `Metric` (types such as summary, gauge, count) |
| Metric name | resource type lowercased, `/` becomes `.`, `Microsoft.` becomes `azure.`; Azure metric name keeps its casing without spaces. `Microsoft.Compute/virtualMachines` + `Percentage CPU` becomes `azure.compute.virtualmachines.PercentageCPU`; `Microsoft.Network/loadBalancers` + `ByteCount` becomes `azure.network.loadbalancers.ByteCount` |
| If the result lacks the `azure.` prefix | it is prepended |
| Dimensions | attributes named with the resource type prefix, e.g. `azure.cache.redis.ShardId`; a dimension whose name equals the metric name is uncapitalised (`azure.network.applicationgateways.tlsprotocol`) |
| Same metric with and without a dimension | two time series; filter with `IS NULL` or `IS NOT NULL` or `sum()` and `count()` double count |
| Standard attributes | `collector.name` = `azure-monitor`, `instrumentation.provider` = `azure`, `azure.region`, `azure.resourceId`, `azure.resourceType` |
| Tags | Azure resource tags become attributes |
| Entities | created for App Service Plans, Flexible DB for MySQL and PostgreSQL, Function Apps, Load Balancer, Redis, Storage Accounts, SQL Databases, SQL Elastic Pools, Service Bus, Virtual Machines, Virtual Networks (and NICs, public IPs), API Management, App Gateways, Containers, Data Factory, Event Hubs, Express Routes, Firewalls, Front Doors, Key Vaults, Logic Apps, Machine Learning, Power BI Dedicated, SQL Managed Instances, VM Scale Sets, VPN Gateways |

Querying (NR-AM): `FROM Metric SELECT count(*) WHERE collector.name = 'azure-monitor' TIMESERIES SINCE 1 hour ago`.
For alert conditions use the event timer streaming method because Azure Monitor delivers with delay
(NR-AM, same advice as in `docs/research/newrelic-notifications-and-nrql-reference.md`).

Migration trap (NR-AM): enabling the integration creates new entities; the polling entities stay for
24 hours; both can run together and then duplicate metric names appear; separate them with
`collector.name`. Dashboards and alerts built on polling entities need to be re-pointed.

Cost (NR-AM): Azure bills metric queries per API call (Azure "Metric queries" pricing item); calls
scale with the number of monitored resources; the batch API reduces them. Dollar figures: not
checked (see section 7).

## 3. Route B: older polling integrations

About 30 services, 5-minute minimum interval, one integration per service (NR-VS). Data arrives as
events named `Azure<Service><Thing>Sample`: for example `AzureSqlDatabaseSample`,
`AzureSqlElasticPoolSample`, `AzureSqlServerSample` (NR-SQL), `AzureAppServiceWebAppSample`,
`AzureAppServicePlanSample`, `AzureAppServiceHostNameSample` (NR-APP). NR-MET has a table that
maps each polling attribute to the Azure Monitor metric name, for example
`azure.apimanagement.service.Capacity` to `capacityPercent`. That table is the bridge when a
converted rule targets an attribute that exists only in one route. NR-VS lists the known weaknesses:
metrics are hard coded, coverage is limited, timestamps are tracked per resource, and the route will
be deprecated. This tool should not generate new polling set-ups.

## 4. Route C: log forwarding through an Event Hub

Source: NR-LOG and the ARM template NRFN.

What it does: an Azure Function consumes an Event Hub and posts to the New Relic Logs API. Anything
that can write to the hub arrives as `Log` events. Filter Azure-origin logs with
`SELECT * FROM Log WHERE plugin.type = 'azure'` (NR-LOG).

Subscription activity logs: optional categories in the template are Administrative, Alert,
Autoscale, Policy, Recommendation, Resource Health, Security and Service Health (NR-LOG; NRFN
parameters `forward<Category>AzureActivityLogs`, all default `false`).

Resource logs and metrics: the template does not create diagnostic settings for resources. Each
resource needs its own diagnostic setting that streams to the hub (NR-LOG; LEARN L-DIAG). Facts from
L-DIAG: the destination Event Hub must be in the same region as a regional resource; streaming needs
`Manage`, `Send` and `Listen` rights on the namespace authorization rule; a compacted hub does not
work; with virtual networks the "Allow trusted Microsoft services" firewall option is required;
`AllMetrics` sends platform metrics. Platform metrics sent to a Log Analytics workspace this way lose
dimensions (flattened); whether the same holds for an Event Hub is UNVERIFIED.

The template deploys (NRFN, `resources` types): Event Hub namespace, hub, consumer group and
authorization rules, a storage account and blob container, an App Service plan (`Microsoft.Web/serverfarms`)
and the Function App, and optionally a virtual network, private DNS zones and private endpoints. The
resource list was read from the file; the full deployment was not run.

Parameters (NRFN, `parameters`): `newRelicLicenseKey` (required, secret), `authenticationMode`
(`Local Authentication` or `Managed Identity`, default local), `eventHubNamespace`, `eventHubName`,
`location`, `newRelicEndpoint` (default `https://log-api.newrelic.com/log/v1`), `logCustomAttributes`,
`maxEventBatchSize` (500), `minEventBatchSize` (20), `maxWaitTime` (`00:00:30`),
`maxRetriesToResendLogs` (3), `retryInterval` (2000), `scalingMode` (`Basic`, `Enterprise`, `Flex`),
the eight `forward...AzureActivityLogs` flags, `disablePublicAccessToStorageAccount`.

Caveats: the forwarder is an Azure Function that costs money in the owner's subscription (cost not
checked); function invocation fails for blobs above about 105 MB (NR-LOG); the blob forwarder resends
the whole file when a blob changes (NR-LOG). The attribute names of a forwarded record in the `Log`
event are UNVERIFIED; a first capture must be inspected before any KQL conversion relies on them.

## 5. Route D: OpenTelemetry Collector and Prometheus

### 5.1 `azure_monitor` receiver

OTEL: the contrib Collector has an `azure_monitor` receiver that scrapes the Azure Monitor API for
resource metrics. Stability is alpha for metrics. Settings: `subscription_ids` or
`discover_subscriptions`, `resource_groups`, `resource_tags`, `services`, `metrics`,
`collection_interval`, `use_batch_api` (experimental), `cloud` (`AzureCloud`, `AzureUSGovernment`,
`AzureChinaCloud`), `dimensions`, `append_tags_as_attributes`, and authentication by service
principal (`tenant_id`, `client_id`, `client_secret`), workload identity, managed identity or default
credentials (or the `azureauth` extension). The README says the receiver "adds the prefix `azure_` to all
scraped metrics"; the rest of the naming (how a name such as `Percentage CPU` is written, and the
aggregation suffix) is UNVERIFIED.
Guest OS metrics from the Azure Monitor Agent live in custom namespaces and need an explicit
`metrics` key (OTEL).

This route works in Azure Government (the `cloud` setting), which the New Relic integrations do not.
It sends over OTLP. Endpoints (NR-OTLP): US `https://otlp.nr-data.net` (ports 443, 4317, 4318), EU
`https://otlp.eu01.nr-data.net`, and a FedRAMP endpoint `https://gov-otlp.nr-data.net`; the header
is `api-key` with the licence key (NR-OTLP). The metric names differ from Route A, so rules converted
for Route A do not match; a rule set must be generated for one route.

### 5.2 AKS managed Prometheus

L-AKS and L-AMW: managed Prometheus stores AKS metrics in an Azure Monitor workspace (limits: 1
million active time series and 1 million events per minute by default, a PromQL query spans at most
32 days). Onboarding needs at least Contributor on the cluster. The fetched pages describe Grafana
and the workspace as consumers. They do not describe a remote write to a third party. Therefore: the
route to New Relic is a Prometheus (or Prometheus Operator, or Collector Prometheus receiver) in the
cluster with `remote_write` to New Relic, running next to the managed add-on (UNVERIFIED that both
can coexist on the same cluster without conflict).

New Relic remote write (NR-RW): URL `https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=<NAME>`
(EU `https://metric-api.eu.newrelic.com/prometheus/v1/write`, JP `https://metric-api.jp.nr-data.net/prometheus/v1/write`);
the credential is an Ingest - License key as the bearer token or `X-License-Key`; the `prometheus_server`
value becomes an attribute that identifies the source. Data shows in query dashboards within about
5 minutes (NR-RW).

Prometheus rule groups converted from Azure (see `azure-monitor-alerting.md` 4.7) only make sense
on this route, with the metric names as they exist in New Relic, not in the Azure Monitor workspace.

## 6. Credentials New Relic needs, summarised

| Route | Credential | Where it lives | Minimum Azure role |
|---|---|---|---|
| A, B | service principal (tenant id, client id, client secret, subscription id) | entered once in New Relic | `Reader` and `Monitoring Reader`, subscription scope (NR-ACT) |
| C | New Relic licence key in the template; Event Hub shared key or managed identity (`Azure Event Hubs Data Receiver` on the namespace for managed identity, NR-LOG) | Function App settings | whoever deploys needs rights to create the resources and role assignment (not stated on the page) |
| D receiver | service principal or managed identity | Collector config / environment | read on metrics (the OTEL README does not name a role; `Monitoring Reader` is UNVERIFIED but consistent with NR-ACT) |
| D remote write | New Relic Ingest - License key | Prometheus secret (`kubectl create secret ...` in NR-RW) | none in Azure beyond cluster rights |

Use a separate principal for New Relic polling and for this migration tool: they have different
lifetimes, and revoking one then does not stop the other.

## 7. What this tool could generate offline (a data path plan)

Input: the rules and action groups from the alerting extraction (which resource types, metric
namespaces, log categories, KQL tables, Prometheus metrics each converted condition reads).
Output, as files for a person to review and deploy; nothing is created by the tool:

1. A report table `rule, data it reads, route that provides it, status`. Status values: `provided by
   Azure Monitor integration (name rule)`, `needs Event Hub forwarding`, `needs OpenTelemetry or
   remote write`, `not available (custom metric, Application Insights, Azure Government)`.
2. A list of Azure resource types to allow-list in the Azure Monitor integration (`Limit to resource
   types`), derived from the metric alert scopes and `targetResourceType`.
3. The metric name for each criterion by the rule in section 2, marked "not confirmed" until a data
   check sees it arrive (repo rule: never guess names).
4. For log rules: the diagnostic settings needed and the forwarder parameters (below).
5. A cost note with the units (API calls, function invocations, Event Hub throughput), no dollars
   unless a price page was fetched.

Pricing page status: L-PRICE was fetched, but every price in its HTML is a placeholder (`$-`); the
amounts are rendered by script. Dollar costs of the forwarder, of Event Hub, of Azure Monitor metric
queries and of alert rules are therefore **not checked**.

### 7.1 Forwarder deployment (Azure CLI, commented template)

The template is a JSON ARM template, so Bicep cannot import it as a module from a URL; deploy it
directly. Secrets are placeholders and must come from the environment, never from a file in git.

```bash
# DATAPATH PLAN (generated, not executed). Review every value before running.
# Source of the template: newrelic/newrelic-azure-functions, armTemplates/azuredeploy-eventhubforwarder.json
# Creates: Event Hub namespace and hub, storage account, App Service plan, Function App (costs money).
# NEWRELIC_INGEST_KEY: a New Relic Ingest - License key, read from the environment.
az deployment group create \
  --resource-group "<NEW_RESOURCE_GROUP>" \
  --template-uri "https://raw.githubusercontent.com/newrelic/newrelic-azure-functions/master/armTemplates/azuredeploy-eventhubforwarder.json" \
  --parameters newRelicLicenseKey="$NEWRELIC_INGEST_KEY" \
               newRelicEndpoint="https://log-api.newrelic.com/log/v1" \
               authenticationMode="Managed Identity" \
               scalingMode="Basic" \
               forwardAdministrativeAzureActivityLogs=true \
               forwardAlertAzureActivityLogs=true \
               forwardResourceHealthAzureActivityLogs=true \
               forwardServiceHealthAzureActivityLogs=true
# EU accounts: newRelicEndpoint must be the EU Logs API endpoint (see the New Relic log API docs; not fetched here).
```

(`az deployment group create --template-uri` and `--parameters` are standard; their reference page
was not fetched, so the exact flags are UNVERIFIED.)

### 7.2 Diagnostic setting per resource (Bicep, from Learn)

The Bicep shape below is the Learn example (`Microsoft.Insights/diagnosticSettings@2021-05-01-preview`,
L-DIAG) with the Event Hub properties added. Property names `eventHubAuthorizationRuleId` and
`eventHubName` are from memory and UNVERIFIED in the fetched page (which shows the CLI flags
`--event-hub-rule` and `--event-hub`). Check them against the Learn page before use.

```bicep
// DATAPATH PLAN (generated, not deployed). One setting per monitored resource.
param scope string                       // resource id of the monitored resource (placeholder)
param settingName string = 'newrelic-forward'
param eventHubAuthorizationRuleId string // .../namespaces/<NAMESPACE>/authorizationrules/<RULE>
param eventHubName string                // the hub the New Relic forwarder reads

resource diag 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  name: settingName
  scope: scope   // NOTE: Bicep wants a resource symbol here, not a string; adapt (UNVERIFIED)
  properties: {
    eventHubAuthorizationRuleId: eventHubAuthorizationRuleId
    eventHubName: eventHubName
    metrics: [
      { category: 'AllMetrics', enabled: true }
    ]
    logs: [
      { categoryGroup: 'allLogs', enabled: true }   // not every service has category groups (L-DIAG)
    ]
  }
}
```

The equivalent CLI (L-DIAG, VERIFIED shape) is `az monitor diagnostic-settings create --name ... --resource <id>
--metrics '[{"category":"AllMetrics","enabled":true}]' --event-hub-rule <rule id> --event-hub <hub>`.
The L-DIAG page says the activity log cannot be configured this way; use the template's
`forward...AzureActivityLogs` flags (section 4) or a subscription-level template.

### 7.3 New Relic side, Azure Monitor integration (plan only)

No Terraform is generated here because the resource names are UNVERIFIED (section 2). The plan file
lists the manual steps from NR-ACT (steps 1 to 5), the list of resource types to allow-list, and a
reminder that `microsoft.insights` must be a registered resource provider.

```text
# DATAPATH PLAN (Azure Monitor integration)
# 1. az account show   -> note subscription id and tenantId
# 2. Entra: register app "NewRelic-Integrations"; client secret (expiry <= 12 months suggested)
# 3. Subscription > Resource providers: microsoft.insights = Registered
# 4. IAM: Reader and Monitoring Reader on the subscription to the app
# 5. New Relic: Infrastructure > Azure > Add an Azure account:
#      subscription id = <SUBSCRIPTION_ID>   tenant id = <TENANT_ID>
#      application id  = <CLIENT_ID>         client secret = <read from your vault, never from this file>
# 6. Enable "Azure Monitor"; limit to resource types: <generated list>
# 7. Alert conditions on this data: streaming method = Event timer
```

### 7.4 Collector sketch (plan only)

```yaml
# DATAPATH PLAN (OpenTelemetry Collector contrib). Not generated for Azure Government unless chosen.
receivers:
  azure_monitor:
    subscription_ids: ["<SUBSCRIPTION_ID>"]
    tenant_id: "<TENANT_ID>"
    client_id: "<CLIENT_ID>"
    client_secret: "${env:AZURE_CLIENT_SECRET}"
    cloud: AzureCloud            # AzureUSGovernment or AzureChinaCloud when needed
    services: ["Microsoft.Compute/virtualMachines"]
    collection_interval: 60s
    use_batch_api: true          # experimental in the README
exporters:
  otlphttp/newrelic:
    endpoint: https://otlp.nr-data.net   # EU: https://otlp.eu01.nr-data.net
    headers:
      api-key: "${env:NEW_RELIC_LICENSE_KEY}"
service:
  pipelines:
    metrics:
      receivers: [azure_monitor]
      exporters: [otlphttp/newrelic]
```

The `otlphttp` exporter name and `headers` key are standard Collector configuration, not read in a
fetched page: UNVERIFIED. The receiver keys come from OTEL.

## 8. UNVERIFIED and not read

- Any New Relic page about streaming Azure metrics to an Event Hub.
- Terraform and NerdGraph pages for linking an Azure account.
- The dollar cost of anything.
- The attribute names of forwarded Azure logs; the metric names produced by the OTel receiver.
- Whether polling and managed Prometheus can run beside a second Prometheus on one AKS cluster.
- The EU Logs API endpoint value for the forwarder (the template default is the US endpoint).
