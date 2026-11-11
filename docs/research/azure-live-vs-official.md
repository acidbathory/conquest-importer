# Azure Monitor: live responses compared with the official examples

Read on 2026-11-11 from a free-trial subscription, with a service principal (Reader and Monitoring
Reader on the subscription, Contributor on one resource group). Objects were created by
`scripts/live-test/azure-estate/estate.py`; real responses are in `tests/fixtures/azure-live/`
(sanitised, see its `SOURCES.md`). The official side is `tests/fixtures/azure/`. Claims below are
from those files or from the calls the scripts made. "Not tested" means exactly that.

## What exists (in the one resource group)

| Type | Count | Notes |
|---|---|---|
| Storage account (Standard_LRS, StorageV2) | 1 | `ckgstoregdtuih` |
| Log Analytics workspace (PerGB2018, 30 days) | 1 | `ckg-law` |
| Action groups | 6 | email, email2 (legacy schema), itsm, eventhub, armrole, apppush |
| Metric alerts | 9 | on the storage account, all disabled, severities 0-4 |
| Scheduled query rules | 7 | 6 `LogAlert` and 1 `SimpleLogAlert`, all disabled |
| Activity log alerts | 3 | administrative (resource group), service health, resource health; disabled |
| Alert processing rules | 2 | suppression with a weekly schedule, add action group; disabled |
| Smart detector rules, Prometheus rule groups | 0 | smart detector needs Application Insights; Prometheus needs an Azure Monitor workspace and AKS |

Total 29 created. The list calls returned exactly these (`_meta.json`).

## What the API refused

| Request | Answer | Meaning |
|---|---|---|
| Action groups with a webhook, secure webhook, Logic App, Azure Function or Automation runbook receiver, URL on `example.invalid` | 400 `WebhookServiceUriBlocked`, `LogicAppCallbackUrlBlocked`, `AzureFunctionHttpTriggerUrlBlocked`, `AutomationRunbookServiceUriBlocked` (message is the code only) | Not a permission error. Azure blocks the host. No other host was tried: only `.invalid` was authorised. Those five receiver types are therefore **not tested live** |
| Metric alert pointing at a missing action group | 400 `Action Group <id> is invalid` | Referenced action groups are validated at write time |
| Metric alert `UsedCapacity` with `Minimum` | 400 `Time aggregation must be one of [Average]` | Per-metric allowed aggregations are enforced at write time |
| Metric alert `SuccessServerLatency`/`Transactions` with `Count` | 400 `Time aggregation must be one of [Total]` (for Transactions) | same |
| Scheduled query rule with `muteActionsDuration` and `autoMitigate` true or unset | 400 `Auto mitigation must be disabled when action suppression is set` | Mute and auto-mitigate exclude each other. The research doc did not know this |
| Scheduled query rule, 1-minute frequency, `union Usage, Heartbeat \| take 5` | 400 `One-minute frequency is not supported for this query...` | Confirms the Learn page rule (union and take at 1 minute) |

All the KQL I sent was accepted by the API's validation (no `skipQueryValidation`): the tables
`Usage`, `Heartbeat` and `AzureActivity` exist in a fresh workspace schema. The 1-minute `union`
probe above is the only KQL rejected.

## Differences that matter to the converter

1. Resource id casing is not stable. Action group ids come back as `.../providers/microsoft.insights/actionGroups/<n>`
   and scheduled query rule ids as `microsoft.insights/scheduledqueryrules`; metric alert, activity log alert and processing rule ids use
   `Microsoft.Insights/...`. A rule's `actions` reference the action group with the case the caller wrote.
   Compare ids case-insensitively. `type` is also cased differently from the id (`Microsoft.Insights/ActionGroups`, `.../ActivityLogAlerts`).
2. `location`: metric alerts `global` (lower case), activity log alerts, processing rules and action groups `Global`, scheduled query rules and storage `eastus`.
3. Fields the official examples show that the live responses did not have for these rules: `identity`, `customProperties`, `actionProperties`,
   `checkWorkspaceAlertsStorageConfigured`, `isWorkspaceAlertsStorageConfigured`, `skipQueryValidation`, `targetResourceTypes`, `tenantScope`.
   They are present only when sent (and `skipQueryValidation`, `checkWorkspaceAlertsStorageConfigured` are write-time flags that are never returned).
   `isWorkspaceAlertsStorageConfigured` was not returned even though the rules target a workspace.
