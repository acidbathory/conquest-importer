# Grafana notifications and the data path: what a translator needs

Scope: contact points, notification policies, mute timings, templates, imported Alertmanager and
ruler rule files, and how Prometheus, Loki and Tempo data reaches New Relic so the converted NRQL
has something to read. Public documentation and public source only. No Grafana or New Relic
instance was called, no `.env` was read, no cloud command was run. Written 2026-11-09.

## 0. Evidence keys

Every claim carries one of: DOCS (a page read, key below), CODE (a source file read, key below),
REPO (this repository), PRIOR (taken from the other session's file
`docs/research/grafana-native-coverage.md`, which I did not re-read at the source; treat as
unverified by this session), UNVERIFIED (memory or inference).

Code keys (raw files on the default branch, fetched 2026-11-09):

- C1: grafana/alerting `receivers/<type>/v1/config.go` (the `Schema` variable lists every field with
  `PropertyName`, `Secure`, `Required`, `Protected`).
- C2: grafana/alerting `receivers/schema/known_types.go` (type strings).
- C3: grafana/alerting `definition/alertmanager.go` (the `Route` struct).
- C4: prometheus/alertmanager `dispatch/route.go` (route inheritance and matching).
- C5: prometheus/alertmanager `pkg/labels/matcher.go` (matcher semantics).
- C6: prometheus/alertmanager `timeinterval/timeinterval.go` (time interval semantics).
- C7: grafana/grafana `pkg/services/ngalert/api/tooling/definitions/provisioning_contactpoints.go`.
- C8: grafana/grafana `pkg/services/ngalert/models/receivers.go` (`Redact`, `SecureFields`).
- C9: grafana/alerting `templates/template_data.go` (template data model).
- C10: grafana/alerting `receivers/delimited_strings.go`.

Docs keys:

- D1: newrelic/docs-website `alerts/get-notified/notification-integrations.mdx`.
- D2: `alerts/get-notified/message-templates.mdx` (Handlebars helpers).
- D3: `alerts/get-notified/custom-variables-alert-event-workflows.mdx` (variable list).
- D4: `alerts/get-notified/muting-rules-suppress-notifications.mdx`.
- D5: New Relic Terraform provider docs `website/docs/r/`: `workflow`, `notification_destination`,
  `notification_channel`, `alert_muting_rule`.
- D6: `infrastructure/prometheus-integrations/.../set-your-prometheus-remote-write-integration.mdx`.
- D7: `opentelemetry/best-practices/opentelemetry-otlp.mdx` (OTLP endpoint).
- D8: docs.newrelic.com "Translate PromQL queries to NRQL".
- D9: docs.newrelic.com "Supported PromQL features".
- D10: docs.newrelic.com "Configure New Relic as a Prometheus data source for Grafana".
- D11: `data-apis/understand-data/metric-data/metric-data-type.mdx`.
- D12: `logs/log-api/introduction-log-api.mdx`.
- D13: grafana/alloy reference docs: `prometheus.remote_write`, `otelcol.exporter.otlphttp`,
  `otelcol.auth.headers`, `otelcol.receiver.loki`.
- D14: grafana/mimir `docs/sources/mimir/references/http-api/_index.md`.
- D15: grafana/loki `docs/sources/reference/loki-http-api.md`.
- D16: prometheus/prometheus `docs/configuration/alerting_rules.md`.
- D17: prometheus/alertmanager `docs/configuration.md`.
- D18: prometheus/docs `docs/guides/opentelemetry.md`.

Known gap in my own method: the docs-website paths are the `develop` branch; some pages are
rendered HTML on docs.newrelic.com. Pages that returned 404 are listed in section 9.

## 1. Contact points

### 1.1 What the API gives and what is hidden

- Object: `{uid, name, type, settings, disableResolveMessage, provenance}`. Several integrations
  share one `name`. CODE C7.
- Export groups by name: `{orgId, name, receivers:[{uid, type, settings, disableResolveMessage}]}`.
  CODE C7.
- Redaction. A secure field that holds a value comes back in `settings` as the literal string
  `[REDACTED]`; unset fields stay empty or absent. `decrypt=true` on the export endpoint returns
  the real values. CODE C7 (`RedactedValue = "[REDACTED]"`), C8 (`Redact` replaces a non-empty
  string at each secret path; empty strings are left as they are).
- The `/apis` receivers API returns `secureFields: {<name>: true}` and no value. PRIOR.
- Settings that are not marked secure are returned in clear, including a `webhook` `headers` map
  and URLs. Both can carry secrets that the user put there (an API key in a header, a token in a
  query string) and Grafana cannot know. CODE C1 (`headers` has no `Secure` flag); inference for
  the consequence.
- The provisioning contact-point model documents an enum that is stale (it omits `jira`, `sns`,
  `mqtt`, `oncall`). Do not validate `type` against it. CODE C7.
- Only v1 integrations are returned by the provisioning API. Integrations from an imported
  Alertmanager config (`v0mimir1`, `v0mimir2`) are not. PRIOR.

### 1.2 Integration types in Grafana 13 (main)

Type strings, CODE C2: `prometheus-alertmanager`, `dingding`, `discord`, `email`, `googlechat`,
`jira`, `kafka`, `LINE` (capitals), `mqtt`, `oncall`, `opsgenie`, `pagerduty`, `pushover`,
`sensugo`, `slack`, `sns`, `teams`, `telegram`, `threema`, `victorops`, `webex`, `webhook`,
`wecom`, `wechat`. `wechat` exists only as a `v0mimir` (imported Alertmanager) integration; it
has no v1 config. CODE C1 (the `wechat` directory has no v1). In which Grafana release each type
appeared (11, 12 or 13) was not checked: UNVERIFIED.

Legend for settings: S = secure (stored encrypted, redacted by the API), P = protected (needs the
protected-write permission to change; value is visible when not S), R = required. Nested fields
are written `parent.child`. Flags are read from the `Schema` blocks in C1. The `Required` flag on
fields that depend on another field (`DependsOn`) is conditional.

| type | settings field names |
|---|---|
| prometheus-alertmanager | url (P,R), basicAuthUser, basicAuthPassword (S) |
| dingding | url (S,P,R), msgType, title, message |
| discord | url (S,P,R), title, message, avatar_url, use_discord_username, use_embed_description |
| email | addresses (R), singleEmail, subject, message |
| googlechat | url (S,P,R), title, message, hide_open_button, hide_version_info |
| jira | api_url (P,R), user (S), password (S), api_token (S), project (R), issue_type (R), summary, description, labels, priority, resolve_transition, reopen_transition, reopen_duration, wont_fix_resolution, dedup_key_field, fields |
| kafka | kafkaRestProxy (P,R), kafkaTopic (R), apiVersion, kafkaClusterId (R for v3), username, password (S), description, details |
| LINE | token (S,R), title, description |
| mqtt | brokerUrl (P,R), topic (R), messageFormat, clientId, message, username, password (S), qos, retain, tlsConfig.insecureSkipVerify, tlsConfig.caCertificate (S), tlsConfig.clientCertificate (S), tlsConfig.clientKey (S) |
| oncall | url (P,R), httpMethod, username, password (S), authorization_scheme, authorization_credentials (S), maxAlerts, title, message |
| opsgenie | apiKey (S,R), apiUrl (P,R), message, description, autoClose, overridePriority, sendTagsAs, responders[] (type, name, id, username) |
| pagerduty | integrationKey (S,R), severity, class, component, group, summary, source, client, client_url, details, url (P) |
| pushover | apiToken (S,R), userKey (S,R), device, priority, okPriority, retry, expire, sound, okSound, uploadImage, title, message |
| sensugo | url (P,R), apikey (S,R), entity, check, handler, namespace, message |
| slack | recipient (R unless url set), token (S), url (S,P), endpointUrl (P), username, icon_emoji, icon_url, mentionUsers, mentionGroups, mentionChannel, color, title, text, footer |
| sns | api_url (P), topic_arn, phone_number, target_arn, subject, message, attributes, sigv4.region, sigv4.access_key (S), sigv4.secret_key (S), sigv4.profile, sigv4.role_arn |
| teams | url (P,R), title, sectiontitle, message |
| telegram | bottoken (S,R), chatid (R), api_url (P), message_thread_id, message, parse_mode, disable_web_page_preview, protect_content, disable_notifications |
| threema | gateway_id (R), recipient_id (R), api_secret (S,R), title, description |
| victorops | url (S,P,R), messageType, title, description |
| webex | room_id (R), bot_token (S,R), api_url (P), message |
| webhook | url (P,R), httpMethod, username, password (S), authorization_scheme, authorization_credentials (S), headers, maxAlerts, title, message, payload.template, payload.vars, tlsConfig (CA, client cert and key are S; subform comes from a shared helper I did not open), hmacConfig.secret (S), hmacConfig.header, hmacConfig.timestampHeader |
| wecom | url (S,P), endpointUrl (P), agent_id (R), corp_id (R), secret (S), msgtype, title, message, touser |

