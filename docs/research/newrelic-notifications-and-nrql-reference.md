# New Relic side reference: notifications and NRQL alert conditions

Status: research only, read on 2026-11-09. Nothing in `src/` was changed. No New Relic account or API was called and no credential was used.

This is the single reference for the New Relic side that all four converters (AWS, GCP, Dynatrace, Grafana) share: destinations, channels, workflows, muting rules and NRQL alert conditions.

## Evidence labels

Every claim carries one label.

- DOCS: read in public New Relic documentation (the docs-website repository files and the rendered docs.newrelic.com pages, fetched 2026-11-09). The short tag after DOCS names the page, see the key below.
- SCHEMA: read from the local Terraform provider schema (`terraform providers schema -json`, run read-only in `scripts/live-test/gcp-link`; provider `newrelic/newrelic` 3.101.12). The Terraform pages in the key were fetched from the provider repository `main` branch, which can be newer than 3.101.12.
- REPO: read in this repository, with the file.
- UNVERIFIED: from memory or inference, or the sources conflict. Needs a live test or a person to check.

Page key (docs-website paths are under `src/content/docs/`):

| Tag | Page |
|---|---|
| TF-DEST | provider docs `resources/notification_destination` |
| TF-CHAN | provider docs `resources/notification_channel` |
| TF-WF | provider docs `resources/workflow` |
| TF-MUTE | provider docs `resources/alert_muting_rule` |
| TF-NRQL | provider docs `resources/nrql_alert_condition` |
| TF-GO | provider source `newrelic/resource_newrelic_nrql_alert_condition.go` and `resource_newrelic_workflow.go` |
| NG-DEST | `apis/nerdgraph/examples/nerdgraph-api-notifications-destinations` |
| NG-CHAN | `apis/nerdgraph/examples/nerdgraph-api-notifications-channels` |
| NG-WF | `apis/nerdgraph/examples/nerdgraph-api-workflows` |
| NG-NRQL | `apis/nerdgraph/examples/nerdgraph-api-nrql-condition-alerts` |
| NG-GAP | `apis/nerdgraph/examples/nerdgraph-api-loss-signal-gap-filling` |
| INT | `alerts/get-notified/notification-integrations` |
| DST | `alerts/get-notified/destinations` |
| TEAMS | `alerts/get-notified/microsoft-teams-integrations` |
| AWF | `alerts/get-notified/alert-event-workflows` |
| VAR | `alerts/get-notified/custom-variables-alert-event-workflows` |
| MT | `alerts/get-notified/message-templates` |
| MUTE | `alerts/get-notified/muting-rules-suppress-notifications` |
| NQC | `alerts/create-alert/create-alert-condition/create-nrql-alert-conditions` |
| STR | `alerts/create-alert/fine-tune/streaming-alerts-key-terms-concepts` |
| LIM | `alerts/admin/rules-limits-alerts` |
| TTL | `alerts/create-alert/condition-details/title-template` |
| DSC | `alerts/create-alert/condition-details/custom-alert-event-descriptions` |

All are on https://docs.newrelic.com/docs/ (rendered) and https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/ (provider pages).

---

# Part 1. Notifications

## 1.0 The object model

- A destination holds the connection and credentials for one third-party system. A channel (a message template) points at one destination and sets the message fields. A workflow filters issues and lists channels to notify. DOCS TF-WF, TF-CHAN.
- A channel can be used by one workflow only, and only once inside it. DOCS TF-WF ("each channel can only be used in a single workflow"), NG-WF ("A channel ID is unique and so can't be used in multiple workflows or used multiple times in the same workflow"). REPO `apply_notifications.py` creates one channel per destination per plan, so it is consistent.
- A channel used by a workflow must be created with `product = "IINT"`. DOCS TF-WF. The other products are `DISCUSSIONS` and `ERROR_TRACKING`. SCHEMA (`newrelic_notification_channel.product`).
- Destinations are created in an account unless a `scope` block (ACCOUNT or ORGANIZATION) is given, which makes a cross-account destination. DOCS TF-DEST; SCHEMA (`scope`).
- Sensitive values (auth password, tokens, header values, the secret part of a secure URL) are write-only: the API does not return them. DOCS TF-DEST ("not returned from the underlying API"). SCHEMA marks `auth_basic.password`, `auth_custom_header.value`, `auth_token.token`, `secure_url.secure_suffix` as sensitive.
- Destination status values: DEFAULT, CONFIGURATION_ERROR, THROTTLING_WARNING, UNINSTALLED, EXTERNAL_SERVER_ERROR, TIMEOUT_ERROR, AUTH_ERROR, UNKNOWN_ERROR. A destination that fails within two hours is snoozed for a few minutes. Failed sends are written to `NrIntegrationError` with category `NotificationError`. DOCS DST.
- Limits per account: destinations 10k for EMAIL, WEBHOOK and Slack, 20k for PAGERDUTY_SERVICE_INTEGRATION, 5k for the rest. Email addresses per destination 5 (free) or 150 (paid). 1,000 unique email addresses per organization per 24 hours. DOCS LIM, INT.

## 1.1 Destination types

Destination `type` values (SCHEMA, also DOCS TF-DEST): WEBHOOK, EMAIL, SERVICE_NOW, SERVICE_NOW_APP, PAGERDUTY_ACCOUNT_INTEGRATION, PAGERDUTY_SERVICE_INTEGRATION, JIRA, SLACK, SLACK_COLLABORATION, SLACK_LEGACY, MOBILE_PUSH, EVENT_BRIDGE, MICROSOFT_TEAMS, WORKFLOW_AUTOMATION.

Channel `type` values (SCHEMA, also DOCS TF-CHAN): WEBHOOK, EMAIL, SERVICENOW_INCIDENTS, SERVICE_NOW_APP, PAGERDUTY_ACCOUNT_INTEGRATION, PAGERDUTY_SERVICE_INTEGRATION, JIRA_CLASSIC, SLACK, SLACK_COLLABORATION, SLACK_LEGACY, MOBILE_PUSH, EVENT_BRIDGE, MICROSOFT_TEAMS, WORKFLOW_AUTOMATION. Note the two renames: destination SERVICE_NOW has channel SERVICENOW_INCIDENTS, and destination JIRA has channel JIRA_CLASSIC. TF-CHAN also mentions `JIRA_NEXTGEN` in the property list; it is not in the SCHEMA enum text (UNVERIFIED whether the API accepts it).

The Terraform destination docs say SLACK and SLACK_COLLABORATION cannot be created by Terraform (import, update and destroy only). DOCS TF-DEST. The NerdGraph docs say the same for Slack in the mutation. DOCS NG-DEST ("the destination cannot be created with a mutation").

### Per type

"API-only" means a user API key is enough to create the object with no browser step. "Secret" lists the values the person must supply because no source exports them.