4. Fields live responses add: `systemData` (createdBy is the app's id, `createdByType` `Application`) on scheduled query rules and processing rules, not on
   metric alerts or activity log alerts; `createdWithApiVersion` (`2026-03-01`) on every scheduled query rule; `kind: null` on action groups; `status: "Enabled"`
   on email receivers (not sent).
5. Defaults the API does and does not fill in:
   - Not filled: `autoMitigate` on scheduled query rules (absent unless sent, although the swagger says default true), `kind` on a `LogAlert` rule
     (absent, not `"LogAlert"`), `resolveConfiguration`, `muteActionsDuration`.
   - Filled: `dimensions: []` stays as sent on dynamic metric criteria; every action group returns all the empty receiver arrays
     (`smsReceivers: []`, `voiceReceivers: []`, ...), so "array present" does not mean "receiver exists".
   - `failingPeriods` was returned as sent (1 of 1 where I sent 1 of 1); a rule that omitted it was returned without it.
   - A `SimpleLogAlert` has no `evaluationFrequency` or `windowSize` in the response.
   - Numbers: `threshold` comes back as a float (`1000.0`).
   - Every disabled rule is stored with `enabled: false` and kept in the list.
6. Criteria shapes matched the official examples: `odata.type` `SingleResourceMultipleMetricCriteria` for a one-resource scope,
   `MultipleResourceMultipleMetricCriteria` for dynamic and dimensional criteria (the official dynamic example used the multiple type for one resource, and it was accepted).
   Dynamic criteria keep `ignoreDataBefore`, `alertSensitivity`, `failingPeriods`. A multi-condition static rule is returned as several `allOf` entries.
7. Pagination: none of the nine lists carried `nextLink` (at most 9 items, no list was large enough to page). The page size is **not tested**. Action groups
   returned only `value`, as the swagger says. Keep following `nextLink`.
8. Empty types answer 200 with `{"value": []}` (Prometheus rule groups, smart detector rules), not 404, even with no Azure Monitor workspace.

## api-version differences seen (same objects, older versions)

| Type | Old version | Difference |
|---|---|---|
| Metric alerts | 2018-03-01 | identical except `criteria.allOf[].dimensions` is absent in the old shape |
| Scheduled query rules | 2023-12-01 and 2021-08-01 | the rules are returned in the new shape; `criterionType`, `alertSensitivity`, `ignoreDataBefore` are absent in old versions, and 2021-08-01 also drops `resolveConfiguration`. Dynamic criteria therefore degrade to a plain static-looking criterion in an old client |
| Activity log alerts | 2020-10-01 | no difference |
| Alert processing rules | 2019-05-05-preview | returned in the old "action rules" shape (`scope`, `suppressionConfig`, `status`, `actionGroupId`) and not the 2021-08-08 shape (`scopes`, `actions[]`, `schedule`). The extractor must ask for 2021-08-08 |
| Action groups | 2021-09-01 | no difference for these receivers |

There is no legacy (2018-04-16 `source`/`schedule`/`action`) log alert in this subscription, so the legacy-rule question stays open.

## Secrets exposure finding

- With Reader-level read, the list returns the receivers in clear: email addresses, ITSM `workspaceId` and `connectionId`, Event Hub namespace and name,
  ARM role ids. Nothing was masked or removed.
- **Not answered:** whether webhook `serviceUri`, Logic App `callbackUrl`, Function `httpTriggerUrl` and runbook `serviceUri` come back in clear.
  Those receivers could not be created (table above) and no other URL host was authorised. The official examples return them in clear; the
  extractor must keep treating them as secrets. A decision from the owner is needed to test: for example a real, harmless HTTPS host the owner controls.
- Tokens and the client secret were never printed, stored or put in an error message (tested offline in `tests/test_azure_estate_arm.py`).

## Cost estimate (from pages fetched with curl on 2026-11-11)

- Azure Monitor pricing page (`https://azure.microsoft.com/en-us/pricing/details/monitor/`), fetched: "There is no charge for Activity Log alert rules
  or other alert rules when they are disabled." All 21 alert rules are disabled, so alert rules and processing rules: expected 0. (The page text has no price
  figures in the HTML; they come from the Microsoft Retail Prices API below.)
- Microsoft Retail Prices API (`https://prices.azure.com/api/retail/prices`, eastus), fetched: Log Analytics analytics-logs ingestion 2.3 per GB
  (a 0.0 entry also exists, free allowance); retention 0.1 per GB-month beyond the included period; general block blob v2 Hot LRS storage about 0.02 per GB-month.
- The workspace has no connected data source and the storage account is empty and unused, so the expected bill is about zero (cents at most from the
  platform tables and idle storage). This is an estimate from list prices: the actual charge was **not checked** in Cost Management (the app has no cost read role).
- Action groups: no price was found for them on the fetched page and the email receivers will not be called: **not checked**.

## Left to do

Cleanup: `python scripts/live-test/azure-estate/cleanup.py` previews; `--yes` deletes exactly the manifest entries
(`out/live-azure/manifest.json`, git-ignored). It was not run with `--yes`.