All CODE C1. My extraction script flagged nested fields coarsely, so nested S flags were
re-checked by hand for `mqtt`, `sns` and `webhook` only. The prior session's table (PRIOR 5.1) was built by a text heuristic; this table follows C1. Authoritative at run time: the `integrationtypeschemas` endpoint of the
running Grafana (PRIOR), which the extractor should call once and use instead of this table.

`email.addresses` is a single string split on commas, semicolons and newlines. CODE C10.

Every integration also has `disableResolveMessage` and a `uid`. CODE C7.

### 1.3 New Relic destinations and channels (what exists)

Destination types you can create with Terraform: `EMAIL`, `SERVICE_NOW`, `SERVICE_NOW_APP`,
`WEBHOOK`, `JIRA`, `MOBILE_PUSH`, `EVENT_BRIDGE`, `PAGERDUTY_ACCOUNT_INTEGRATION`,
`PAGERDUTY_SERVICE_INTEGRATION`, `MICROSOFT_TEAMS`, `WORKFLOW_AUTOMATION`. `SLACK` and
`SLACK_COLLABORATION` can only be imported, updated and destroyed, not created. DOCS D5
(notification_destination).

Destination secrets go in dedicated blocks, not in `property`: `auth_basic {user, password}`,
`auth_token {prefix, token}`, `auth_custom_header {key, value}` (several allowed), and
`secure_url {prefix, secure_suffix}` for a URL whose path or query holds a secret. OAuth2 is not
available through Terraform. DOCS D5.

Channel `property` keys by type (channel `product` must be `IINT` for workflows): `WEBHOOK`
`payload` (required) and `headers`; `EMAIL` `subject`, `customDetailsEmail`; `JIRA_CLASSIC`
`project`, `issuetype`, `summary`, `description` (ids, not keys or names);
`PAGERDUTY_SERVICE_INTEGRATION` `summary`, `customDetails`; `PAGERDUTY_ACCOUNT_INTEGRATION`
`summary`, `service`, `email`, `customDetails`; `SLACK` `channelId`, `customDetailsSlack`;
`MICROSOFT_TEAMS` `teamId`, `channelId`, `customDetails`; `EVENT_BRIDGE` `eventSource`. DOCS D5
(notification_channel). The existing emitter writes only EMAIL and WEBHOOK
(REPO `src/importer/core/emit/notifications.py`).

Facts that constrain every webhook template:

- The webhook notifier makes an HTTP POST with a JSON content type; the payload must be valid JSON
  after rendering or it cannot be saved. DOCS D1. No other HTTP method is documented.
- Custom headers can be set on the channel (`headers`) and as encrypted `auth_custom_header` on the
  destination; if the same key is in both, the destination wins. DOCS D1, D5.
- Basic and Bearer authorization are options on the webhook destination. DOCS D1.
- Secrets in the URL path or query: use `secure_url`; it is encrypted and not shown. DOCS D1.
- Whether channel `payload` values are stored encrypted is not documented. Do not put secrets in a
  payload template. UNVERIFIED (that they are not encrypted); the rule is a precaution.

### 1.4 Verdict table

Verdicts: CONVERTS (all non-secret inputs are readable and a native destination or a plain webhook
fits), WEBHOOK TEMPLATE (webhook destination plus a generated Handlebars payload; the secret URL or
header is a manual step), MANUAL STEP (secret or OAuth sign-in, no automated path), NO EQUIVALENT.

| Grafana type | New Relic target | Verdict | Why |
|---|---|---|---|
| email | `EMAIL` destination, property `email`; channel `subject` | CONVERTS | Addresses are not secret. DOCS D5, CODE C1. Limits per destination are PRIOR. |
| webhook | `WEBHOOK`, property `url`; channel `payload` | CONVERTS, with a warning | URL is readable, but receivers of the Grafana body (an Alertmanager-style `alerts[]` JSON) break: NR sends its own shape. HTTP method is fixed to POST (`httpMethod` PUT is lost). `password`, `authorization_credentials`, `hmacConfig`, `tlsConfig` are secret or unsupported, so they are manual. The URL itself may hold a token: write it as `secure_url`. CODE C1; DOCS D1, D5. |
| oncall | `WEBHOOK` | WEBHOOK TEMPLATE | Same transport as webhook (url, basic or bearer). OnCall expects Alertmanager-format JSON. CODE C1; the OnCall input format is UNVERIFIED. |
| prometheus-alertmanager | `WEBHOOK` to `<am>/api/v2/alerts` | WEBHOOK TEMPLATE (low value) | Feasible with a one-element array body; value is low because New Relic is the replacement for the Alertmanager. Body shape UNVERIFIED. |
| slack | `SLACK` (OAuth app) | MANUAL STEP | `url` and `token` are both secure, so the incoming-webhook URL cannot be read, and the New Relic Slack destination needs an OAuth sign-in per workspace and cannot be created through Terraform. Keep `recipient` (readable) as a label for the human. DOCS D1, D5; CODE C1. |
| pagerduty | `PAGERDUTY_SERVICE_INTEGRATION` (auth_token) | MANUAL STEP | `integrationKey` is secure. New Relic's service integration needs the key of a "New Relic" integration created in the PagerDuty service; whether a Grafana Events API v2 key is accepted there is not stated. The channel `summary` and `customDetails` can be generated. DOCS D1 (setup steps), D5; CODE C1; interchangeability of keys UNVERIFIED. |
| teams | `MICROSOFT_TEAMS` (needs the New Relic Teams app and a security code), or `WEBHOOK` with an Adaptive Card body | MANUAL STEP | Grafana's notifier posts an Adaptive Card message (`attachments` with `type` and a card), so the `url` is a Teams Workflows webhook URL that holds a secret even though C1 does not flag it S. Native path needs a human. CODE C1/teams; DOCS D5 (`securityCode`). |
| jira | `JIRA` destination (`url`, `auth_basic`); channel `JIRA_CLASSIC` | MANUAL STEP | `user`, `password`, `api_token` are secure. Channel needs the numeric project id and issue type id, whereas Grafana stores project key and issue type name. DOCS D5; CODE C1. |
| opsgenie | `WEBHOOK` with the Atlassian-supplied New Relic template | WEBHOOK TEMPLATE | No native type; D1 points to an Atlassian-hosted template (body not read). `apiKey` is secure and becomes an `auth_custom_header` `Authorization: GenieKey <key>` on the destination (that is the header Grafana sends, CODE C1/opsgenie). Auto-close on resolve needs a different URL, so a single channel cannot do it. DOCS D1, D5. |
| victorops | `WEBHOOK` to the REST endpoint | WEBHOOK TEMPLATE | `url` is secure and contains the integration and routing key: `secure_url`. Body fields Grafana sends: `message_type`, `entity_id`, `entity_display_name`, `timestamp`, `state_message`, `monitoring_tool`, `alert_url`. CODE C1/victorops. D1 links a Splunk On-Call template. |
| discord | `WEBHOOK` | WEBHOOK TEMPLATE | URL is the secret (S): `secure_url`. Body: `{content, username, avatar_url, embeds[{title,description,url,color,footer,image}]}`. CODE C1/discord. |
| googlechat | `WEBHOOK` | WEBHOOK TEMPLATE | URL is secret (S). Body is a Chat card message (`cards[].header.title`, `sections[].widgets[]`). CODE C1/googlechat. |
| telegram | `WEBHOOK` to `https://api.telegram.org/bot<token>/sendMessage` | WEBHOOK TEMPLATE | The token sits in the URL path: `secure_url` with prefix `https://api.telegram.org/` and the rest secret. Grafana sends multipart form fields (`chat_id`, `text`, `parse_mode`). Whether the Bot API accepts a JSON body is UNVERIFIED (from memory it does). CODE C1/telegram. |
| webex | `WEBHOOK` to `https://webexapis.com/v1/messages` | WEBHOOK TEMPLATE | `bot_token` becomes `auth_token {prefix = "Bearer ", token}`. Body `{roomId, markdown}`. CODE C1/webex. |
| sensugo | `WEBHOOK` to `<url>/api/core/v2/namespaces/<ns>/events` | WEBHOOK TEMPLATE | `apikey` becomes header `Authorization: Key <apikey>` (`auth_custom_header`). CODE C1/sensugo. Event body not mapped here. |
| kafka | `WEBHOOK` to the REST proxy | WEBHOOK TEMPLATE | Grafana posts to `<proxy>/topics/<topic>` (API v2) or `<proxy>/v3/clusters/<id>/topics/<topic>/records` with body `{"records":[{"value":...}]}` and header `Content-Type: application/vnd.kafka.json.v2+json` (v2). Whether New Relic honours a Content-Type set in channel `headers` is UNVERIFIED. CODE C1/kafka. |
| wecom | `WEBHOOK` | WEBHOOK TEMPLATE for `url` (robot) mode; NO EQUIVALENT for app mode | App mode fetches an access token from `corp_id` and `secret` first (two requests). CODE C1/wecom. |
| dingding | `WEBHOOK` | WEBHOOK TEMPLATE | URL (with `access_token`) is secret: `secure_url`. Body `msgtype` `link` or `actionCard`. CODE C1/dingding. |
| pushover | none native | MANUAL STEP | `apiToken` and `userKey` are both secrets and travel in the request body (multipart form), so a payload template would hold secrets. CODE C1/pushover. |
| sns | `EVENT_BRIDGE` destination, then an AWS-side EventBridge rule to the SNS topic | MANUAL STEP | `EVENT_BRIDGE` takes `AWSAccountId`, `AWSRegion` and a channel `eventSource`; New Relic has no SNS type. The AWS side is a human step. `sigv4` keys are secrets. DOCS D5; CODE C1/sns; the SNS route is my inference. |
| threema | none | NO EQUIVALENT | Form-encoded POST with `api_secret` in the body. CODE C1/threema. |
| LINE | none | NO EQUIVALENT | Form-encoded POST to LINE Notify with a Bearer token. CODE C1/line. That LINE Notify has been shut down is from memory: UNVERIFIED. |
| mqtt | none | NO EQUIVALENT | MQTT is not HTTP. CODE C1/mqtt. |
| wechat | none | NO EQUIVALENT | Imported Alertmanager only. CODE C1. |

