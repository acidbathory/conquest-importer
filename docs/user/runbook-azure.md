# Runbook: Azure Monitor to New Relic

For observability engineers and architects who will run the importer against an Azure subscription.
Status: prototype, built offline. Read section 1 before you promise anything to anyone.

State described here: the `improve/azure-source` branch, 2026-11-11. Every command and flag on this
page was checked against `importer <command> --help`. Nothing has been run against a live Azure
subscription or a New Relic account yet; anything marked UNVERIFIED has not been seen working.
Research behind it: [alerting objects and mapping](../research/azure-monitor-alerting.md),
[data path](../research/azure-datapath.md), [measured baseline](../research/azure-baseline.md).

## 1. What you get, and what you do not get

What the importer does for Azure:

| Asset | What happens | Honest status |
|---|---|---|
| Metric alerts | A rule with one static-threshold criterion on a platform metric converts to a New Relic NRQL condition on `Metric`, created disabled. PromQL metric alerts go through the Grafana PromQL translator. | Every converted rule is labelled approximate. Metric names are derived from a documented rule and not yet seen arriving (UNVERIFIED). |
| Scheduled query rules (KQL) | A table, simple `where` terms and a count (or one average, sum, min or max of a column) convert to a count over forwarded Azure logs. Everything else is manual with the reason. | Log attribute names are UNVERIFIED. Most KQL in the official examples reads agent tables and stays manual. |
| Activity log, service health, resource health alerts | Manual, with the condition written out and the workaround. | No New Relic source for Azure activity events until they are forwarded as logs. |
| Prometheus rule groups | Alert rules go through the Grafana PromQL translator; recording rules are not alerts. | The data must reach New Relic by remote write or OTLP; managed Prometheus has no documented export. |
| Smart detector rules | Unsupported, with a baseline condition suggested. | No equivalent. |
| Action groups, alert processing rules | Read and saved (secrets dropped). Their conversion to destinations, workflows and muting rules is a separate step by another part of the project and is not described here. | Not part of this page. |
| Data (metrics, logs) | Not moved. See section 5. | History stays in Azure. |

Measured on the official examples (not an estate): about one alert rule in ten converts, see
[the baseline](../research/azure-baseline.md).

What you do NOT get:

- No proof on live data. Live verification of metric names, dimension attributes and log attributes
  is 0 until `check-azure-data` has run against an account that receives the data.
- Multi-criteria metric alerts (Azure fires only when all criteria are true) are manual: New Relic has
  no composite condition. Dynamic thresholds are manual: the models differ and there is no published
  sensitivity mapping.
- Severity 0 and 1 become critical, 2 to 4 become warning; the Azure severity is written into the
  description. Five levels collapse to two.
- Conditions use the event timer and open later than the Azure rule. Azure's `autoMitigate`,
  `timeToResolve` and `muteActionsDuration` have no condition setting; the report says so per rule.
- Azure Government and classic resources are not collected by New Relic's Azure integrations.

## 2. Prerequisites

Accounts and access:

- An Azure subscription and a read-only service principal. The importer sends only `GET` requests, to
  `management.azure.com`, plus one token request to `login.microsoftonline.com`. Step by step:
  [Azure credentials](azure-credentials.md). The **Reader** role at subscription scope is enough.
- A New Relic User API key (starts with `NRAK-`), only to create things and to run `check-azure-data`.
  A plan-only run does not need it. See [permissions](../permissions.md).
- A New Relic account to migrate into. Use a test account first.

Settings go in `.env` in the repository root. It is git-ignored. Never paste real values into chat,
tickets or documents.

```sh
AZURE_TENANT_ID=
AZURE_CLIENT_ID=
AZURE_CLIENT_SECRET=
AZURE_SUBSCRIPTION_ID=
# or, instead of the first three, a token from `az account get-access-token`:
AZURE_ACCESS_TOKEN=
NEW_RELIC_API_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_ALLOWED_ACCOUNT_IDS=
# Only behind a proxy that inspects TLS:
SSL_CERT_FILE=
```