| Destination type | Auth | Required destination properties (exact keys) | Secrets | API-only with a user API key? | Channel type, product | Channel properties |
|---|---|---|---|---|---|---|
| WEBHOOK | none, `auth_basic` (user, password), `auth_token` (prefix, token; UI calls it Bearer), or one or more `auth_custom_header` (key, value). DOCS TF-DEST, INT. OAuth2 is UI only. DOCS TF-DEST note | `url` (required). Optional `two_way_integration`; TF-DEST example also sets an arbitrary property `source`. The `secure_url` block (`prefix`, `secure_suffix`) stores the part of the URL after the domain encrypted | basic password, token, custom header values, `secure_suffix` | Yes (basic, token, custom header, secure URL). OAuth2 no | WEBHOOK, IINT | `payload` (required; JSON, Handlebars). `headers` (optional, a map of key/value; DOCS TF-CHAN). If the same header key is on both destination and channel, the destination wins. DOCS INT. No `method` property is documented: the notifier sends HTTP POST and assumes JSON. DOCS INT |
| EMAIL | none | `email` (required; a comma-separated list, TF-DEST example) | none | Yes. UNVERIFIED: the UI sends a verification email to a new address and the recipient must verify before receiving (DOCS INT, UI text); not stated for API creation | EMAIL, IINT (or DISCUSSIONS) | `subject` (optional), `customDetailsEmail` (optional) |
| SERVICE_NOW | `auth_basic` | `url` (base URL only), `two_way_integration` optional | password | Yes | SERVICENOW_INCIDENTS, IINT | `description`, `short_description`, plus the fields of the instance's incident table. The right keys come from the `channelSchema` query. DOCS NG-CHAN. DOCS INT: this is the legacy integration, in maintenance mode |
| SERVICE_NOW_APP | `auth_basic` (a ServiceNow user with role `x_newre_core.inbound_api`) | `url` (base URL, must be `*.service-now.com`) | password | The destination call is API, but the New Relic application must first be installed in the ServiceNow store by a ServiceNow admin, and DOCS INT says some accounts must email New Relic to be allowed to create it | SERVICE_NOW_APP, IINT | none |
| PAGERDUTY_ACCOUNT_INTEGRATION | `auth_token`, prefix `Token token=`, token is a PagerDuty REST API key (general access or user token) | `two_way_integration` optional (TF-DEST example uses key/value `two_way_integration`/`true`) | the REST API key | Yes. New Relic creates a service integration in PagerDuty itself (DOCS INT) | PAGERDUTY_ACCOUNT_INTEGRATION, IINT | `summary` (required), `service` (required, the PagerDuty service id), `email` (required, a PagerDuty user), `customDetails` (optional, JSON) |
| PAGERDUTY_SERVICE_INTEGRATION | `auth_token`, prefix `Token token=`, token is the service Events API integration key | none (TF-DEST sends `property { key = "" value = "" }`; NG-DEST sends `properties: []`) | the integration key | Yes | PAGERDUTY_SERVICE_INTEGRATION, IINT | `summary` (required), `customDetails` (optional, JSON). One destination per PagerDuty service. DOCS INT |
| JIRA | `auth_basic` (user = the Atlassian account email, password = an Atlassian API token) | `url` (base URL), `two_way_integration` optional | API token | Yes. Jira Cloud only; on-premises and Data Center are not supported. Two-way sync needs an Admin token. DOCS INT | JIRA_CLASSIC, IINT or ERROR_TRACKING (TF-CHAN example uses ERROR_TRACKING; UNVERIFIED which products work with workflows, the generic rule is IINT) | `project` (required, numeric id), `issuetype` (required, numeric id), `summary`, `description`. Ids come from `channelSuggestions`. DOCS NG-CHAN |
| SLACK | OAuth2 in a browser | set by the OAuth flow (`teamName`, label "Team Name", shown in a TF-DEST import example) | OAuth grant | No. A person must click through Slack authorisation once per workspace and New Relic account. After that the destination id can be read by API and channels can be created. DOCS INT, TF-DEST, NG-DEST | SLACK, IINT | `channelId` (required, the Slack channel id), `customDetailsSlack` (optional; must suit Slack's blocks API). Workflow-level `update_original_message` is Slack only. Private channels need the person to be authenticated, then the bot joins. DOCS INT |
| SLACK_COLLABORATION | OAuth2 | as Slack | OAuth grant | No (cannot be created by Terraform) | SLACK_COLLABORATION, product DISCUSSIONS (UNVERIFIED) | not documented. DOCS DST says Slack is used by workflows, Errors Inbox and Discussions. Not a workflow target in practice |
| SLACK_LEGACY | the old Slack incoming-webhook destination | not documented | UNVERIFIED | UNVERIFIED | SLACK_LEGACY | not documented. DOCS INT tells users to migrate away from it. Do not generate it |
| MOBILE_PUSH | none | `userId` (required, a New Relic user id) | none | Only for the user who owns the key. DOCS INT: only for the current logged-in user, and one per user | MOBILE_PUSH, IINT | none | 
| EVENT_BRIDGE | NG-DEST shows `auth` BASIC with an IAM user and password; TF-DEST shows no auth block. UNVERIFIED which is current | `AWSAccountId` (docs say a 12-digit account id), `AWSRegion` | possibly the IAM credentials (conflict above) | The New Relic side yes. Then in AWS a person must associate the partner event source with an event bus (DOCS INT); that step can be done by AWS API but is not New Relic | EVENT_BRIDGE, IINT | `eventSource` (required; in the TF-CHAN example `aws.partner/mydomain/myaccountid/name`; NG-CHAN says it must be a full existing event source), `eventContent` (JSON template). Creating the channel creates the partner event source in AWS. DOCS INT |
| MICROSOFT_TEAMS | none | `securityCode` (required) | the security code | Almost. A person must install the "New Relic for Microsoft Teams" app (Microsoft AppSource) in the Teams workspace and add it to a channel; the app posts the code. After that the destination and channel are API calls. DOCS TEAMS, NG-DEST | MICROSOFT_TEAMS, IINT | `teamId` (required), `channelId` (required, `19:...@thread.tacv2`), `customDetails` (optional). Team and channel ids come from `channelSuggestions` (DOCS NG-CHAN). Available in US, EU and JP regions. DOCS NG-DEST |
| WORKFLOW_AUTOMATION | `auth_custom_header` with key `Api-Key` and value a New Relic user API key (required) | none; TF-DEST says the `property` block must still be present with `key = ""` and `value = ""` | the New Relic user API key | Yes | WORKFLOW_AUTOMATION, IINT | `workflowAutomation` (required, the workflow automation reference) |

Correction to earlier claims in the repo: Microsoft Teams has its own destination type, MICROSOFT_TEAMS (SCHEMA, DOCS TF-DEST, DOCS NG-DEST, DOCS TEAMS). REPO `sources/dynatrace/notifications.py` ("ms teams" entry) says "A Teams destination type is not confirmed for New Relic" and maps it to WEBHOOK. DOCS DST also warns that Office 365 incoming-webhook connectors in Teams are being retired by Microsoft (users had to move webhook URLs to a new format by 2025-01-31), so a webhook to a legacy connector URL is the weaker route.

### Opsgenie, Splunk On-call (VictorOps), xMatters, Datadog-style targets

New Relic documents these as webhook templates, not destination types: Opsgenie, Splunk On-call, xMatters. DOCS INT. They use WEBHOOK with a payload (and headers) the receiving product expects.

## 1.2 Workflows

### Shape and fields

NerdGraph `aiWorkflowsCreateWorkflow` input (DOCS NG-WF) and the Terraform resource (SCHEMA `newrelic_workflow`):

| Field | Meaning |
|---|---|
| `name` | required; the UI says it must be unique (DOCS AWF). REPO checks by name before creating |
| `workflowEnabled` (TF `enabled`, default true per TF-WF) | master switch |
| `destinationsEnabled` | deprecated in Terraform (SCHEMA, DOCS TF-WF); still in NerdGraph examples |
| `enrichmentsEnabled` | TF default true (DOCS TF-WF) |
| `mutingRulesHandling` (required) | `NOTIFY_ALL_ISSUES`, `DONT_NOTIFY_FULLY_MUTED_ISSUES`, `DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES`. DOCS TF-WF, SCHEMA |
| `issuesFilter` (required) | `name` (cosmetic, not empty), `type` (`FILTER`; `VIEW` also exists in SCHEMA), `predicates[]` of `attribute`, `operator`, `values`. All predicates must match; a value list means "any of". Empty predicate list catches every issue. DOCS TF-WF, NG-WF |
| `destinationConfigurations[]` | `channelId`, optional `notificationTriggers`, optional `updateOriginalMessage` (Slack only). At least one |
| `enrichments.nrql[]` | `name` (must be unique, used in templates), `configuration[].query` |

### Filter attributes and operators

- Plain string attributes, for example `state`, `priority`. Operators: CONTAINS, DOES_NOT_CONTAIN, EQUAL, DOES_NOT_EQUAL, STARTS_WITH, ENDS_WITH. DOCS TF-WF.
- Number attributes (mostly timestamps such as `acknowledgedAt`): EQUAL, DOES_NOT_EQUAL, LESS_OR_EQUAL, LESS_THAN, GREATER_OR_EQUAL, GREATER_THAN. DOCS TF-WF.
- List attributes (anything that belongs to the child alert events): EXACTLY_MATCHES, DOES_NOT_EXACTLY_MATCH (the list contains one of the values, whole-item match), and CONTAINS, DOES_NOT_CONTAIN (substring of an item; the docs say avoid). DOCS TF-WF.
- IS and IS_NOT only with the value NULL (docs say avoid). All operators are case-insensitive. DOCS TF-WF. The full SCHEMA operator list is CONTAINS, DOES_NOT_CONTAIN, DOES_NOT_EQUAL, DOES_NOT_EXACTLY_MATCH, ENDS_WITH, EQUAL, EXACTLY_MATCHES, GREATER_OR_EQUAL, GREATER_THAN, IS, IS_NOT, LESS_OR_EQUAL, LESS_THAN, STARTS_WITH.
- Priority values: `CRITICAL`, `HIGH`, `MEDIUM`, `LOW` (DOCS VAR). To notify on warning alert events only, filter priority equal to `HIGH` (DOCS AWF). So a critical term maps to CRITICAL and a warning term to HIGH (DOCS AWF tip; the mapping rule itself is UNVERIFIED beyond that tip).
- Attributes seen in the docs: `accumulations.conditionName`, `accumulations.policyName`, `accumulations.conditionFamilyId`, `accumulations.conditionProduct`, `accumulations.origins`, `accumulations.sources`, `accumulations.runbookUrl`, `accumulations.nrqlQuery`, `labels.policyIds`, `labels.accountIds`, `labels.aggregationKeys`, `labels.originalAccountIds`, `labels.targetId` (the VAR page prints a typo `abels.targetId`), `entitiesData.types`, `entitiesData.names`, `entitiesData.ids`, `entitiesData.kinds`, `priority`, `state`, `isCorrelated`, `mutingState`, `owner`. DOCS VAR (the same list that serves as notification variables). Using these as filter attributes is DOCS for `accumulations.conditionName`-style and `labels.policyIds` (TF-WF example, NG-WF example); for the others it is UNVERIFIED that each can be filtered on (the UI offers attributes from past issues; DOCS AWF).
- Entity tags. Three spellings appear in New Relic's own pages:
  - `accumulations.tag.team` in a working NerdGraph filter example. DOCS NG-WF.
  - `accumulations.tag.account`, `accumulations.tag.affectedService`, `accumulations.tag.assignmentGroup`, `accumulations.tag.causeService` in the variable list, and `accumulations.tag.appName` in an enrichment example. DOCS VAR.
  - `accumulations.tags.X` and `tag.team` in the Terraform workflow page. DOCS TF-WF.
  - The muting-rule and title-template pages use `tags.<NAME>` (plural) for alert-event attributes, and say the incident payload shows it as `tag.<name>` without the "s". DOCS MUTE, TTL. That is a different object (the alert event), not the issue.
  - `labels.tags.x`: no page found. UNVERIFIED.
  - Decision: for an issue filter use `accumulations.tag.<name>` (two docs pages agree and one is a create-workflow example). `tags.<name>` as a workflow issue attribute is not documented anywhere, so REPO `sources/dynatrace/notifications.py:168` (`tags.{key}`) is probably wrong. Needs a live test. Tags only exist on the issue if the tag is on the entity or condition that raised the alert, and the UI notes that `accumulations.tag.foo` appears only after an issue has carried it. DOCS MT.
- Size limit: the filter is limited to 4,096 characters per workflow, not raisable. DOCS AWF, LIM. Workflows per account: 1,000 by default. DOCS AWF, LIM.

### Notification triggers

Per destination configuration, not per workflow. Values: ACTIVATED, ACKNOWLEDGED, INVESTIGATING, PRIORITY_CHANGED, CLOSED, OTHER_UPDATES. DOCS TF-WF. Defaults when unset: DOCS AWF table says Activated and Closed go to all destinations; Acknowledged and Investigating to Webhook, PagerDuty, Slack, Email, Mobile; Priority changed to Webhook, EventBridge, ServiceNow, Jira; Other updates to Webhook, EventBridge, ServiceNow, Jira, PagerDuty (account integration). The AWF page also says the Activated trigger must be on for the others to matter. Whether the API applies those defaults when `notificationTriggers` is omitted is UNVERIFIED (REPO omits it).

### Enrichments

- At most 2 per workflow. DOCS AWF.
- NRQL only. Results can be sent as JSON or numbers to Webhook and Jira, and as images to Slack, PagerDuty, ServiceNow (App) and email. Use single-value queries (`count`, `min`, `max`) for Webhook and Jira. DOCS AWF.
- The query can use issue variables, for example `{{entitiesData.names}}` or `{{accumulations.tag.appName}}`. The query must be run in the UI before saving (a UI rule). DOCS AWF, VAR.
- Terraform: `enrichments { nrql { name, configuration { query } } }`, at most one `enrichments` block. SCHEMA.

### Muting handling in a workflow

- An issue is FULLY_MUTED (all open alert events muted), PARTIALLY_MUTED, or NOT_MUTED. DOCS MUTE.
- `NOTIFY_ALL_ISSUES` notifies regardless of muting. So a workflow created with `NOTIFY_ALL_ISSUES` makes every muting rule useless for that workflow's notifications. DOCS TF-WF. REPO `emit/notifications.py` hard-codes `NOTIFY_ALL_ISSUES` (both NerdGraph and Terraform). That is correct only while no muting rules are migrated.
- Muting is decided by the first alert event that triggered a notification in the issue. DOCS MUTE.

### Workflow variables (Handlebars)

DOCS VAR lists 72 keys. Message templates use Handlebars: `{{var}}`, nested paths such as `{{annotations.title.[0]}}`, and the built-ins. New Relic adds `{{json x}}`, `{{escape x}}` and `{{#eq a b}}`; `{{#if}}`, `{{#each}}`, `{{#unless}}` are standard. DOCS MT. Keys:

accumulations.conditionDescription, accumulations.conditionFamilyId, accumulations.conditionName, accumulations.conditionProduct, accumulations.deepLinkUrl, accumulations.evaluation.metricValueFunction, accumulations.evaluation.name, accumulations.nrqlQuery, accumulations.origins, accumulations.policyName, accumulations.runbookUrl, accumulations.sources, accumulations.tag.account, accumulations.tag.affectedService, accumulations.tag.assignmentGroup, accumulations.tag.causeService, acknowledgedBy, activatedAt, annotations.description, annotations.title, annotations.wildcard, closedBy, closedAt, closedIncidentsCount, createdAt, dataMLModules.components, dataMLModules.goldenSignals, dataMLModules.suggestedResponders, entitiesData.entities, entitiesData.ids, entitiesData.kinds, entitiesData.names, entitiesData.types, impactedEntitiesCount, incidentIds, isCorrelated, isAcknowledged, issueAckUrl, issueCloseUrl, issuePageUrl, issueActivatedAtUtc, issueClosedAtUtc, issueCreatedAtUtc, issueAcknowledgedAt, issueClosedAt, issueDurationMs, issueDurationText, issueId, issueTitle, issueUpdatedAt, labels.accountIds, labels.aggregationKeys, labels.originalAccountIds, labels.policyIds, labels.targetId, mutingState, nrAccountId, openIncidentsCount, owner, policyUrl, priority, priorityText, realIssueCount, state, stateText, status, totalIncidents, triggeredAt, triggerEvent, updatedAt, violationChartUrl, workflowName.

Notes: `priority` is CRITICAL/HIGH/MEDIUM/LOW and `priorityText` is capitalised. A variable that is absent for an issue renders as an undefined-type error; guard with `{{#if x}}`. DOCS VAR, INT. Variables the REPO uses (`issueId`, `annotations.title.[0]`, `state`, `priority`, `issueTitle`) are all in the list. Other payload variables seen in the Terraform examples: `{{ nrAccountId }}`, `{{json issuePageUrl}}`, `{{json totalIncidents}}`. DOCS TF-CHAN.

Webhook payload must be valid JSON after rendering, or the template cannot be saved (UI). DOCS INT. Event Bridge `eventContent` and PagerDuty `customDetails` must also be valid JSON. DOCS TF-CHAN comments.

## 1.3 Muting rules

NerdGraph: `alertsMutingRuleCreate`, `alertsMutingRuleUpdate`; queries `actor.account.alerts.mutingRule(s)`. DOCS MUTE. Terraform: `newrelic_alert_muting_rule`.

- Fields. `name` (required), `description`, `enabled` (required in Terraform, SCHEMA), `condition`, `schedule`, `actionOnMutingRuleWindowEnded`. DOCS MUTE, SCHEMA.
- `condition`: `operator` AND or OR, and `conditions[]` of `attribute`, `operator`, `values`. At most 20 sub-conditions and 500 values per condition. DOCS MUTE.
- Attributes: `accountId`, `conditionId`, `conditionName`, `conditionRunbookUrl` (the UI page says `runbookUrl` as `conditionRunbookUrl`), `conditionType`, `entity.guid`, `nrqlEventType`, `nrqlQuery`, `policyId`, `policyName`, `product`, `targetId`, `targetName`, `compoundAlertId`, `compoundAlertName`, and `tags.<NAME>`. DOCS MUTE, TF-MUTE. For a faceted NRQL condition, `tags.<FACETED_ATTRIBUTE>` targets the facet value (`FACET host` gives `tags.host`). For conditions on `NrAi*` events use `tags.tags.<NAME>`. Values are case-sensitive. DOCS MUTE.
- Operators: TF-MUTE lists ANY, CONTAINS, ENDS_WITH, EQUALS, IN, IS_BLANK, IS_NOT_BLANK, NOT_CONTAINS, NOT_ENDS_WITH, NOT_EQUALS, NOT_IN, NOT_STARTS_WITH, STARTS_WITH. The MUTE page lists the negatives as `DOES_NOT_EQUALS`, `DOES_NOT_CONTAINS`, `DOES_NOT_STARTS_WITH`, `NOT_ENDS_WITH`, and `IS_ANY`. The two pages conflict. Use the TF-MUTE spellings (the provider validates them) and confirm with one live call. UNVERIFIED which set NerdGraph accepts.
- Schedule: `startTime`, `endTime` (local ISO 8601 with no offset, for example `2020-07-08T14:30:00`), `timeZone` (required, an IANA name), `repeat` (DAILY, WEEKLY, MONTHLY or null), `endRepeat` or `repeatCount` (one of them, never both; `repeatCount` includes the original window, so 2 means one repeat), `weeklyRepeatDays` (MONDAY..). If WEEKLY and no days are given, it repeats on the start weekday. The weekday boxes override the start and end date: the rule applies on the first of those days after the start date. DOCS MUTE, TF-MUTE. A rule with no schedule is active whenever enabled (DOCS MUTE statuses Active, Scheduled, Ended, Inactive).
- Window-end behaviour: `actionOnMutingRuleWindowEnded` is `CLOSE_ISSUES_ON_INACTIVE` (default in the UI, called Notify; closes the muted event and a new un-muted one opens if the problem continues) or `DO_NOTHING` (Suppress notification; the muted event stays open). DOCS MUTE, TF-MUTE.
- What muting does: the alert event still opens and is recorded; only the notification is withheld, and only for workflows whose `mutingRulesHandling` is not `NOTIFY_ALL_ISSUES`. A rule applies to one account. DOCS MUTE.
- Compared with Dynatrace maintenance windows and Grafana mute timings (comparison from memory of those products, not read in this task; UNVERIFIED):
  - A New Relic rule is a single window plus a daily, weekly or monthly repeat that ends by date or count. It has no "months of the year", "days of the month" list, "time of day range spanning every day" or year list, which Grafana mute timings do have. Those have to be unrolled into several rules or approximated.
  - A New Relic rule matches alert-event attributes (policy, condition, entity, facet tags), not arbitrary source labels. A Grafana mute timing is attached to a notification policy that matches on labels, and a Dynatrace maintenance window matches entities by tag or id; both need a translation to condition name, policy, entity guid or facet tag.
  - A Dynatrace maintenance window can suppress problem detection or alerting; New Relic only suppresses notifications and still records the events.

## 1.4 Testing a destination or channel from the API

Yes. DOCS NG-DEST, NG-CHAN, NG-WF.

| Mutation | Input | Notes |
|---|---|---|
| `aiNotificationsTestDestination(accountId, destination: {type, name, properties})` | a destination definition not yet saved | returns `result`, `details`, `error { details }` |
| `aiNotificationsTestDestinationById(accountId, destinationId)` | an existing destination | same return |
| `aiNotificationsTestChannel(accountId, channel: {type, name, properties})` | a channel definition, before or after creation | sends a real notification to the third party |
| `aiNotificationsTestChannelById(accountId, channelId)` | an existing channel | sends a real notification |
| `aiWorkflowsTestWorkflow(accountId, testWorkflowData: {destinationConfigurations, issuesFilter})` | a workflow definition | finds a past issue that matches the filter and sends a fake notification; returns an error when no past issue matches. Returns `status`, `notificationResponses { status evidence channelId }`, `errors` |

Because the channel and workflow tests send real messages (UNVERIFIED for the destination test on every type; the UI "Test connection" buttons exist for Jira, ServiceNow, PagerDuty, mobile push and a "Send test notification" button for the rest, DOCS INT), they fall under the sandbox rule: approval per session, and name the recipient first. The destination test for an EMAIL destination is the one to run first; whether it sends mail is UNVERIFIED.

Supporting queries: `actor.account.aiNotifications.channelSchema(channelType, destinationId, product, constraints)` returns the mandatory keys; `channelSuggestions` returns Jira projects, issue types, Teams teams and channels, PagerDuty services. DOCS NG-CHAN. `actor.account.aiWorkflows.workflows(filters: {name | id | destinationType})` lists workflows. DOCS NG-WF.

## 1.5 Corrected mapping: source target to New Relic

Effort codes: auto (the importer can create it from what the source exports), secret (a person supplies a secret or address the source does not export), OAuth (a browser sign-in), template (a webhook payload must be written for the receiver), none (no sensible route).

### AWS SNS subscription protocols

| SNS protocol | Best New Relic destination | Effort | What the REPO does today |
|---|---|---|---|
| email, email-json | EMAIL | auto (the address is in the subscription) | EMAIL, auto. Matches. REPO `sources/cloudwatch/notifications.py` |
| https | WEBHOOK | auto for the destination, but template: an SNS HTTPS endpoint expects the SNS JSON envelope and a confirmation handshake; New Relic sends its own payload and cannot answer the handshake. UNVERIFIED for the receiver | WEBHOOK with a fixed 4-field payload (`emit/notifications.py:WEBHOOK_PAYLOAD`), auto. The report does not say the receiver may not understand it |
| http | WEBHOOK to an https URL | the docs only describe https endpoints for the webhook notifier (DOCS INT, "specified HTTPS endpoint"); plain http UNVERIFIED | not migrated, matches |
| sms | none directly. A WEBHOOK to an SMS provider API (secret + template), or PagerDuty or Slack | none or template | NO_EQUIVALENT. Reasonable |
| lambda | EVENT_BRIDGE (rule target Lambda), or WEBHOOK to a Lambda function URL or API Gateway | needs a person: AWS account id and region, associate the partner event source with a bus, add a rule. Template for the webhook route | NO_EQUIVALENT. `docs/what-migrates.md` says "No New Relic equivalent": that is wrong, EVENT_BRIDGE is documented (DOCS INT, DST) |
| sqs | EVENT_BRIDGE (rule target SQS) | needs a person (AWS side) | NO_EQUIVALENT. Same correction |
| firehose | EVENT_BRIDGE (rule target Firehose) | needs a person (AWS side) | NO_EQUIVALENT. Same correction |
| application (mobile push endpoint) | none. MOBILE_PUSH is only the New Relic mobile app for one user | none | NO_EQUIVALENT. Matches |

### GCP notification channel types

| Google type | Best New Relic destination | Effort | REPO today (`sources/gcp/notifications.py`) |
|---|---|---|---|
| email | EMAIL | secret (the address is not exported) | text only, no object. Matches the effort |
| webhook_tokenauth | WEBHOOK with `auth_token` | secret (URL and token not exported) | text only |
| webhook_basicauth | WEBHOOK with `auth_basic` | secret (URL, user, password) | text only |
| slack | SLACK | OAuth | text only. Matches |
| pagerduty | PAGERDUTY_SERVICE_INTEGRATION (a Google PagerDuty channel holds a service key) | secret | text only |
| sms | none (webhook to an SMS provider) | none or template | NO_DESTINATION |
| pubsub | none directly. A webhook to the Pub/Sub REST publish needs a short-lived Google token, so it does not fit. UNVERIFIED | none | NO_DESTINATION |
| mobile (Google Cloud mobile app) | none | none | NO_DESTINATION |
| campfire | WEBHOOK to the chat service (template) | template | NO_DESTINATION |
| other Google types (for example Teams, Jira, Opsgenie types if present) | MICROSOFT_TEAMS, JIRA, WEBHOOK | UNVERIFIED which types the API returns | falls to NO_DESTINATION |

### Dynatrace integration types

| Dynatrace | Best New Relic destination | Effort | REPO today (`sources/dynatrace/notifications.py`) |
|---|---|---|---|
| email | EMAIL | auto (recipients are in the workflow task) | EMAIL, auto. Matches |
| slack | SLACK | OAuth | SLACK manual. Matches |
| pagerduty | PAGERDUTY_SERVICE_INTEGRATION (or ACCOUNT with a REST key) | secret | SERVICE_INTEGRATION manual. Matches |
| jira | JIRA | secret (site URL, user email, API token) plus project and issue type ids | JIRA manual. Matches |
| ms teams | MICROSOFT_TEAMS | a person installs the Teams app once and reads the security code; then secret | WEBHOOK, with the claim "not confirmed". Differs |
| servicenow | SERVICE_NOW_APP (the certified application) preferred; SERVICE_NOW is legacy and in maintenance mode | secret, plus the app installed in ServiceNow | SERVICE_NOW. Differs from the documented preference (DOCS INT) |
| http request | WEBHOOK | secret and template | WEBHOOK manual. Matches |
| opsgenie, victorops, xMatters, others | WEBHOOK with the vendor's template | template and secret | NO_EQUIVALENT. Could be WEBHOOK (DOCS INT) |

### Grafana contact point types (the repo has no Grafana contact point conversion yet; REPO `docs/what-migrates.md` says not done)

| Grafana contact point | Best New Relic destination | Effort |
|---|---|---|
| email | EMAIL | auto if the addresses are exported |
| webhook | WEBHOOK | secret (URL, auth) and template (Grafana payload differs) |
| slack | SLACK | OAuth |
| pagerduty | PAGERDUTY_SERVICE_INTEGRATION | secret |
| opsgenie, victorops | WEBHOOK (vendor templates are documented, DOCS INT) | template and secret |
| teams | MICROSOFT_TEAMS | Teams app install, then code |
| jira | JIRA | secret |
| sns, kafka, telegram, discord, googlechat, line, pushover, threema, webex, wecom, sensugo, oncall, others | WEBHOOK where the target has an HTTP API, otherwise none | template and secret, or none |

The Grafana list is from memory. UNVERIFIED.

## 1.6 What a user can supply, and what stays human

| Input the user gives | Fully automatable by the importer | Still needs a human |
|---|---|---|
| A New Relic user API key and account id only | Policies, NRQL conditions, muting rules, disabled workflows, the workflow with its filter, EMAIL destinations and channels for addresses already exported, WEBHOOK destinations with no auth, WORKFLOW_AUTOMATION. Reading existing Slack destinations by id. Test mutations (with approval) | Anything that needs a secret that the source does not export; OAuth; Teams app install; AWS side of EVENT_BRIDGE; deciding to enable |
| A webhook URL (and optional token or basic credentials) | WEBHOOK destination with `secure_url` and the auth block, a channel with a payload template, the workflow | Writing a payload the receiver understands; receiver confirmation if any |
| An email address (or a list) | EMAIL destination (up to 5 free, 150 paid per destination) and channel and workflow | Possibly recipient verification (UNVERIFIED for API creation) |
| A PagerDuty integration key | PAGERDUTY_SERVICE_INTEGRATION destination and channel and workflow | none after the key |
| A PagerDuty REST key | ACCOUNT destination; the channel needs the service id and user email (found with `channelSuggestions`) | choosing the service |
| A Jira API token, site URL, email | JIRA destination | choosing project and issue type (ids by `channelSuggestions`) |
| A Teams security code | MICROSOFT_TEAMS destination; channel after `channelSuggestions` picks team and channel | installing the Teams app, posting the code |
| Slack | nothing | the OAuth click per workspace and account, then the importer can create channels for a given channel id |

---

# Part 2. NRQL alert conditions

## 2.1 Conflicts between New Relic pages, and the working rule

| Item | Sources | Working rule for the importer |
|---|---|---|
| Aggregation window range | STR (live page and repo copy): 30 seconds to 6 hours. TF-NRQL: at least 30, at most 21600 (6 hours). NQC (live page): "30 seconds to 120 minutes, in increments of 15 seconds". SCHEMA/TF-GO do not validate it (server enforces) | Keep 30..3600 seconds, which sits inside every reading. Do not widen until a live test shows the server accepts more. Default 60. DOCS STR, NQC; REPO `common.py` caps 30..3600 |
| Window granularity | NQC: increments of 15 seconds. Others silent | UNVERIFIED; the importer emits whole windows of 30 s or more |
| Threshold duration range | TF-NRQL, SCHEMA: static 60..86400 seconds, baseline 120..86400, a multiple of the aggregation window. LIM: "Duration for condition alert event: 30 seconds to 2 hours" | Keep 60..86400 as REPO does. The LIM row looks like an old limit. UNVERIFIED; a live test with a 3-hour duration decides |
| Aggregation timer range | STR: default 60 s, minimum 5 s, maximum 20 min. TF-NRQL: 0 to 1200 s, default 60 | Use 5..1200 |
| Aggregation delay range | STR: default 2 min. TF-NRQL: 0..1200 s for EVENT_FLOW, 0..3600 s for CADENCE, default 120 | 0..1200 for EVENT_FLOW |
| Default violation time limit | AC page: "defaults to 3 days". NG-NRQL examples use `violationTimeLimitSeconds: 86400`. TF-GO sets a default constant (`violationTimeLimitSecondsDefault`, value not read) and a comment that it matches the NerdGraph default | UNVERIFIED. Set it explicitly. Terraform range 300..2592000 seconds (30 days) |
| Max facets | NQC, LIM: 20,000 for NRQL conditions (1,000 for non-NRQL) | 20,000. If a query returns more, creation fails; if it grows later, the condition fails |

## 2.2 Settings and their semantics

### Signal block

- `aggregation_window` / `signal.aggregationWindow`: seconds of data collected before evaluation. Default 60. DOCS STR, SCHEMA (Optional, Computed).
- `aggregation_method` / `signal.aggregationMethod`: `EVENT_FLOW` (default), `EVENT_TIMER`, `CADENCE`. DOCS STR, TF-GO.
  - EVENT_FLOW: closes a window when the first data point of a later window arrives; the delay decides how many windows later. Uses data timestamps. For data that arrives frequently and mostly in order. If points can be more than 65 minutes apart, use EVENT_TIMER. DOCS STR.
  - EVENT_TIMER: starts a per-window timer on the first point and resets on each further point; the window closes when the timer runs out. For infrequent or batched data, "such as cloud integration data or infrequent error logs". Make the timer at least the window length. DOCS STR.
  - CADENCE: wall-clock based, the original method, described as inferior; NQC says to use event flow or event timer. DOCS STR, NQC.
  - NQC also says: for data from Infrastructure Cloud Integrations (CloudWatch, Azure) use event timer. DOCS NQC. For APM agent data (Transaction, TransactionError) use event flow with defaults. DOCS NQC.
  - The method and `nrql.evaluation_offset` / `since_value` cannot be set together (deprecated pair). DOCS TF-NRQL.
- `aggregation_delay`: used by EVENT_FLOW and CADENCE. Default 120 s. DOCS STR, TF-NRQL. In Terraform the attribute type is string (SCHEMA), so a number is converted.
- `aggregation_timer`: used by EVENT_TIMER only. Default 60 s. DOCS STR.
- `evaluation_delay`: delays the start of evaluation for a new signal, maximum 7200 s; 0 or omitted turns it off. Meant for new autoscaled entities. DOCS TF-NRQL, NQC. The `evaluationDelay` NerdGraph node is under `signal`. DOCS NQC.
- `evaluation_offset`, `since_value`: deprecated; the legacy way to set a delay in minutes (1..20). Do not use. DOCS TF-NRQL, SCHEMA.
- `slide_by`: seconds of overlap between windows, must divide the aggregation window and be smaller than it; at least 30 s when the window is 3600 s or less. Costs more data points; the first full window builds a buffer during which no alert opens. Compute-pricing customers can pay extra. DOCS NQC, TF-GO description, STR.
- `fill_option` / `fill_value`: `NONE` (default), `STATIC` (needs `fill_value`, defaults to 0 in the UI) and `LAST_VALUE` (kept for 2 hours or the threshold duration if longer). With NONE, an empty window resets the threshold-duration timer. Gap filling happens only after a later data point has arrived, and it stops once a gap is longer than the expiration duration. For anomaly thresholds the fill value is read as standard deviations. DOCS NQC, NG-GAP, AC.
- Any change to the query, window, method, delay/timer, fill, thresholds, direction, slide-by or the close-on-expiration setting resets the evaluation: a "for at least N minutes" condition cannot fire until N minutes have passed. DOCS NQC.

### Expiration (loss of signal)

- `expiration_duration`: seconds after the last data point arrived (arrival time, not data timestamp) before the signal counts as lost. 30 to 172,800 s (48 h) inclusive. The docs recommend at least 3 to 5 minutes. Null means loss of signal is off. Independent of the threshold duration. In a faceted query every facet is its own signal. DOCS NQC, NG-GAP, TF-GO (`IntBetween(30, 172800)`).
- `open_violation_on_expiration`: open a lost-signal event. Default false. Needs a duration.
- `close_violations_on_expiration`: close the open events of that signal. Default false. Use it for ephemeral or sporadic signals. If both are on, it closes first and then opens.
- `ignore_on_expected_termination`: do not open a lost-signal event when the entity carries the tag `termination: expected` (or `hostStatus: shutdown` for hosts). SCHEMA exposes it as a top-level Terraform attribute.
- Loss of signal needs a signal to exist first: enabling a condition with no data active will not detect it. It does not work with nested aggregation or subqueries. An event opened by lost signal closes when data returns, at condition expiry (3 days default), or on manual close. DOCS NQC.
- Title templates are not applied to lost-signal events; the default title is used. DOCS TTL.

### Terms (thresholds)

- At most two terms: one `CRITICAL` and one optional `WARNING`. DOCS LIM, TF-NRQL, SCHEMA (`term` max 2).
- `operator`: above, above_or_equals, below, below_or_equals, equals, not_equals (case-insensitive in Terraform; Terraform default is `equals`, so always set it). Baseline allows only `above`. DOCS TF-NRQL. NerdGraph names are ABOVE, BELOW, ABOVE_OR_EQUALS, BELOW_OR_EQUALS, EQUALS, NOT_EQUALS (NG-NRQL shows ABOVE and BELOW; the rest by pattern, UNVERIFIED).
- `threshold_duration`: seconds, a multiple of the aggregation window; static 60..86400, baseline 120..86400. DOCS TF-NRQL, TF-GO.
- `threshold_occurrences`: `ALL` ("for at least") or `AT_LEAST_ONCE` ("at least once in"). Switching between them does not reset evaluation. DOCS NQC, TF-GO.
- `valueFunction` (NerdGraph, static only): `SINGLE_VALUE` used by REPO; the NerdGraph schema browser lists the choices. DOCS NG-NRQL. Not in the Terraform schema.
- Per-term `disable_health_status_reporting` and `disable_event_creation` (beta), and `prediction` (beta, static only: `predict_by` seconds, default 3600, at most 360 windows ahead; `prefer_prediction_violation`). SCHEMA, DOCS AC, TF-NRQL.
- Baseline (anomaly) conditions: `type = "baseline"`, `baseline_direction` is `UPPER_ONLY`, `LOWER_ONLY` or `UPPER_AND_LOWER`; the threshold is a number of standard deviations from 1 to 1000; operator is `above`; `signal_seasonality` is `NEW_RELIC_CALCULATION`, `HOURLY`, `DAILY`, `WEEKLY` or `NONE` (baseline only). Default UI breach rule: three standard deviations for five minutes. Anomaly learning is lost on any evaluation reset. DOCS TF-NRQL, NG-NRQL, AC, NQC.
- Outlier: `type = "outlier"` with `outlier_configuration.dbscan { epsilon > 0, minimum_points >= 1, evaluation_group_facet }`. Marked beta preview and enabled per account in the schema text. SCHEMA, DOCS AC.
- `violation_time_limit_seconds`: force-closes a long-running event, 300..2,592,000 s. `violation_time_limit` (hours enum) is deprecated and conflicts with it. SCHEMA, TF-GO. TF-NRQL says one of the two must be set; TF-GO gives the seconds attribute a default, so omitting both is accepted by the provider. UNVERIFIED for the NerdGraph default (see 2.1).

### Text fields

- `name`: 1..128 characters. DOCS LIM.
- `description`: a Handlebars template, 4,000 characters, for NRQL and infrastructure conditions. It can use alert-event attributes `{{conditionName}}`, `{{targetName}}`, and tags as `{{tags.fullHostname}}`. DOCS DSC.
- `title_template`: Handlebars, 256 characters, NRQL only; facet values as `{{tags.hostName}}`; not used for lost-signal events. DOCS TTL.
- `runbook_url`: shown in notifications as `accumulations.runbookUrl`. DOCS TF-NRQL, VAR.
- Tags are entity tags, managed with `newrelic_entity_tags` after creation, not a condition attribute. DOCS TF-NRQL ("Tags"), NQC.
- `target_entity` is a limited-release beta (GUID of the entity health is attributed to). SCHEMA.
- `nrql.data_account_id` allows a cross-account query. SCHEMA, DOCS AC.

## 2.3 NRQL in a condition

- Supported functions that return numbers: apdex, average, count, latest, max, min, percentage, percentile, sum, uniqueCount. Data types: events and `Metric` (raw data points). DOCS NQC. Percentile over many facets can fail with a chart-data error; use average. DOCS NQC.
- Not usable, with the page's own advice (DOCS NQC):
  - `SINCE` and `UNTIL` (stripped when a condition is made from a chart; the window comes from the aggregation settings).
  - `TIMESERIES` (replaced by the aggregation window, or the slide-by value with sliding windows).
  - `PREDICT` (replaced by the "predict future behavior" threshold option).
  - `histogram()`; use `percentile()`.
  - `bytecountestimate()` and `cardinality()`: not yet supported.
  - Several aggregation functions in one query: one condition per value.
  - `COMPARE WITH`: use an anomaly (baseline) condition.
  - `LIMIT`: evaluation always uses the full result.
  - Subqueries and subquery JOINs.
  - Nested aggregation has restrictions: the inner query must be faceted, the same window on both levels, no loss of signal, and no `WITH METRIC_FORMAT`, `metricTimesliceName`, `keyset`, `uniques`, `newrelic.timeslice.value` or `apm.*` timeslice names in the inner query.
- Faceted queries accept no `LIMIT`; maximum 20,000 values. A query scoped to one entity (a `WHERE` or a `FACET` on the entity) is needed for the entity health status to work. DOCS NQC.
- `rate()`, `derivative()`, `predictLinear()`: not mentioned in the condition pages or the unsupported list, and not in the supported function list. Whether they work inside a condition is UNVERIFIED and needs a live test. The Grafana remediation text in REPO (`sources/grafana/remediation_rules.py:187`) already suggests `rate(sum(metric), 1 minute)` for counters, so this matters.
- Percentile in a condition is supported: `SELECT percentile(duration, 95)`. DOCS NQC.
- `count(*)` on an empty window. First-hand now (this closes the "second-hand" gap in `count-null-empty-window.md`): the query runs `FROM`, then `WHERE`, then `SELECT`. When no row survives the `WHERE`, `SELECT` does not run, so `count()` and `uniqueCount()` return NULL, never 0. DOCS NQC ("NRQL conditions and query order of operations").
  - Documented fix: `SELECT filter(count(*), WHERE result = 'FAILED') FROM SyntheticCheck WHERE monitorName = 'X'`. The outer `WHERE` must name at least one entity so the signal is tied to it; a window with that entity reporting returns 0. If no event at all arrives, the NULL cannot be avoided; use a lost-signal threshold. DOCS NQC.
  - An empty window with `fill_option NONE` resets the threshold-duration timer. DOCS NQC.
  - Whether `fill_option STATIC` with `fill_value 0` also cures it is UNVERIFIED: NG-GAP says gaps are filled only after a later data point arrives, which is exactly what is missing. A live test decides.
  - Consequence for the importer: a `below N` condition on `SELECT count(*) ... WHERE <filter>` will not evaluate in empty windows. A heartbeat (absence) condition, which relies on NULL plus `expiration`, must not be wrapped in `filter()`. Both match the earlier research note.

## 2.4 What the repo hard-codes or never sets

Emitters read: `core/emit/nerdgraph.py`, `core/emit/terraform.py`, `core/emit/common.py`, `core/emit/nrql.py`, `core/emit/notifications.py`.

| Setting | REPO today | Risk |
|---|---|---|
| `aggregationMethod` | `EVENT_FLOW` for every source (`nerdgraph.py:44`, `terraform.py`) | DOCS NQC says event timer for cloud-integration (CloudWatch, GCP, Azure) data. GCP metrics are polled every 5 minutes (REPO `gcp-native-coverage.md`). With a 60 s window and fill NONE most windows are empty, and an empty window resets the threshold-duration timer, so an `ALL` condition over 5 minutes can never fire. Pick window and method per source |
| `aggregationDelay` | 120 s (`AGGREGATION_DELAY_SECONDS`) unless the signal sets it | Matches the default. With EVENT_TIMER the timer is the setting and the delay does not apply (DOCS STR); sending both is UNVERIFIED |
| `aggregationWindow` | 60 s default; accepted range 30..3600 | Within every documented range. Longer windows (to 6 h per STR, TF-NRQL) are rejected by the emitter |
| `valueFunction` | `SINGLE_VALUE`; removed for baseline | Fine |
| Terms | priorities from severity; `thresholdDuration` = the whole breach window for every term; occurrences from the source | Correct, but the emitter does not check the two-term cap (UNVERIFIED whether the translators do) |
| `fillOption`, `fillValue` | never set (NONE) | The sparse-data and zero-count cases above |
| `slideBy` | never set | None unless the source used a sliding window |
| `expiration` | set only when the source notifies on no data: `expirationDuration = minutes*60`, `openViolationOnExpiration = true`, `closeViolationsOnExpiration = false` | No lower bound check (30 s) and no upper bound check (172,800 s): a source value over 48 hours fails at the API. `closeViolationsOnExpiration` false leaves lost-signal events open until data returns |
| `ignoreOnExpectedTermination` | never set | Low |
| `violationTimeLimitSeconds` | never set | Server default is unverified (see 2.1); a very long default can hold events open |
| `titleTemplate`, `runbookUrl` | never set | The title is the condition name for every entity of a faceted condition. Source runbook links are dropped (the Dynatrace remediation rule says so). Setting `runbook_url` is cheap |
| `description` | `Imported from <label> <id>` | Fine; below the 4,000 limit |
| Condition name | printable, 128 characters | Matches LIM |
| `enabled` | false | Intended |
| Policy | NerdGraph: `incidentPreference: PER_CONDITION` (`apply.py:216`). Terraform: `newrelic_alert_policy` with no `incident_preference` (`terraform.py` HEADER), whose default is PER_POLICY (SCHEMA/DOCS: "The default is PER_POLICY") | PER_POLICY gives one open issue for the whole policy, and every converted condition is in one policy, so later conditions join an already-open issue and send no new notification. The two paths disagree |
| `evaluation_delay` | never set | Low |
| Tags | none | Entity tags for routing (team, source system) are not set, so tag-based workflow filters have nothing to match |

Workflow and destination emitters:

| Item | REPO today | Risk |
|---|---|---|
| Teams | WEBHOOK (Dynatrace) | MICROSOFT_TEAMS exists |
| Issue filter | `accumulations.conditionName EXACTLY_MATCHES [names]` | Correct operator for a list attribute. The filter is capped at 4,096 characters; a topic with many or long condition names overflows. No length check |
| Dynatrace tag filter | `tags.<name>` | Not a documented issue attribute; use `accumulations.tag.<name>` and test |
| `mutingRulesHandling` | `NOTIFY_ALL_ISSUES` always | Muting rules, once migrated, will not reduce notifications |
| `notificationTriggers` | omitted | Relies on server defaults (UNVERIFIED); closed and acknowledged coverage cannot be chosen |
| Webhook channel | `payload` only, no `headers` | Fine for the NR default; the receiver's required headers cannot be set |
| Email destination | one address per destination, subject `{{ issueTitle }}` | Allowed (up to 150 per destination); not wrong |
| Workflow names | `workflowExists` by name | Matches the "must be unique" rule |
| Enrichments | none | Optional |

---

# Part 3. What the repo should change, ranked

1. Terraform policy: add `incident_preference = "PER_CONDITION"` to the `newrelic_alert_policy` in `emit/terraform.py` HEADER, to match `apply.py`. Silent loss of notifications otherwise. Needs no live test; confirm in plan output.
2. Set the aggregation method and window from the source. Use `EVENT_TIMER` (with `aggregationTimer`, not delay) for CloudWatch Metric Streams, GCP polled and Dynatrace/Grafana data that arrives in batches, and make the GCP window at least the 5 minute polling interval. Keep `EVENT_FLOW` for APM and logs. Needs a live test (see below).
3. Replace the Teams mapping with MICROSOFT_TEAMS in `sources/dynatrace/notifications.py`; keep the manual step (install the app, read the security code, then pick team and channel). Update `docs/what-migrates.md` and the report text.
4. Fix `tags.<name>` to `accumulations.tag.<name>` for workflow issue filters, and keep `tags.<name>` for muting rules and title templates. Needs a live test.
5. Add a 4,096-character check for the workflow issue filter. When the condition-name list is too long, split into several workflows (channel ids are not reusable, so each needs its own channels) or filter by policy once policies are per topic.
6. Correct the SNS wording: Lambda, SQS and Firehose have a New Relic route through EVENT_BRIDGE (plus an AWS-side step), and SMS can go through a webhook to a provider. State that an SNS https subscriber is sent New Relic's payload, not the SNS envelope.
7. Choose `mutingRulesHandling` from whether muting rules are migrated: `DONT_NOTIFY_FULLY_MUTED_ISSUES` when they are.
8. Validate expiration (30..172,800 s), terms (max two, one critical), and set `violationTimeLimitSeconds` and `runbookUrl` explicitly.
9. Set `notificationTriggers` explicitly (ACTIVATED, ACKNOWLEDGED, CLOSED at least) after a live check of the server defaults.
10. Apply the `filter(count(*), WHERE ...)` form to `below`/`below_or_equals` log counts only after the live tests below; do not touch heartbeat queries.
11. Dynatrace ServiceNow: prefer SERVICE_NOW_APP (or a webhook to the certified app); keep SERVICE_NOW as the legacy fallback.
12. Add optional `headers` to the webhook channel for receivers that need them, and a Teams/Slack/PagerDuty/Jira/Opsgenie template set for common targets.

## Live tests needed in a sandbox (create-only, approvals per session, no secrets printed)

1. Create a policy via Terraform with and without `incident_preference` and read back the value. Confirm the default.
2. Create a static NRQL condition with `aggregationWindow` 7200 and 21600, and with `thresholdDuration` 10800; record which values the server accepts. Resolves the 120-minute vs 6-hour and 2-hour conflicts.
3. Read the default `violationTimeLimitSeconds` of a condition created without it.
4. A `SELECT count(*) FROM Log WHERE ...` condition with `below 1` and a silent window; then the `filter(count(*), WHERE ...)` form; then `fill_option STATIC` with `fill_value 0`; observe the opened events (extends the plan in `count-null-empty-window.md`).
5. Try `rate(sum(x), 1 minute)`, `derivative(x, 1 minute)` and `predictLinear(x, 1 hour)` in a disabled condition create call; record accept or reject.
6. A workflow with filters `accumulations.tag.team`, `tags.team`, `tag.team`, `labels.tags.team` on an entity tagged `team` that raised an issue; use `aiWorkflowsTestWorkflow` (needs approval, sends a real notification to a chosen test destination) to see which match.
7. A workflow created without `notificationTriggers`; read back the triggers.
8. Create the EMAIL destination and run `aiNotificationsTestDestination`; note whether mail is sent and whether verification is required.
9. Create a MICROSOFT_TEAMS destination with a real security code (needs a person with a Teams workspace) and read the `channelSuggestions` for teams and channels.
10. Create a muting rule and read it back with each of the operator spellings in 1.3 to settle `NOT_EQUALS` against `DOES_NOT_EQUALS`; then test that a workflow with `DONT_NOTIFY_FULLY_MUTED_ISSUES` suppresses a notification and one with `NOTIFY_ALL_ISSUES` does not.
11. EVENT_BRIDGE: create the destination with and without an auth block and see which the API accepts.
12. Webhook channel with `headers`, and check what HTTP method is used (docs say POST only).

---

# Appendix. Provider schema extracts (SCHEMA, provider 3.101.12)

`R` required, `O` optional, `C` computed, `OC` optional and computed. Types are Terraform types.

- `newrelic_notification_destination`: account_id OC number; active O bool; guid C; id OC; last_sent C; name R; status C; type R. Blocks: `auth_basic` (max 1: user R, password R sensitive), `auth_custom_header` (list: key R, value R sensitive), `auth_token` (max 1: prefix O, token R sensitive), `property` (set, min 1: key R, value R, label O, display_value O), `scope` (max 1: id R, type R), `secure_url` (max 1: prefix R, secure_suffix R sensitive), `timeouts`.
- `newrelic_notification_channel`: account_id OC; active O; destination_id R; id OC; name R; product R; status C; type R. Block `property` (set, min 1: key R, value R, label O, display_value O), `timeouts`.
- `newrelic_workflow`: account_id OC; destinations_enabled O (deprecated); enabled O; enrichments_enabled O; guid C; id OC; last_run C; muting_rules_handling R; name R; workflow_id C. Blocks: `destination` (set, min 1: channel_id R, name C, notification_triggers OC list(string), type C, update_original_message OC bool), `enrichments` (set, max 1, containing `nrql` list min 1: name R, account_id C, enrichment_id C, type C, `configuration` list min 1: query R), `issues_filter` (set, exactly 1: name R, type R, filter_id C, `predicate` list: attribute R, operator R, values R list(string)).
- `newrelic_alert_muting_rule`: account_id OC; action_on_muting_rule_window_ended OC; description O; enabled R; id OC; name R. Blocks: `condition` (list, exactly 1: operator R; `conditions` list min 1: attribute R, operator R, values R list(string)), `schedule` (max 1: end_repeat O, end_time O, repeat O, repeat_count O number, start_time O, time_zone R, weekly_repeat_days O set(string)).
- `newrelic_nrql_alert_condition`: account_id OC; aggregation_delay O string; aggregation_method O; aggregation_timer O string; aggregation_window OC number; baseline_direction O; close_violations_on_expiration O; description O; enabled O; entity_guid C; evaluation_delay O number; expiration_duration O; fill_option O; fill_value O; id OC; ignore_on_expected_termination O; name R; open_violation_on_expiration O; policy_id R number; runbook_url O; signal_seasonality O; slide_by O; target_entity O (beta); title_template O; type O; violation_time_limit OC (deprecated); violation_time_limit_seconds O. Blocks: `critical` / `warning` (max 1 each) and `term` (set, max 2; with `priority`), each with disable_event_creation, disable_health_status_reporting, operator, threshold R, threshold_duration, threshold_occurrences, deprecated duration and time_function, and a `prediction` block; `nrql` (exactly 1: query R, data_account_id OC, deprecated evaluation_offset and since_value); `outlier_configuration.dbscan` (epsilon R, minimum_points R, evaluation_group_facet O); `timeouts`.
- `newrelic_alert_policy`: account_id OC; channel_ids O (deprecated); entity_guid C; id OC; incident_preference O (default PER_POLICY, DOCS provider text; options PER_POLICY, PER_CONDITION, PER_CONDITION_AND_TARGET); name R.
- `newrelic_service_level`: description, guid R, id, name R, sli_guid C, sli_id C; `events` (exactly 1: account_id R, facets O, `valid_events` R, `good_events` O, `bad_events` O, each with from R, where O and `select` function R / attribute O / threshold O); `objective` (exactly 1: target R, name, description, `time_window.rolling` count R and unit R).
- Synthetics: `newrelic_synthetics_monitor` (name R, status R, type R SIMPLE or BROWSER, uri, period, locations_public, locations_private, validation_string, verify_ssl, bypass_head_request, treat_redirect_as_failure, custom_header, tag, browsers, devices, runtime_type, script_language and others); `newrelic_synthetics_script_monitor` (name R, period R, status R, type R, script, locations_public, `location_private`, runtime fields, tag); `newrelic_synthetics_step_monitor` (name R, period R, status R, `steps` min 1: ordinal R, type R, values); `newrelic_synthetics_cert_check_monitor` (name R, domain R, certificate_expiration R, period R, status R); `newrelic_synthetics_broken_links_monitor` (name R, uri R, period R, status R); `newrelic_synthetics_alert_condition` (monitor_id R, name R, policy_id R, enabled, runbook_url); `newrelic_synthetics_multilocation_alert_condition` (entities R, name R, policy_id R, `critical` R threshold, optional `warning`, violation_time_limit_seconds, runbook_url). Period values: EVERY_MINUTE, EVERY_5_MINUTES, EVERY_10_MINUTES, EVERY_15_MINUTES, EVERY_30_MINUTES, EVERY_HOUR, EVERY_6_HOURS, EVERY_12_HOURS, EVERY_DAY (SCHEMA text on `newrelic_synthetics_monitor`).
- `newrelic_one_dashboard`: account_id OC; description; name R; permissions; `page` (min 1) with widget blocks (`widget_area`, `widget_bar`, `widget_billboard`, and others) each with `column` R, `row` R, `title` R, `nrql_query` (query R, account_id), plus `variable` blocks (list: name R, title R, type R, replacement_strategy R, default_values, is_multi_selection, `item` title/value, `nrql_query` query R and account_ids).
- `newrelic_workflow_automation`: name R, definition R (YAML), scope_id R, scope_type R; computed definition_id, version, yaml, description.