Sensitivity of the template text: Grafana `title`, `message`, `text`, `subject` fields are Go
templates. They are read as data and must never be copied into a New Relic template or a file as
text; see section 4. Names (`name`, `recipient`, channel labels) are untrusted and may only appear
through `quote_string`/`identifier`, `hcl_string`, GraphQL variables or `md_cell`
(REPO CLAUDE.md quality bar).

## 2. Notification policies

### 2.1 The tree

Node fields (CODE C3 `Route`): `receiver`, `group_by`, `matchers` (strings such as
`severity="critical"`), `object_matchers` (triples `[label, op, value]`), deprecated `match`
and `match_re`, `mute_time_intervals`, `active_time_intervals`, `continue`, `routes[]`,
`group_wait`, `group_interval`, `repeat_interval`, `provenance`. Grafana turns `object_matchers`
into ordinary matchers: all are ANDed. CODE C3 `AsAMRoute`.

Inheritance (CODE C4 `newRoute`): a child starts from its parent's options, then overrides
`receiver`, `group_by` (or `...`), `group_wait`, `group_interval`, `repeat_interval` when set.
`mute_time_intervals` and `active_time_intervals` are NOT inherited: they are replaced by the
child's own, empty if unset. Root defaults: wait 30 s, interval 5 min, repeat 4 h. CODE C4.
`group_by: ['...']` sets "group by all labels" and cannot be combined with other labels. CODE
Alertmanager `config/config.go` (error text "cannot have wildcard group_by (`...`) and other
labels at the same time").

Matching (CODE C4 `Route.Match`): depth first, left to right. A node matches if all its matchers
match. For each child, collect its matches; stop after the first child that produced matches unless
that child has `continue: true`. If no child matched, the node itself is the match. The root has
no matchers, so everything reaches at least the root receiver.

Matcher semantics (CODE C5):

- `=` and `!=` compare exact strings. `=~` and `!~` compile `^(?:<value>)$`: anchored at both ends,
  RE2.
- A missing label is the empty string. So `team!="x"` and `team!~"x.*"` match an alert that has no
  `team`; `team=""` and `team=~".*"` also match it; `team=~".+"` does not.
  CODE C4 `Matchers.Matches` reads `lset[name]` which yields "" for an absent key.

Rules can skip the tree: `notification_settings` of type simplified routing names a contact point
(and optional group_by and timings) directly, and Grafana 13 can select a named policy tree.
PRIOR. The `GrafanaRule` model already carries `notification_settings` (REPO
`src/importer/sources/grafana/models.py`).

### 2.2 Mapping to New Relic

| Grafana | New Relic | Fidelity |
|---|---|---|
| `receiver` | workflow `destination` channels (one channel per integration) | exact, subject to section 1 |
| `matchers` on `alertname` | workflow predicate `accumulations.conditionName` EXACTLY_MATCHES (already what the CloudWatch and Dynatrace plans use, REPO `core/notification_ir.py`) | exact once the rule is converted |
| `matchers` on a label that is a query `by (...)` label | predicate `accumulations.tag.<label>` (FACET values are copied to issue tags) | good; DOCS D3 ("Query using triggering facet value"), D4 (`tags.` for facets) |
| `matchers` on a static rule label (`severity`, `team`) | decided at conversion time by evaluating the matcher against the rule's labels; nothing is sent to New Relic | exact; the labels are not carried to New Relic today (REPO: the translator drops them) |
| `matchers` on `grafana_folder` | same, evaluated statically against the folder name | exact |
| operators `=` / `!=` | `EQUAL`/`DOES_NOT_EQUAL` on strings; `EXACTLY_MATCHES`/`DOES_NOT_EXACTLY_MATCH` on list attributes (all `accumulations.*` are lists) | see 2.3 |
| `=~` / `!~` | no regex operator exists in workflow filters. Operators are `CONTAINS`, `DOES_NOT_CONTAIN`, `EQUAL`, `DOES_NOT_EQUAL`, `STARTS_WITH`, `ENDS_WITH` (strings) and `EXACTLY_MATCHES`, `DOES_NOT_EXACTLY_MATCH`, `CONTAINS`, `DOES_NOT_CONTAIN` (lists); `values` match any. DOCS D5 (workflow). | literal alternation `a\|b` becomes one predicate with two values; `x.*` becomes `STARTS_WITH`; `.*x` `ENDS_WITH`; anything else is manual |
| `continue` | several workflows may match one issue, each notifying its own channels | exact, the engine evaluates all workflows |
| first match wins (no `continue`) | no priority between workflows; each runs independently | must be encoded in predicates (2.3) or the match is reported as undecidable |
| `group_by` | policy `incident_preference` (REPO uses `PER_CONDITION` for all conditions, `core/apply.py`): `PER_POLICY`, `PER_CONDITION`, `PER_CONDITION_AND_TARGET`. DOCS D5 (alert policy, via PRIOR 3.2). | default `[grafana_folder, alertname]` is close to `PER_CONDITION`; `...` is close to `PER_CONDITION_AND_TARGET`; any other label set is lost |
| `group_wait`, `group_interval` | no setting found in the workflow docs read | lost; UNVERIFIED that none exists |
| `repeat_interval` | no timed re-notify found; notifications are sent on issue events (`ACTIVATED`, `ACKNOWLEDGED`, `INVESTIGATING`, `PRIORITY_CHANGED`, `CLOSED`, `OTHER_UPDATES`). DOCS D5 (workflow) | lost; Grafana would re-send every `repeat_interval` while firing; New Relic sends once per event |
| `mute_time_intervals` | `newrelic_alert_muting_rule` (section 3) plus `muting_rules_handling = DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES` on that workflow | partial: a muting rule is account wide, not per route |
| `active_time_intervals` | complement muting rule | only for simple weekly or daily patterns |
| `disableResolveMessage` | omit `CLOSED` from `notification_triggers` | exact |
| priority of firing | one workflow per priority using predicate `priority` EQUAL `HIGH` for warnings (DOCS D5/N12 via PRIOR 3.3) | only if the importer created warning thresholds |
| enrichments | up to 2 NRQL enrichments per workflow; results usable in the template. DOCS D5, D3 | optional: could re-add `$values` style data as a query; not automatic |

### 2.3 Algorithm: tree to (filter, destinations) workflows

Input: the policy tree, the list of converted rules (each with title, static labels, folder and the
set `B` of `by (...)` labels from its query, or `unknown` when the query has no aggregation), and
the receiver table. Output: a list of `PlannedWorkflow` with `condition_names`,
`destination_indexes` and optional `predicates`, plus a list of rules that could not be decided.

1. Build the static label set for each converted rule: its `labels`, plus `alertname` = title and
   `grafana_folder` = folder title. (`alertname` and `grafana_folder` are added to every
   Grafana-managed alert instance; PRIOR 5.2 and the default `group_by`.) Add the Grafana label
   `__grafana_autogenerated__`-style internals only if the policy refers to them (they appear
   only in generated routes). PRIOR.
2. Classify each label name used by a matcher, per rule:
   - STATIC: name is in the static set. Value is known.
   - DYNAMIC: name is in `B`. Value varies per series; maps to `accumulations.tag.<name>`.
   - ABSENT: name is in neither, and `B` is known (an aggregation guarantees the only series labels
     are `B`). Value is `""`.
   - UNKNOWN: name is in neither and `B` is unknown (bare selector: every label of the metric may
     appear). Cannot be decided.
3. Evaluate a matcher with three outcomes: TRUE, FALSE, or RESIDUAL(predicate) for DYNAMIC. For
   STATIC and ABSENT apply C5 exactly (anchor regexes as `^(?:v)$`, compare with `""` for ABSENT).
   For DYNAMIC build the predicate with the operator mapping in 2.2; if the regex cannot be
   expressed, outcome UNDECIDABLE. UNKNOWN is UNDECIDABLE.
4. Walk the tree with C4 semantics, carrying a list of residual predicates `R` (a node's `R` is its
   parent's plus its own):
   - a node whose matchers include a FALSE is skipped;
   - a node with UNDECIDABLE is skipped and the rule is recorded as undecidable for that node;
   - otherwise descend into the children in order. Track `earlier_matched`: TRUE if an earlier
     sibling matched unconditionally (no residuals). If an earlier sibling matched unconditionally
     and had `continue: false`, stop. If an earlier sibling matched only conditionally (it has
     residuals), a later sibling must also hold "earlier sibling did not match": expressible only if
     the earlier residual is one predicate (negate its operator); otherwise record the pair as
     undecidable and do not emit a workflow for the later sibling.
   - a node becomes a leaf (emits) when no child produced an unconditional match. A node with only
     conditional child matches also emits itself with the negations above.
5. Each emitted leaf carries: effective receiver, residuals `R`, the node's own
   `mute_time_intervals` and `active_time_intervals` (not inherited, C4), the effective
   `group_by`, and the timings.
6. Group leaves by the key `(receiver, sorted(R), mute set, active set)`. One `PlannedWorkflow` per
   key; `condition_names` is the sorted set of rule titles (the converted condition names, as
   `converted_condition_names` already produces); `predicates` is empty when `R` is empty (the
   existing `issue_predicates()` then filters on the condition names) and otherwise
   `accumulations.conditionName` EXACTLY_MATCHES plus one predicate per residual, ANDed.
7. A rule that reaches no leaf except the root default gets the root receiver. A rule that has
   `notification_settings` (simplified routing) skips the tree and goes straight to its named
   receiver with its own group_by and timings.
8. Size guard: a filter is limited to 4,096 characters and 1,000 workflows per account by default.
   DOCS D5 (PRIOR for the numbers). If the condition-name list would exceed the limit, split the
   workflow, or filter on `labels.policyIds` after putting the conditions in a dedicated policy.
   The importer uses one policy today (REPO `core/emit/terraform.py`), so splitting by name is the
   no-new-feature option.
9. Report, never guess: every UNDECIDABLE node, every regex that could not be expressed, every
   lost timing, and every rule with no receiver go to the report with a fixed reason string.

Semantic differences to document, not hide:

- A list predicate `DOES_NOT_EXACTLY_MATCH` on `accumulations.tag.X` is true for the whole issue,
  which may aggregate several incidents with different X; Alertmanager decides per alert instance.
  An issue with both X=a and X=b is "contains a" and "contains b". DOCS D5 (list operators).
- A missing tag: Alertmanager treats it as `""`. How `DOES_NOT_EXACTLY_MATCH` or `EXACTLY_MATCHES`
  treat a missing tag is not documented; `IS`/`IS_NOT` with `NULL` exist but the docs say to avoid
  them. DOCS D5. So a matcher that is true because the label is missing (`team!="x"`) is safe only
  when resolved statically.
- Two workflows matching one issue can notify the same channel twice only if the channel is reused;
  a channel can be used in only one workflow, so each workflow needs its own channels and
  destinations are reused. DOCS D5 (workflow, "each channel can only be used in a single workflow").
  The existing planner already makes one channel per destination per plan (REPO).

## 3. Mute timings, active timings, silences

### 3.1 Source model

Time interval: `{name, time_intervals:[{times:[{start_time,end_time}], weekdays, days_of_month,
months, years, location}], version, provenance}`. A mute timing may contain several intervals; the
timing is active when ANY interval matches; within one interval ALL listed fields must match
(empty field matches anything). CODE C6 `ContainsTime`.

- `times`: `HH:MM` pairs, start inclusive, end exclusive; `24:00` allowed as end; start must be
  less than end, so a window across midnight is written as two ranges. CODE C6 (error text "start
  time cannot be equal or greater than end time"; `validTime` allows `24:00`).
- `weekdays`: names or numbers, ranges with `:`, 0 is Sunday; a range may wrap (`friday:monday`).
  CODE C6.
- `days_of_month`: `1`..`31` or `-1`..`-31` (from the end of the month), ranges with `:`; an end
  day must be negative if the start is. Days that do not exist in a short month are clamped or
  skipped. CODE C6.
- `months`: `1`..`12` or names; `years`: positive ranges. CODE C6.
- `location`: an IANA name. Without it, the time is evaluated in UTC. CODE C6 (`now.UTC()` then
  `.In(location)` only when set).
- A mute timing suppresses notifications while active; an active time interval allows notifications
  only while active; both are attached to a route and are not inherited. CODE C4; PRIOR 5.3 (mute
  wins on overlap).

### 3.2 New Relic muting rule

`newrelic_alert_muting_rule` / NerdGraph `alertsMutingRuleCreate` (DOCS D4, D5):

- `condition { operator = AND|OR, conditions { attribute, operator, values } }`. Attributes include
  `conditionName`, `conditionId`, `policyId`, `policyName`, `product`, `targetName`, `targetId`,
  `tags.<NAME>`. Operators: `EQUALS`, `NOT_EQUALS`, `IN`, `NOT_IN`, `CONTAINS`, `NOT_CONTAINS`,
  `STARTS_WITH`, `NOT_STARTS_WITH`, `ENDS_WITH`, `NOT_ENDS_WITH`, `IS_BLANK`, `IS_NOT_BLANK`,
  `ANY`. At most 20 sub-conditions and 500 values per operator. DOCS D4, D5.
- `schedule { start_time, end_time, time_zone (the argument reference says `timeZone`, the example
  says `time_zone`: check against the provider version), repeat = DAILY|WEEKLY|MONTHLY,
  weekly_repeat_days, end_repeat | repeat_count }`. Times are local ISO 8601 without an offset;
  `repeat_count` includes the first occurrence; use only one of `end_repeat` and `repeat_count`.
  DOCS D4, D5.
- `action_on_muting_rule_window_ended = CLOSE_ISSUES_ON_INACTIVE | DO_NOTHING`. Use `DO_NOTHING`:
  Grafana does not close alerts at the end of a mute timing. DOCS D4.
- Muting is by alert event attribute, account wide. A workflow honours it only through its
  `muting_rules_handling` (`DONT_NOTIFY_FULLY_MUTED_ISSUES`,
  `DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES`, `NOTIFY_ALL_ISSUES`). DOCS D5. The importer sets
  `NOTIFY_ALL_ISSUES` everywhere today (REPO `emit/notifications.py`); the workflows a mute timing
  covers need the stricter value.

### 3.3 Mapping

| Mute timing part | Mapping | Limits |
|---|---|---|
| one `times` range + `weekdays` list/range | one muting rule: `schedule.start_time` = first matching date at start, `end_time` = same day at end, `repeat = WEEKLY`, `weekly_repeat_days` = expanded weekday list (wrap-around expanded) | exact |
| `weekdays` only | `start_time` 00:00, `end_time` 24:00 (next day 00:00), `repeat = WEEKLY` | exact |
| `times` only | `repeat = DAILY` | exact |
| several `times` ranges | one muting rule per range, or manual | rule count |
| `location` | `time_zone` | exact |
| no `location` | `time_zone = "UTC"` | exact |
| `days_of_month` | MONTHLY recurrence repeats on the start date's day of month (UNVERIFIED how the day is chosen); lists, ranges and negative days (`-1` last day) are not expressible | MANUAL, or one rule per listed day for plain positive days |
| `months`, `years` | no field; bound with `start_time`, `end_repeat` or `repeat_count` | one rule per contiguous span, otherwise manual |
| window across midnight written as two ranges (`22:00-24:00` and `00:00-06:00`) | merge into one window ending next day, or two rules | either is exact |
| DST | schedule is local time plus zone; behaviour at transitions not documented | UNVERIFIED |

Scoping: the muting rule condition is `conditionName IN [the converted condition names routed to
the route that carries the timing]`. If a condition is also routed elsewhere, the mute also
silences those other workflows when they use the strict handling. Report that overlap. A timing
attached to the root cannot exist (not allowed on the root, PRIOR 5.2).

Active time intervals (notify only inside): emit the complement. For a weekly pattern with one
window the complement is two windows per week plus the full off-days; compute it only for plain
`times` and `weekdays` and send everything else to manual.

### 3.4 Silences

Alertmanager v2 silences: `matchers`, `startsAt`, `endsAt`, `createdBy`, `comment`, plus
`rule_uid` in Grafana. PRIOR 5.5. They are runtime state. Default: do not migrate. An optional
switch could create a one-off muting rule (no `repeat`) for silences whose `endsAt` is in the
future, with `tags.<label>` conditions; regex matchers follow the same restricted mapping as
policies. Expired silences are never migrated. This is a design suggestion, not verified behaviour.

## 4. Templates

Go templates in Grafana appear in three places: contact point fields (`title`, `message`, `text`,
`subject`, ...), named template groups (`{{ define "x" }}`), and rule annotations and labels
(`$labels`, `$values`, `$value`). Data model (CODE C9, PRIOR 5.4): top level `Receiver`, `Status`,
`Alerts` (`.Firing`, `.Resolved`), `GroupLabels`, `CommonLabels`, `CommonAnnotations`,
`ExternalURL`, `GroupKey`, `TruncatedAlerts`; per alert `Status`, `Labels`, `Annotations`,
`StartsAt`, `EndsAt`, `GeneratorURL`, `Fingerprint`, and for Grafana alerts `SilenceURL`,
`DashboardURL`, `PanelURL`, `Values` (map refId to float), `ValueString`, `OrgID`
(CODE C9 lines for `DashboardURL`, `PanelURL`, `SilenceURL`, `Values`, `ValueString`, `OrgID`).

New Relic side: Handlebars, with helpers `json`, `escape`, `eq`, `contains`, `math`, `timezone`,
`replace` plus the Handlebars built-ins (`#if`, `#unless`, `#each`, `@last`). DOCS D2. A variable
that is missing and has nested fields makes rendering fail so the template cannot be saved; guard
with `{{#if x}}`. DOCS D2. A webhook body must stay valid JSON after rendering. DOCS D1.

Variables that exist (DOCS D3, the full table): `accumulations.conditionDescription`,
`accumulations.conditionFamilyId`, `accumulations.conditionName`, `accumulations.conditionProduct`,
`accumulations.deepLinkUrl`, `accumulations.nrqlQuery`, `accumulations.origins`,
`accumulations.policyName`, `accumulations.runbookUrl`, `accumulations.sources`,
`accumulations.tag.<name>` (created from facets and tags), `acknowledgedBy`, `activatedAt`,
`annotations.description`, `annotations.title`, `annotations.wildcard`, `closedAt`, `closedBy`,
`closedIncidentsCount`, `createdAt`, `entitiesData.entities|ids|kinds|names|types`,
`impactedEntitiesCount`, `incidentIds`, `isAcknowledged`, `isCorrelated`, `issueAckUrl`,
`issueCloseUrl`, `issuePageUrl`, `issueActivatedAtUtc`, `issueClosedAtUtc`, `issueCreatedAtUtc`,
`issueDurationMs`, `issueDurationText`, `issueId`, `issueTitle`, `issueUpdatedAt`,
`labels.accountIds`, `labels.policyIds`, `mutingState`, `nrAccountId`, `openIncidentsCount`,
`owner`, `policyUrl`, `priority` (`CRITICAL|HIGH|MEDIUM|LOW`), `priorityText`, `state`
(`CREATED|ACTIVATED|CLOSED`), `stateText` (`active|closed`), `status`, `totalIncidents`,
`triggeredAt`, `triggerEvent`, `updatedAt`, `violationChartUrl`, `workflowName`. Enrichment results
appear at the top of the variable list and are addressed as `[latest <name>]`
(`{{ escape [latest my_enrichment] }}`). DOCS D2, D3.

Variable map (Grafana to New Relic):

| Grafana | New Relic | Note |
|---|---|---|
| `.Status` (`firing`/`resolved`) | `stateText` (`active`/`closed`) or `state` | `{{#eq state "CLOSED"}}resolved{{else}}firing{{/eq}}`; DOCS D2, D3 |
| `.Alerts` (list, one per series) | none as a list; an issue groups incidents. `totalIncidents`, `entitiesData.entities`, `accumulations.tag.<facet>` are lists | one issue, not one message per series |
| `len .Alerts.Firing` / `.Resolved` | `openIncidentsCount` / `closedIncidentsCount` | DOCS D3 |
| `.Labels.alertname` | `accumulations.conditionName` (list) | `{{#each accumulations.conditionName}}...{{/each}}` |
| `.Labels.<query label>` | `accumulations.tag.<label>` (list) | only labels in the query's FACET |
| `.Labels.<static rule label>` | no variable; write the value into the template as a literal at conversion time | a literal from untrusted text must go through the JSON-string escaper |
| `.Annotations.summary` / `.description` | `annotations.title` / `annotations.description`, or `accumulations.conditionDescription` | which one holds the condition text depends on how the condition description is written (UNVERIFIED) |
| `.StartsAt` / `.EndsAt` | `issueActivatedAtUtc` / `issueClosedAtUtc` (or `activatedAt` / `closedAt` with `timezone`) | DOCS D2, D3 |
| `.GeneratorURL`, `.PanelURL`, `.DashboardURL` | `issuePageUrl`, `violationChartUrl`, `policyUrl`, `accumulations.deepLinkUrl` | not the same target: the links lead into New Relic |
| `.SilenceURL` | none | silence = muting rule in New Relic |
| `.Fingerprint`, `.GroupKey` | `issueId`, `incidentIds` | |
| `.Receiver` | `workflowName` | approximate |
| `.ExternalURL`, `.OrgID` | `issuePageUrl` base, `nrAccountId` | |
| `.CommonLabels`, `.GroupLabels`, `.CommonAnnotations` | none | |
| `.ValueString`, `.Values.B`, `$values.B.Value`, `$value` | none documented. Closest: an enrichment NRQL query that re-reads the metric, using `{{accumulations.tag.<facet>}}` in its WHERE (DOCS D3 enrichment examples) | lost by default |
| `$labels.x` in annotations | `accumulations.tag.x` | same rule as `.Labels` |
| `{{ range .Alerts }}` | `{{#each accumulations.conditionName}}` or `{{#each entitiesData.names}}` | |
| `{{ if eq .Status "firing" }}` | `{{#eq state "ACTIVATED"}}` | |
| `{{ template "x" . }}`, `define`/`end` | expand inline at conversion time; New Relic has no template groups | |
| `humanize`, `toUpper`, `reReplaceAll`, `date`, `tz` | `replace`, `timezone`; the rest has no helper | |
| `{{ .Alerts.Firing }}` loops with `SortedPairs` | no | |

Policy for conversion: do not translate arbitrary Go templates. Recognise only the Grafana default
templates (`default.title`, `default.message`, and the per-type defaults) and map them to a fixed,
reviewed Handlebars body per destination type; any contact point with a customised template gets
the default New Relic body and a report line "custom template not converted" with the raw text
shown only through `md_cell`. Reason: the template text is untrusted input and Go and Handlebars
differ in escaping. REPO CLAUDE.md rule.

## 5. Imported Alertmanager configs, ruler rules, Prometheus rule files

### 5.1 Where this data lives and how to read it

Grafana-managed (extra config, Grafana 13): a Prometheus or Mimir Alertmanager config can be
imported and run as is, with `v0mimir` integrations that the provisioning API does not return.
`GET /api/alertmanager/grafana/config/api/v1/alerts` returns `template_files`,
`alertmanager_config` (route, receivers, time intervals, inhibit rules) and `extra_config`; the
route is marked internal and deprecated. PRIOR 4.1, 5.7. For an external Alertmanager replace
`grafana` with the data source uid. PRIOR.

Mimir (DOCS D14, `<prometheus-http-prefix>` default `/prometheus`; multi-tenant calls need
`X-Scope-OrgID`):

- Rules, YAML dictionary namespace to groups: `GET <prefix>/config/v1/rules`, per namespace
  `.../rules/{namespace}`, per group `.../rules/{namespace}/{groupName}`. Group fields `name`,
  `interval`, `source_tenants`, `rules[]` with `record` or `alert`, `expr`, `for`, `labels`,
  `annotations`. Can be turned off with `-ruler.enable-api`.
- Evaluation state: `GET <prefix>/api/v1/rules`, `GET <prefix>/api/v1/alerts`.
- Alertmanager config for a tenant: `GET /api/v1/alerts` (multi-tenant Alertmanager API, needs
  `-alertmanager.enable-api`); returns `template_files` and `alertmanager_config`. Tooling:
  `mimirtool rules list`, `mimirtool alertmanager get`.

Loki ruler (DOCS D15): `GET /loki/api/v1/rules`, `/loki/api/v1/rules/{namespace}`,
`/loki/api/v1/rules/{namespace}/{groupName}` (also under `/api/prom/rules`), and
`GET /prometheus/api/v1/rules` for state. Same group schema, LogQL in `expr`.

Through Grafana (PRIOR 3): `GET /api/ruler/{DatasourceUID}/api/v1/rules` for the groups of a
data source and `GET /api/prometheus/{DatasourceUID}/api/v1/rules` for state; needs
`alert.rules.external:read`. The loader in this repository already reads this "ruler" shape from
files, and marks such rules manual (REPO `docs/what-migrates.md`, "rules run by the Mimir or Loki
ruler").

Prometheus rule file (DOCS D16): `groups:` list; group `name`, optional `interval`, optional
`labels` (group labels added to rules), `rules:`. Alert rule: `alert`, `expr`, `for`,
`keep_firing_for`, `labels`, `annotations` (templated with `$labels`, `$value`, `$externalLabels`).
Record rule: `record`, `expr`, `labels`. `expr` holds the whole condition including the comparison
(`... > 0.5`), unlike a Grafana rule where the threshold is a separate node.

Alertmanager configuration file (DOCS D17): `route` with `receiver`, `matchers` (strings),
`group_by`, `continue`, `routes`, `mute_time_intervals`, `active_time_intervals`, timings;
`receivers[]` each with lists such as `discord_configs`, `email_configs`, `jira_configs`,
`msteams_configs`, `msteamsv2_configs`, `opsgenie_configs`, `pagerduty_configs`,
`pushover_configs`, `slack_configs`, `sns_configs`, `telegram_configs`, `victorops_configs`,
`webex_configs`, `webhook_configs`, `wechat_configs`, `mattermost_configs`, `rocketchat_configs`,
`incidentio_configs`; `time_intervals`; `inhibit_rules`; `templates`. Unlike Grafana's API, this
file holds secrets in clear (`api_url`, `routing_key`, `auth_token`), so a loader for it must
never copy them to its outputs or logs; it should write a placeholder and a variable name.

### 5.2 Recommendation on a separate `prometheus-rules` source

Recommendation: yes, but in two steps, and not first.

Step 1 (small, do first): make the existing Grafana loader convert data source-managed ("ruler")
rules that use the PromQL subset it already handles, instead of marking all of them manual. These
rules arrive as `groups[].rules[]` with `expr`, so a top-level comparison splitter is needed (below).
Effort S to M. It reuses the translator, the IR and the golden tests. Coverage upside: every
customer whose rules live in Mimir or Loki rather than in Grafana. How many rules that is for the
owner: UNVERIFIED (needs the live read in section 8).

Step 2 (separate source `prometheus-rules`): a loader for `rules.yml` files, `mimirtool` output
and the Mimir/Loki ruler responses, plus an `alertmanager.yml` reader that feeds the notification
plan. Effort: loader S; `expr` splitter and `for`/`keep_firing_for`/labels/annotations mapping M;
Alertmanager receivers to the section 1 table M; policy tree reuse of section 2 is free if the
tree is normalised into the same internal shape. Reasons it is not first:

- The translator today converts only a selector with one optional aggregation and `by`; it sends
  `rate`, `increase`, `histogram_quantile`, binary operators and comparisons to manual
  (REPO `docs/what-migrates.md`). Hand-written Prometheus rules (kube-prometheus-stack, mixin rule
  sets) are dominated by exactly those. My guess is that the upside is small until `rate`/
  `increase` over a counter and a top-level comparison are supported; that guess is UNVERIFIED and
  should be replaced by a count over a real rule file.
- New Relic's own translator documents `rate(sum(c), 1 second)` and notes that counters are stored
  as deltas (DOCS D8), so a `sum(c)` over the window divided by its length is the safe form for
  `increase`/`rate` in a condition. Whether `rate()` itself is accepted in an alert condition is
  UNVERIFIED (PRIOR 4.2).
- Record rules cannot become conditions; they are metrics. Either the recorded series is
  re-created in New Relic with a different mechanism, or the alerts that read it become manual.
  This is the largest hidden dependency in real rule files: alerts on `job:request_latency:mean5m`
  need the recording rule's expression inlined (possible only when it is itself in the supported
  subset).

Minimum viable translation rules for `expr`:

1. Parse `expr` as `<vector expression> <cmp> <number>` at top level (`>`, `>=`, `<`, `<=`, `==`,
   `!=`). Left side goes to the existing selector/aggregation translator, right side is the
   threshold. Anything else: manual.
2. `for` becomes the threshold duration (existing rounding rules apply); `keep_firing_for` is
   ignored and noted (existing behaviour for Grafana rules).
3. Group `interval` is the evaluation interval used when the range is missing.
4. Labels map as in section 2; annotations as in section 4.

## 6. Data path: getting Prometheus, Loki and Tempo data into New Relic

Converted NRQL only works if the data exists under the names the NRQL uses. REPO: the converter
already says "the metric must already reach New Relic (Prometheus remote write or OTLP) and its
New Relic name and label attributes are not confirmed" (`docs/what-migrates.md`).

### 6.1 Prometheus remote write (DOCS D6)

- Endpoint (US): `https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=<NAME>`.
  EU: `https://metric-api.eu.newrelic.com/prometheus/v1/write`. JP:
  `https://metric-api.jp.nr-data.net/prometheus/v1/write`.
- Auth: the ingest license key (not a user API key), as `authorization: credentials: <key>`
  (Prometheus 2.26+), `bearer_token` (older), or `?X-License-Key=<key>` in the URL (not
  recommended). The key selects the account. DOCS D6.
- `prometheus_server=<NAME>` becomes an attribute on every data point; reuse the same name as the
  `X-Prometheus-Server` header when reading back. DOCS D6, D10.
- Data is visible after about five minutes. DOCS D6.
- The protocol carries no type, so New Relic infers it from the name: `*_bucket`, `*_count`,
  `*_total` are stored as `count`; `*_sum` as `summary`; everything else as `gauge`. Add a label
  `newrelic_metric_type` (`counter`, `gauge`, `summary`, lower case) in `write_relabel_configs` to
  override; it is stripped on receipt. DOCS D6.
- Counters are stored as deltas, not cumulative: "the Prometheus counter is a cumulative sum while
  the New Relic count is a delta sum" (2, then 5 in Prometheus; 2, then 3 in New Relic). DOCS D8.
  "NRDB converts PromQL-style accumulating counters to delta counters." DOCS D9. Where in the
  pipeline the conversion happens is not stated: UNVERIFIED.
- Reading a `count`: take `rate(sum(m), 1 second)` or `sum(m)` over the window; for gauges
  `latest`, `average`, `max`. DOCS D8, D11.
- Histograms: Prometheus classic histograms become several counters (`_bucket` with the `le`
  attribute, `_sum`, `_count`); summaries become `_sum` summary, `_count` count and one gauge per
  quantile. DOCS D8. `histogram_quantile` works in New Relic's PromQL-style mode (DOCS D9), but
  alert conditions are NRQL only (PRIOR 4.3), so classic-histogram latency alerts need a manual
  design (UNVERIFIED how to compute a quantile from `_bucket` counts in NRQL).
- Label to attribute: each label is an attribute with the same name; `__name__` is the metric
  name; `job` and `instance` stay as ordinary attributes named `job` and `instance`. DOCS D8
  ("filter by `job`, `instance`" examples and `WHERE id = '/'`). Names are used as sent (DOCS D8
  `container_memory_usage_bytes`).
- Name rules: Prometheus metric names may contain colons (recording rules, `job:x:rate5m`) and,
  with UTF-8 enabled in Prometheus 3, dots. The Metric API accepts names under 255 characters
  (DOCS metric API page) and PRIOR 4.1 says attribute names are limited to alphanumerics, `:`,
  `.`, `_`. Whether a colon in a metric name is stored unchanged or rewritten is UNVERIFIED. In
  NRQL a name with a colon or dot must be backquoted; the repo's `identifier()` already quotes
  (REPO `core/emit/nrql.py`).
- Limits that bite alerts (PRIOR 4.1): 150 attributes per metric, attribute value 4,096
  characters, data older than 48 h or newer than 24 h dropped, cardinality caps per account per
  day. Past the cap raw data still arrives but rollups stop, so query windows over 60 minutes can
  be wrong. PRIOR; not re-read.
- Dedup of HA Prometheus pairs: three values per replica; the full list was not captured.
  PRIOR 4.1.

### 6.2 OTLP (DOCS D7)

- Endpoints: US `https://otlp.nr-data.net`, EU `https://otlp.eu01.nr-data.net`, JP
  `https://otlp.jp.nr-data.net`, FedRAMP `https://gov-otlp.nr-data.net`; ports 443, 4317 (gRPC),
  4318 (HTTP). Header `api-key: <license key>`. gzip or zstd compression.
- Metrics: New Relic prefers delta temporality for Counter, Histogram, async Counter and
  cumulative for UpDownCounter and Gauge; exponential histograms recommended. DOCS D7.
- Attributes are the OTLP attributes (resource and scope attributes included); maps of maps and
  heterogeneous arrays are not supported. DOCS D7.
- OTLP names are the OpenTelemetry names (dots kept). If the data came from Prometheus through an
  OpenTelemetry collector, the Prometheus-to-OTLP translator may have escaped dots to underscores
  and appended `_total` and unit suffixes, depending on `translation_strategy`
  (`UnderscoreEscapingWithSuffixes` is the Prometheus default, `NoUTF8EscapingWithSuffixes`
  keeps dots). DOCS D18. The name the NRQL must use depends on which pipeline wrote the data.

### 6.3 Grafana Alloy dual write

Remote write to both Grafana and New Relic (`prometheus.remote_write` supports several `endpoint`
blocks, each with its own queue; per-endpoint `url`, `bearer_token`, `headers`, `write_relabel_config`).
DOCS D13. A configuration, written from the documented arguments (the License key goes in an
environment variable; never in a file):

```alloy
prometheus.remote_write "dual" {
  endpoint {
    name = "grafana"
    url  = sys.env("GRAFANA_PROM_URL")
    basic_auth {
      username = sys.env("GRAFANA_PROM_USER")
      password = sys.env("GRAFANA_PROM_TOKEN")
    }
  }
  endpoint {
    name         = "newrelic"
    url          = "https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=alloy-prod"
    bearer_token = sys.env("NEW_RELIC_LICENSE_KEY")
    write_relabel_config {
      source_labels = ["__name__"]
      regex         = "my_counter"
      target_label  = "newrelic_metric_type"
      replacement   = "counter"
      action        = "replace"
    }
  }
}
```

The `write_relabel_config` block takes the same fields as Prometheus (DOCS D13 shared block; the
field list was not opened, so the exact attribute names are UNVERIFIED). A New Relic specific
Alloy guide was not found (UNVERIFIED that none exists).

OTLP route (metrics, logs, traces) with a header set by `otelcol.auth.headers`:

```alloy
otelcol.auth.headers "newrelic" {
  header {
    key   = "api-key"
    value = sys.env("NEW_RELIC_LICENSE_KEY")
  }
}

otelcol.exporter.otlphttp "newrelic" {
  client {
    endpoint = "https://otlp.nr-data.net"
    auth     = otelcol.auth.headers.newrelic.handler
  }
}
```

The `auth = otelcol.auth.headers.newrelic.handler` link inside `client` follows the usual Alloy
pattern but the `client` argument table was not read: UNVERIFIED. `otelcol.exporter.otlphttp` appends `/v1/metrics`, `/v1/logs`, `/v1/traces` to `client.endpoint`
(DOCS D13). To send scraped Prometheus metrics this way use `otelcol.receiver.prometheus` in front
of the exporter (its documentation was fetched but its type and name translation was not read).

### 6.4 OpenTelemetry Collector and others

The OTLP exporter in a Collector needs the same endpoint and `api-key` header (DOCS D7). Other
documented routes for Prometheus data: the New Relic Prometheus Agent for Kubernetes and the
Prometheus OpenMetrics integration for Docker (DOCS D6 and "Send Prometheus metric data to New
Relic" page).

### 6.5 Prometheus-compatible query API (the reverse direction)

New Relic exposes a Prometheus API that emulates Prometheus' query APIs, so Grafana can use New
Relic as a Prometheus data source. URL `prometheus-api.newrelic.com` (US),
`prometheus-api.eu.newrelic.com` (EU), `prometheus-api.jp.newrelic.com` (JP); header
`X-Query-Key` (Insights query key; JP uses `api-key` with a user API key); optional
`X-Prometheus-Only` (default true) and `X-Prometheus-Server`. DOCS D10. PromQL is translated to the
closest NRQL; supported features, 366 range steps, 100 series by default, one-minute look-back on
instant queries: DOCS D9. This is useful during migration (existing dashboards read New Relic) but
whether Grafana alert rules work against it was not read: UNVERIFIED. It does not help alert
conditions, which are NRQL.

### 6.6 Loki and logs

- Send path: the New Relic Log API (US `https://log-api.newrelic.com/log/v1`, EU
  `https://log-api.eu.newrelic.com/log/v1`; header `Api-Key` or `License-Key`, JSON). DOCS D12.
  Or OTLP logs to the OTLP endpoint (DOCS D7).
- From Alloy: `loki.source.*` -> `otelcol.receiver.loki` -> `otelcol.exporter.otlphttp`. The
  documented example does exactly this with an OTLP-capable endpoint. DOCS D13
  (`otelcol.receiver.loki`).
- Label to attribute: Loki labels become log record attributes and then attributes on the `Log`
  event, but the exact names (for example whether `service_name` stays that way) are not documented
  in the pages read. UNVERIFIED. The converted NRQL (`FROM Log WHERE k = 'v' AND message LIKE
  '%text%'`, REPO) depends on it.
- The log line becomes `message` (REPO assumption, repeated in the converter's note). UNVERIFIED.

### 6.7 Tempo and traces

OTLP traces to the same OTLP endpoint (DOCS D7). Grafana alerts on Tempo (TraceQL metrics) are rare
and out of scope for the current translator (it handles only Prometheus and Loki, REPO). Span
attribute names and the `Span` event schema were not read. UNVERIFIED.

### 6.8 What a `check-grafana-data` tool would query

Follow the GCP pattern: read-only NRQL through the shared `rows()` helper and a `Check` per
converted condition (REPO `src/importer/core/data_check.py`,
`src/importer/sources/gcp/data_check.py`). All queries are built from names the converter
produced, quoted with `identifier()` and string-escaped. For each converted Prometheus condition:

1. Does the metric arrive:
   `SELECT count(*) FROM Metric WHERE metricName = '<name>' SINCE 15 minutes ago`
   (DOCS D8 uses `WHERE metricName = '...'`). Zero means the condition is blind, as with
   `sum()` over nothing returning 0.
2. Do the attributes the NRQL filters or facets on exist:
   `SELECT keyset() FROM Metric WHERE metricName = '<name>' SINCE 1 hour ago` (DOCS D8). Compare
   with the label names used in `WHERE`/`FACET`; report each missing one. This catches `job`,
   `instance`, renamed or escaped labels.
3. Which sender wrote it: `SELECT uniques(prometheus_server) FROM Metric WHERE metricName = '<name>'`
   (attribute from DOCS D6). Several servers mean double counting with `sum()`; report it.
4. Value sanity for the intended type: for a name ending `_total`, `_count`, `_sum`, `_bucket`,
   report the inferred type (count/summary) from DOCS D6 and check that the NRQL uses `sum`/`rate`;
   for any other name report "gauge unless relabelled". Reading the stored type from NRDB is not
   documented: UNVERIFIED.
5. Freshness: `SELECT max(timestamp) FROM Metric WHERE metricName = '<name>'` (use of the
   `timestamp` attribute on Metric is UNVERIFIED), to flag a source that stopped; compare with the condition's aggregation window.
6. Logs: `SELECT count(*) FROM Log WHERE <attr> = '<value>' SINCE 1 hour ago` for each label
   filter, and `SELECT keyset() FROM Log SINCE 1 hour ago` to show which attribute names the Loki
   labels became.
7. Notification side (separate, `check-grafana-notifications` if wanted): none; destinations are
   not data.

Output: the same three statuses as the existing tools (`data arrives`, `no data`, `not checked`)
and a note naming the missing attribute.

## 7. What the Grafana data source setup implies

- Grafana Cloud Metrics is Mimir (a hosted, multi-tenant Prometheus-compatible store). The ruler,
  the Alertmanager API and remote write/read all follow the Mimir HTTP API. DOCS D14 (endpoints:
  remote write `POST /api/v1/push`, OTLP `POST /otlp/v1/metrics`, remote read
  `POST <prefix>/api/v1/read`). That Grafana Cloud's hosted Prometheus is Mimir is from memory:
  UNVERIFIED in the pages read.
- New stacks do not get data source-managed rules or the Cloud Alertmanager pre-provisioned;
  existing ones keep them. PRIOR 3. A data source-managed rule shows up only in the ruler API, not
  the Grafana-managed provisioning list. So an extractor that reads only `/api/v1/provisioning`
  misses them (REPO: the loader accepts the ruler shape from files, the extractor does not fetch it).
- Data is in the customer's Grafana Cloud or self-hosted store, not in New Relic. Migrating the
  alert does not migrate the series. Nothing in New Relic reads from Grafana Cloud Metrics
  server-side (no such documented path was found, PRIOR 4.4). The only documented ways are
  dual-write at the source (section 6.3), or a scraper/collector that reads the same targets.
  History is not backfilled by dual-write; the New Relic condition starts with an empty window.
  Mimir has a remote read endpoint (DOCS D14), which a custom bridge could use to backfill:
  UNVERIFIED and not documented as a New Relic feature; the Metric API drops data older than 48 h
  (PRIOR 4.1), so backfill beyond that is not possible anyway.
- A self-hosted Prometheus behind Grafana: remote write from Prometheus itself (section 6.1), no
  Alloy needed.
- A rule that queries several data sources, or Grafana Cloud's own metrics (`grafanacloud-usage`,
  `grafanacloud-prom` and so on), has no New Relic source at all. These are manual with a reason.
- The data source type the extractor sees (`prometheus`, `loki`, `tempo`, cloud data sources)
  decides which data path applies; a rule's `datasourceUid` can be mapped to a type by one
  `GET /api/datasources` call (read-only, needs `datasources:read`). PRIOR for the permission
  detail.

## 8. Build list, secrets policy, live-read list

### 8.1 Prioritised build list

| # | Item | Effort | Risk | Depends on |
|---|---|---|---|---|
| 1 | Extractor: read contact points with names, types and non-secret settings; drop secret values at read time (anything `[REDACTED]`, anything in a field the schema marks S, every `headers` value, every URL query string); never send `decrypt`; call `integrationtypeschemas` once for the field flags; fetch named policy trees, time intervals and templates | S to M | Low if the redaction runs before anything is written | none |
| 2 | Notification IR for Grafana: reuse `IRTopic`/`NotificationPlan`; email and webhook as `PlannedDestination`; all other types as `ManualDestination` with steps (extend `core/notification_ir.py` only if a new kind is needed) | M | Low; golden files change and need human review | 1 |
| 3 | Policy tree flattening (section 2.3) with static label evaluation, the three-valued matcher and an "undecidable" report; plans one workflow per key, `muting_rules_handling` set per workflow | M | Medium: wrong routing sends alerts to the wrong people; mitigate with workflows created disabled (already the case) and a report listing each rule's matched routes | 1, 2, translator keeping rule labels and the `by` label set |
| 4 | Reviewed Handlebars bodies for webhook types: discord, googlechat, telegram, webex, victorops, dingding, sensugo, kafka, wecom (url mode), oncall; generated through the JSON-string escaper only; golden-tested | M | Medium: payload shapes were derived from Grafana's sending code, not from each vendor's current API; each needs a live test by the owner | 2 |
| 5 | Mute timing to muting rule emitter for the exact cases in 3.3 (times, weekdays, location); everything else reported | S to M | Low to medium: scope is account wide | 3 |
| 6 | `check-grafana-data` (6.8) | S | Low | translator metric names |
| 7 | Data-path guide for the user (Alloy dual write, remote write, OTLP; section 6.3) shipped as documentation, not code | S | Low | none |
| 8 | Active time interval complement muting rules | M | Medium | 5 |
| 9 | Flip ruler/Loki-ruler rules from manual to convert for the existing PromQL subset (5.2 step 1) | S to M | Low | translator |
| 10 | `prometheus-rules` source and `alertmanager.yml` loader (5.2 step 2) | L | Medium: secrets live in the input file | 3, 9, translator support for rate/increase |
| 11 | Optional silences to one-off muting rules | S | Low value | 5 |
| 12 | Template conversion beyond defaults | L | High; do not do | none |

### 8.2 Secrets policy for contact points

- `decrypt` is never sent; the extractor already says so (REPO `extract.py` docstring). Keep it.
  The read-only role `alert.provisioning:read` is enough for the non-secret read; the
  secrets-reader role is never requested. PRIOR 4.3.
- Treat as sensitive and store only a hash, the host, or nothing: every field flagged S, every
  `headers` value, every URL's path and query for webhook, oncall, kafka, mqtt, sensugo, teams and
  alertmanager types (URLs often carry tokens), `authorization_credentials`, and `message`/`title`
  template bodies that could contain pasted secrets.
- A secret never reaches an output file, a log, a plan, a Terraform file or an error message. A
  generated Terraform destination uses a variable or a placeholder (`var.<name>` with no default)
  for `auth_*` and `secure_url`; the human supplies the value. A `hcl_string` is still required
  for every name.
- Remains manual: every secret (tokens, API keys, passwords, HMAC secrets, TLS material, secret
  URLs), the Slack OAuth sign-in and channel pick, the Teams app install and security code, the
  PagerDuty integration key creation, Jira project and issue type ids, the SNS-to-EventBridge
  AWS side, and all of `pushover`, `threema`, `LINE`, `mqtt`, `wechat`.
- Alertmanager files (5.1) carry secrets in clear. The loader reads them in memory and drops them
  before building any object that can be serialised.

### 8.3 Facts that need a live read of the owner's Grafana stack

The stack has 12 provisioned data sources and no contact points (given in the brief). Read-only
calls would settle:

- `GET /api/datasources`: the types and uids of the 12 (which are Prometheus/Mimir, Loki, Tempo,
  cloud metrics, which are Grafana Cloud defaults).
- `GET /api/ruler/<uid>/api/v1/rules` for each Prometheus/Loki data source: whether any
  data source-managed rules exist (decides the value of 5.2 step 1).
- `GET /api/v1/provisioning/policies` and the `/apis` named routing trees: whether a non-default
  tree exists, and whether rules use simplified routing (`notification_settings`).
- `GET /api/v1/provisioning/mute-timings`, `/templates`, and the Alertmanager config
  `extra_config`: whether any imported Alertmanager config exists.
- The `integrationtypeschemas` response of this Grafana version (settles section 1.2 flags).
- Per rule: the real label sets and the `by` labels (feeds the static evaluation in 2.3), and the
  folder titles used as `grafana_folder`.
- The Prometheus label names and metric names in each data source
  (`/api/datasources/proxy/uid/<uid>/api/v1/label/__name__/values` and label endpoints) to compare
  with what New Relic receives; this is a live read too (inventory only, no series data).
- On the New Relic side (the sandbox): whether any Prometheus data exists in the test account and
  what `keyset()` shows for a real metric (attribute names, `prometheus_server`, colon handling).
  Not done here by instruction.

## 9. Not found, not read, and UNVERIFIED

Pages and files that did not load or were not opened:

- 404: grafana/grafana `alertmanager_export.go`, `provisioning_notification_policies.go` (names
  differ in the current tree); docs-website `incident-workflows.mdx`,
  `prometheus-high-availability-ha.mdx`, `view-query-data/*.mdx` paths I guessed; Mimir
  architecture/ruler page; New Relic "translate PromQL" under `query-your-data/nrql-.../best-practices`.
- Opened as rendered HTML only (text extracted): D8, D9, D10 on docs.newrelic.com.
- Not opened: Atlassian's Opsgenie template and Splunk On-Call's New Relic guide (linked from D1);
  the Alloy `write_relabel_config` shared block; `otelcol.receiver.prometheus` details; Grafana
  docs pages on contact point settings; the `http2.V1TLSSubformOptions()` helper.

UNVERIFIED list (consolidated):

1. Which Grafana release introduced each integration type.
2. That `wechat` has no v1 config (the directory has none; the provisioning enum is stale).
3. That a Grafana PagerDuty Events API v2 key is accepted by the New Relic PagerDuty service
   integration.
4. That the Telegram Bot API accepts a JSON body from a New Relic webhook; that Kafka REST proxy
   accepts the vendor content type set through channel `headers`; that New Relic honours a
   Content-Type override at all.
5. The actual API shapes of Pushover, Opsgenie close, VictorOps, Sensu, DingTalk, WeCom, Google
   Chat as they stand today (only Grafana's sending code was read).
6. That LINE Notify is discontinued.
7. Whether channel `payload` properties are stored encrypted.
8. How a workflow predicate treats a missing tag (`DOES_NOT_EXACTLY_MATCH` on a null attribute).
9. Whether New Relic has any repeat-notification, group wait or interval setting for workflows.
10. How muting-rule MONTHLY recurrence picks its day; DST behaviour of schedules; whether a muted
    issue notifies after the window ends if the alert still fires.
11. Where in the pipeline counters are converted to deltas; whether colons or dots in Prometheus
    metric names are stored unchanged; the label to attribute naming of Loki labels in `Log`
    events; the `Span` schema from Tempo.
12. Whether `rate()`, `latestrate()` and `RLIKE` anchoring work in alert condition queries.
13. How to compute a quantile from classic histogram `_bucket` counts in an alert condition.
14. Whether Grafana alert rules can query New Relic's Prometheus API.
15. That Grafana Cloud hosted metrics is Mimir with the endpoints in D14.
16. How the condition description or title becomes `annotations.title` vs `accumulations.
    conditionDescription` in the issue payload.
17. Everything marked PRIOR (sections 2.2 limits, 4.1 API list, 6.1 limits, RBAC roles): taken from
    the other session's file without re-reading the source.
18. The effect of the stack's unknown choices: named policy trees, simplified routing, extra
    config, number of ruler rules (section 8.3).