Install (Python 3.12 or newer, and [uv](https://docs.astral.sh/uv/)):

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
uv sync
source .venv/bin/activate     # then `importer` is on your path
importer doctor --source azure
```

`doctor` is offline for Azure. It checks that the credentials are set, look right and are plain
text (it never shows a value), and that the subscription id is a UUID. Without activating the
environment, put `uv run --active` in front of every `importer` command.

## 3. The easy way

```sh
bash scripts/migrate.sh
```

The script loads `.env`, then runs `importer menu`. Type `10` for Azure. It asks for any of
`AZURE_TENANT_ID`, `AZURE_CLIENT_ID` and `AZURE_SUBSCRIPTION_ID` that is not set, and asks for
`AZURE_CLIENT_SECRET` with a hidden prompt (kept in memory only). Then: check setup, read (read-only),
convert (local files only), preview, one question; only `yes` creates anything. Everything is created
disabled. The menu writes to `out/guided/azure-<timestamp>/`.

Azure is number 10 because 5 to 9 were already in use. The menu handles alert rules only; use the
manual way for the rest. The access-token route (`AZURE_ACCESS_TOKEN`) works with the manual way only.
More detail: [menu and rollback](menu-and-rollback.md).

## 4. The manual way

Run from the repository root with the environment active. Use a new `--out` folder each time.

| # | Command | Reads | Writes | New Relic |
|---|---|---|---|---|
| 1 | `importer doctor --source azure` | environment | nothing | no |
| 2 | `importer extract-azure --subscription <uuid> --out out/azure` | Azure alert rules, action groups, processing rules, Prometheus rule groups (GET) | `out/azure/` (folder 0700, files 0600) | no |
| 3 | `importer convert --source azure --in out/azure --out out/azure-alerts` | step 2 | IR, NerdGraph JSON, `main.tf`, `report.md` | no |
| 4 | `importer report out/azure-alerts --source azure` | step 3 | HTML report | no |
| 5 | `importer check-azure-data --alerts out/azure-alerts --nr-account-id <id>` | New Relic (read-only NRQL) | optional markdown | reads |
| 6 | `importer plan-remediation --in out/azure-alerts --out out/azure-remediation` | step 3 | plan | no |
| 7 | `importer apply --in out/azure-alerts --account-id <id> --apply --confirm-account <id>` | step 3 | conditions, disabled | WRITES |

Step 2 drops secrets as it reads: webhook and callback URLs, function trigger URLs, secure webhook
ids, passwords and the values of the free-form property maps become a reference name such as
`secretref:azure/actionGroups/ops/webhookReceivers[hook]/serviceUri`. It writes one `<type>.json`
per resource type and `extract-summary.json` (counts, API versions, warnings). The export still holds
subscription ids, resource names and email addresses: review it before sharing.

Reads are capped (pages, bytes and 10 minutes for the whole run). A 429 is retried after its
`Retry-After` (at most 60 seconds each time). If one resource type cannot be read (for example a 403)
the others are still saved and the run prints what it could not read.

A hand-made `name-map.json` next to the export overrides derived names (section 5):

```json
{"metrics": {"Percentage CPU": "azure.compute.virtualmachines.PercentageCPU"},
 "attributes": {"AzureDiagnostics.Category": "category", "ShardId": "azure.cache.redis.ShardId"}}
```

## 5. The data path

A converted condition reads data. If the data is not in New Relic it never fires, and a `sum()` or
`count()` over a missing metric looks healthy.

- Metric alerts on platform metrics need the New Relic **Azure Monitor** integration for the
  subscription (it needs Reader and Monitoring Reader, a different principal is a good idea). Metrics
  arrive as dimensional `Metric` data with `collector.name = 'azure-monitor'`. Custom and guest metrics
  are not collected. Details and the naming rule: [data path](../research/azure-datapath.md).
- The name is derived: resource type lowercased, `/` becomes `.`, `Microsoft.` becomes `azure.`, the
  metric name keeps its case without spaces. `Microsoft.Compute/virtualMachines` + `Percentage CPU`
  becomes `azure.compute.virtualmachines.PercentageCPU`. This follows the New Relic documentation
  and was **not** seen arriving. Dimension attributes are named the same way
  (`azure.cache.redis.ShardId`); the casing of `azure.resourceId` is UNVERIFIED.
- Log conditions (KQL, and the manual activity log workaround) need the Event Hub log forwarder and a
  diagnostic setting on each resource. Forwarded logs carry `plugin.type = 'azure'`; the attribute
  names of a record are UNVERIFIED, so inspect one before trusting a log condition.
- PromQL conditions need Prometheus data in New Relic by remote write or OTLP; the names are the
  Prometheus names, not `azure.` names.
- Do not run New Relic's older polling integrations and the Azure Monitor integration for the same
  resources; names would duplicate.

`importer check-azure-data` is how the derived names become verified. For each converted condition it
counts data points of the metric, checks that the filter and facet attributes exist, and, when the
metric is missing, lists the metric names that did arrive for the same resource type. Copy the right
one into `name-map.json` and convert again.

## 6. Verification checklist

1. `importer doctor --source azure` passes.
2. `extract-azure` prints counts that match the portal (alert rules, action groups).
3. `convert` ran; open `report.md`. Check the count of manual rules and read the reasons.
4. `check-azure-data` shows "data arrives" for every converted condition. Exit code 2 means a
   condition is blind; fix the data path or the name.
5. Compare one converted query with the same chart in Azure Monitor metrics before you trust it.
6. Enable conditions one at a time, in a test account, and watch one open and close.

UNVERIFIED until a live run: that New Relic opens a violation for the same breach; how much later
it opens than Azure; that `count` and `sum` on stored Azure metrics match Azure's `Count` and `Total`.

## 7. Rollback and undo

Everything created is disabled and recorded.

- Menu: `bash scripts/migrate.sh`, then `5`. It lists recorded runs, shows what would be deleted and
  asks for `yes`.
- `importer runs` lists recorded runs. `importer rollback-run` does the same as menu item 5.
- `importer rollback --manifest out/azure-alerts/manifest.json --account-id <id>` previews; add
  `--apply --confirm-account <id>` to delete. Each object is re-read and deleted only if its id still
  has the recorded name.
- Azure is never changed by the importer. Revoke the service principal when you are done (see
  [Azure credentials](azure-credentials.md)).

## 8. Known limits and the remediation plan

- Not converted: several criteria, dynamic thresholds, availability tests, custom and guest
  metrics, Application Insights scopes, windows over one hour or under one minute, scopes of several
  resource groups, KQL beyond table + where + count/aggregate (joins, `let`, regular expressions,
  `arg_max`, `or`), agent tables (Perf, InsightsMetrics, Heartbeat, Container, Application Insights),
  activity log and service health alerts, smart detectors, `LogToMetric` rules, tenant-scoped rules.
- A failing-periods rule (M of N) needs M consecutive windows in New Relic; Azure counts non-consecutive
  ones, so New Relic is stricter.
- `Count` on a metric is written `count()` and may differ from Azure's sample count; `Total` is
  written `sum()` and is wrong for a metric stored as a gauge.
- A below or equals count over logs is wrapped so an empty window gives 0 instead of NULL.
- The plan: `importer plan-remediation --in out/azure-alerts --out out/azure-remediation` lists every
  item that did not convert with its steps and the closest New Relic construct
  ([remediation cookbook](remediation-cookbook.md)).

## 9. Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `AZURE_CLIENT_SECRET is not set` / `must be a UUID` | Missing or malformed setting | Fill `.env`; `importer doctor --source azure` says which. |
| `Azure sign-in returned 401: invalid_client` | Wrong or expired client secret | Create a new secret ([credentials](azure-credentials.md)). |
| `Azure returned 403` for one type | The principal lacks Reader on the subscription for that provider | Assign Reader at subscription scope. Other types are still saved. |
| `AZURE_ACCESS_TOKEN must be plain ASCII text` | A smart quote or line break was pasted | Paste again as plain text. |
| `more than N pages` or `took longer than 600 seconds` | A cap stopped the run | Narrow the subscription, or ask for the cap to be raised. |
| A converted condition never fires | The metric is not in New Relic or has another name | `check-azure-data`, then `name-map.json`. |
| `the subscription must be a UUID` | `--subscription` is a name, not an id | Use the id from `az account show`. |
| `... already has files; pass a new folder` | The importer never mixes runs | Pick a new `--out` folder. |
| Menu: 10 is not accepted | Older checkout | Update; Azure is menu item 10. |
| Condition opens later than the Azure alert | Polled data, event timer, clock-aligned windows | Expected; see section 1. |
