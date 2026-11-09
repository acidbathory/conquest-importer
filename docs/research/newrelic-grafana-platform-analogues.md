# Grafana to New Relic: product and platform analogues

Scope: concept-by-concept mapping from Grafana (OSS, Enterprise, Cloud) to New Relic, with the
importer's status for each. Public documentation, public provider docs and this repository only.
No Grafana or New Relic instance was called, no `.env` was read, no cloud command was run.
Written 2026-11-09 on branch `research/newrelic-grafana-platform-analogues`.

## 0. Evidence keys and conventions

Every claim carries DOCS (page read, key below), CODE/REPO (file in this repository) or UNVERIFIED
(memory, inference, or a negative found only by absence in a search).

Grafana docs (fetched with `curl -sSL`, 2026-11-09):

- Base GD = `https://grafana.com/docs/grafana/latest/`. GA-eval = `alerting/fundamentals/alert-rule-evaluation/`; GA-nodata = `.../nodata-and-error-states/`; GA-queries = `alerting/fundamentals/alert-rules/queries-conditions/`; GA-types = `.../alert-rule-types/`; GA-rec = `.../recording-rules/`; GA-pol = `alerting/fundamentals/notifications/notification-policies/`; GA-mute = `alerting/configure-notifications/mute-timings/`; GA-sil = `.../create-silence/`; GA-http = `alerting/set-up/provision-alerting-resources/http-api-provisioning/`; GA-tf = `.../terraform-provisioning/`.
- GD-ent = `introduction/grafana-enterprise/`; GD-org = `administration/organization-management/`; GD-roles = `administration/roles-and-permissions/`; GD-rbac = `.../access-control/`; GD-sa = `administration/service-accounts/`; GD-team = `administration/team-management/`; GD-auth = `setup-grafana/configure-security/configure-authentication/` (+ `saml/`); GD-scim = `.../configure-scim-provisioning/`; GD-audit = `.../audit-grafana/`; GD-public = `dashboards/share-dashboards-panels/shared-dashboards/`; GD-reports = `dashboards/create-reports/`; GD-annot = `dashboards/build-dashboards/annotate-visualizations/`; GD-play = `dashboards/create-manage-playlists/`; GD-explore = `explore/`; GD-qhist = `explore/query-management/`; GD-ds = `datasources/`.
- Base GC = `https://grafana.com/docs/grafana-cloud/`. GC-irm = `alerting-and-irm/irm/` (+ `introduction/`); GC-irmset = `alerting-and-irm/oncall/set-up/`; GC-slo = `alerting-and-irm/slo/` (+ `introduction/`, `create/`, `set-up/terraform/`); GC-sm = `testing/synthetic-monitoring/` (+ `create-checks/checks/`, `private-probes/`); GC-k6 = `testing/k6/`; GC-faro = `monitor-applications/frontend-observability/`; GC-prof = `monitor-applications/profiles/`; GC-k8s = `monitor-infrastructure/kubernetes-monitoring/`; GC-app = `monitor-applications/application-observability/`; GC-adm = `cost-management-and-billing/reduce-costs/metrics-costs/control-metrics-usage-via-adaptive-metrics/`; GC-logs = `.../reduce-costs/logs-costs/`; GC-intro = `introduction/`; GC-lim = `cost-management-and-billing/understand-your-invoice/usage-limits/`.
- Other: K6NR = `https://grafana.com/docs/k6/latest/results-output/real-time/new-relic/`; PYRO = `https://grafana.com/docs/pyroscope/latest/`; BEYLA = `https://grafana.com/docs/beyla/latest/`; SLOTH = `https://github.com/slok/sloth` README; PYRRA = `https://github.com/pyrra-dev/pyrra` README.
- GTF-`x` = Grafana Terraform provider doc `docs/resources/x.md` (grafana/terraform-provider-grafana, main).

New Relic:

- NTF-`x` = New Relic Terraform provider doc `website/docs/r/x.html.markdown` (newrelic/terraform-provider-newrelic, main).
- ND-`x` = `newrelic/docs-website` raw file under `src/content/docs/`, short name: ND-closed = `alerts/alert-event-management/how-alert-condition-events-are-closed`; ND-issues = `.../Issues-and-alert-event-management-and-response`; ND-nrql = `alerts/create-alert/create-alert-condition/create-nrql-alert-conditions`; ND-compound = `.../compound-alerts/compound-alerts`; ND-pref = `alerts/organize-alerts/specify-when-alerts-create-events`; ND-dec = `alerts/organize-alerts/change-applied-intelligence-correlation-logic-decisions`; ND-resp = `alerts/alert-event-management/response-intelligence-ai`; ND-integ = `alerts/get-notified/notification-integrations`; ND-slm = `service-level-management/` (`intro-slm`, `create-slm`, `alerts-slm`, `consume-slm`); ND-slmgql = `apis/nerdgraph/examples/nerdgraph-slm`; ND-syn = `synthetics/synthetic-monitoring/using-monitors/intro-synthetic-monitoring`; ND-synlim = `.../getting-started/monitor-limits`; ND-synpl = `.../private-locations/private-locations-overview-monitor-internal-sites-add-new-locations`; ND-synapi = `apis/nerdgraph/examples/synthetics-api/*`; ND-replay = `browser/browser-monitoring/browser-pro-features/session-replay/get-started`; ND-k8s = `kubernetes-pixie/kubernetes-integration/get-started/introduction-kubernetes-integration`; ND-pixie = `kubernetes-pixie/auto-telemetry-pixie/get-started-auto-telemetry-pixie`; ND-k8sotel = `kubernetes-pixie/k8s-otel/intro`; ND-ebpf = `ebpf/ebpf-apm`; ND-jfr = `apm/agents/java-agent/features/real-time-profiling-java-using-jfr-metrics`; ND-pc = `new-relic-control/pipeline-control/overview`; ND-drop = `logs/ui-data/drop-data-drop-filter-rules` and `data-apis/manage-data/drop-data-using-nerdgraph`; ND-bud = `data-apis/manage-data/data-ingest-budgets/overview`; ND-acct = `accounts/accounts-billing/account-structure/new-relic-account-structure`; ND-mt = `.../multi-tenancy/intro-to-multi-tenancy`; ND-um = `accounts/accounts-billing/new-relic-one-user-management/user-management-concepts`; ND-ut = `.../user-type`; ND-authd = `.../authentication-domains-saml-sso-scim-more`; ND-scim = `accounts/accounts/automated-user-management/scim-support-automated-user-management`; ND-audit = `accounts/accounts/account-maintenance/query-account-audit-logs-nrauditevent`; ND-keys = `apis/intro-apis/new-relic-api-keys`; ND-teams = `service-architecture-intelligence/teams/teams`; ND-price = `accounts/accounts-billing/new-relic-one-pricing-billing/new-relic-one-pricing-billing`; ND-dash = `query-your-data/explore-query-data/dashboards/*` (`introduction-dashboards`, `share-dashboards-publicly`, `dashboard-template-variables`, `manage-your-dashboard`); ND-pdf = `apis/nerdgraph/examples/export-dashboards-pdfpng-using-api`; ND-ct = `change-tracking/overview`; ND-qb = `query-your-data/explore-query-data/query-builder/introduction-query-builder`; ND-de = `.../browse-data/introduction-data-explorer`.

Repository keys: RES-NR = `docs/research/newrelic-notifications-and-nrql-reference.md`; RES-GN = `docs/research/grafana-notifications-datapath.md`; RES-GD = `docs/research/grafana-dashboards.md`; WM = `docs/what-migrates.md`; IR = `src/importer/core/notification_ir.py`; TR = `src/importer/sources/grafana/translate.py`; DT-SL = `src/importer/sources/dynatrace/service_levels.py`.

Fidelity: EXACT (same meaning, same behaviour), APPROXIMATE (usable, with named differences), DIFFERENT (same purpose, different model; needs redesign), NONE (no New Relic equivalent). Effort: S under a day, M a few days, L weeks, X not buildable (product limit).

Naming note. New Relic's current pages say "alert event" where older pages and the repo say "violation" or "incident" (redirect chain in ND-closed). An "issue" is the grouped unit that workflows notify on (ND-issues). This file uses alert event and issue.

## 1. Alerting

### 1.1 Rules and rule types

| Grafana concept | Grafana details | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|---|
| Grafana-managed alert rule | Query + optional expressions + one condition; labels, annotations, `for`, `keep_firing_for`, no-data and error state. GA-types, GA-queries. Provisioning `GET /api/v1/provisioning/alert-rules` (GA-http); TF `grafana_rule_group` (GA-tf, GTF-rule_group) | NRQL alert condition (static, baseline, outlier) | NerdGraph `alertsNrqlConditionStaticCreate`, `alertsNrqlConditionBaselineCreate` (REPO `emit/nerdgraph.py`); TF `newrelic_nrql_alert_condition` (NTF) | APPROXIMATE | Converts only query + optional reduce + one threshold on a Prometheus selector (one `avg/sum/min/max`) or a Loki `count_over_time`; rest manual with a fixed reason. WM "Grafana (phase 0)", TR | M to widen |
| Data source-managed rule | Mimir or Loki ruler, Prometheus rule format, no expressions, no no-data/error states (GA-nodata: "supported only for Grafana-managed") | Same NRQL condition | as above | APPROXIMATE | `extract-grafana` marks it manual (TR `R_DS_MANAGED`); `extract-grafana-notifications` reads ruler rules and the Prometheus rule-file loader turns one-comparison alerts into the same nodes. WM "Rule files" | S |
| Recording rule | Pre-computes a query into a new metric, Grafana-managed or data source-managed (GA-rec) | None. Closest listed provider resource is `newrelic_events_to_metrics_rule`; its semantics were not read | `newrelic_events_to_metrics_rule` (NTF list only) | DIFFERENT | Manual: `R_RECORD` in TR. Alerts on a recorded metric must be rewritten over the raw metric | M |
| Rule group, folder, evaluation interval | Group holds the interval; rules in a group run sequentially or concurrently; folder holds groups (GA-eval) | Alert policy is the container; no per-rule interval, evaluation follows the aggregation window (30 s to 1 h per WM) | `alertsPolicyCreate`; TF `newrelic_alert_policy` | DIFFERENT | One policy, `incidentPreference: PER_CONDITION` (REPO `core/apply.py:221`). The group interval only sets the window for `last`/instant reducers; folder and group are not mapped to policies | S (group or folder to policy) |
| Queries on other data sources | Grafana-managed rules may query many sources and mix them (GA-types) | NRQL over NRDB only; cross-account via `data_account_id` (RES-NR 2.4) | n/a | NONE unless the data is ingested | Only Prometheus and Loki convert; CloudWatch, Azure Monitor, Elasticsearch, SQL manual (WM) | depends on data path, see 13 |

### 1.2 Expressions and conditions

| Grafana concept | Grafana details | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|---|
| Reduce expression | Min, Max, Mean, Median, Sum, Count, Last over the query range (GA-queries) | NRQL aggregation per evaluation window; each window is separate, not a look-back over a range | `nrql.query`, `signal.aggregationWindow` | APPROXIMATE | last, mean, min, max, sum when equal to the PromQL aggregation; `count` and `median` manual (WM) | S |
| Math expression | Free-form arithmetic over queries, series joined by labels (GA-queries) | Arithmetic between aggregates in one `SELECT` on one event type | `nrql.query` | APPROXIMATE within one event; NONE across data sources | Manual (`R_MATH`) | M |
| Several queries combined by math or AND | e.g. `$A > 10 && $B > 5` | Compound alert condition: up to five conditions, AND/OR/NOT, same owning account (ND-compound) | TF `newrelic_alert_compound_condition` (NTF); NerdGraph mutation name UNVERIFIED | APPROXIMATE | Manual (`R_MULTI`, `R_MIXED`) | L |
| Resample | Re-aligns timestamps (GA-queries) | `TIMESERIES` bucket or aggregation window | n/a | DIFFERENT | Manual (`R_RESAMPLE`) | X |
| SQL expression | MySQL-like transform over earlier results (GA-queries) | None in NRQL | n/a | NONE | Manual (`R_SQL`) | X |
| Classic condition | Listed by the importer as a manual reason; not in the current expression list (GA-queries lists Reduce, Math, Resample, SQL, Threshold) | n/a | n/a | NONE | Manual (`R_CLASSIC`) | X |
| Threshold: gt, gte, lt, lte, eq, ne | GA-queries | Term `operator` ABOVE, ABOVE_OR_EQUALS, BELOW, BELOW_OR_EQUALS, EQUALS, NOT_EQUALS (RES-NR 2.2); at most CRITICAL + WARNING terms | `terms` / `term {}` | EXACT (operator only) | Converts | done |
| Threshold ranges (within, outside, inclusive) | `$A > 0 AND $A < 10` (GA-queries) | No single term; two conditions or a rewritten query | n/a | NONE | Manual (`R_RANGE`) | M |
| Recovery threshold | Alert returns to Normal only after a second threshold is crossed (GA-queries) | None; an alert event closes after the signal is non-breaching for the threshold's period (ND-closed) | n/a | NONE | Ignored with a note (`N_RECOVERY`) | X |
| Per-series evaluation | One alert instance per time series or table row (GA-queries) | `FACET`: each facet value is its own signal; at most 20,000 facet values; no `LIMIT` (ND-nrql) | `nrql.query` | APPROXIMATE | PromQL `by (...)` becomes `FACET`; without `by` a note says series are combined (`N_PER_SERIES`) | done |

### 1.3 States, timing, no data

| Grafana concept | Grafana details | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|---|
| Normal | No condition met (GA-eval) | No open alert event | n/a | EXACT | implicit | none |
| Pending and `for` | Condition met, pending period not elapsed; 0 skips Pending (GA-eval) | Threshold duration with occurrences `ALL` ("for at least"); not a visible state; an empty window resets the timer unless gap fill is set (RES-NR 2.2) | `threshold_duration`, `threshold_occurrences` | APPROXIMATE | Converted, rounded up to whole windows, at most 24 h (`N_PENDING`, `N_ROUNDED_FOR`) | done |
| Alerting | Pending elapsed; routed for notification (GA-eval) | Open alert event, issue created/active (ND-issues) | n/a | EXACT | done | none |
| Recovering and `keep_firing_for` | Firing alert stays firing for a hold period after recovery (GA-eval; TF `keep_firing_for`, GTF-rule_group) | Closes after non-breaching for the threshold's period (ND-closed); no separate hold setting | n/a | NONE | Ignored with a note (`N_KEEP_FIRING`) | X |
| Resolved | Normal (Resolved) is routed (GA-eval) | Issue trigger `CLOSED` on the workflow destination (RES-NR 1.2) | `notification_triggers` | EXACT | Not wired (plan only) | S |
| NoData state | Query returns nothing; raises separate `DatasourceNoData` alert with its own labels (GA-nodata) | Loss of signal: `expiration_duration` 30 s to 48 h, `open_violation_on_expiration` (RES-NR 2.2) | signal `expiration` block | APPROXIMATE | `no_data_state = Alerting` becomes loss of signal; NoData default has no mapping, noted | S |
| Error state | Evaluation timeout or repeated failure; raises `DatasourceError` alert (GA-nodata) | None found | n/a | NONE | Noted (`N_ERROR_STATE`) | X |
| `no_data_state` / `exec_err_state` options | OK, NoData/Error, KeepLast, Alerting (GTF-rule_group) | Alerting = loss of signal; OK = default; KeepLast = candidate `fill_option = LAST_VALUE` (held 2 h or the threshold duration, RES-NR 2.2) | `fill_option` | APPROXIMATE | KeepLast marked no equivalent; the fill option was not tried | S |
| `missing_series_evals_to_resolve` | Evaluations without a series before it resolves (GTF-rule_group) | `close_violations_on_expiration` for a lost signal | signal `expiration` | APPROXIMATE | Not read | S |
| Acknowledge | No acknowledgement of Grafana alert instances in the docs read (silences only) | Issue acknowledge in UI, workflow trigger `ACKNOWLEDGED` (ND-issues, `alerts/get-notified/acknowledge-alert-events`) | UI, NerdGraph | DIFFERENT | n/a | n/a |

### 1.4 Labels, annotations, grouping

| Grafana concept | Grafana details | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|---|
| Rule and series labels | Free key/values; they drive routing, silences, grouping (GA-pol) | Entity tags (`accumulations.tag.<name>` in workflow filters), facet values as `tags.<facet>` in muting rules and templates (RES-NR 1.2, 1.3). No free-form labels on a condition | TF `newrelic_entity_tags`; NerdGraph `taggingAddTagsToEntity` UNVERIFIED | APPROXIMATE | Labels are evaluated statically for routing decisions; none are written to New Relic | S |
| Annotations | `summary`, `description`, `runbook_url`, custom, Go-templated | `title_template` (256 chars), `description` (4,000 chars, Handlebars), `runbook_url` (RES-NR 2.2) | fields on the condition | APPROXIMATE | `description` or `summary` becomes the message (TR line 768); template text is not translated | S |
| Grouping (`group_by`) | Notification policy groups instances into one notification (GA-pol) | Issue preference: one issue per policy (default), per condition, per condition and signal (ND-pref) | `incidentPreference` | DIFFERENT | Fixed `PER_CONDITION` | S |
| Silence-like tags on the instance | `__alert_rule_uid__`, `grafana_folder` etc. added by Grafana | none | n/a | NONE | Reported as undecidable unless the rule carries them (RES-GN) | n/a |

### 1.5 Notification routing

| Grafana concept | Grafana details | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|---|
| Notification policy tree | Root policy, child policies, matchers `= != =~ !~`, first match wins unless "continue", group timers, mute and active timings on child policies only (GA-pol, GA-mute). Named trees: TF `grafana_apps_notifications_routingtree_v1beta1` (GTF) | Workflow with issue filter predicates (all must match; value list = any of) and destination configurations; filter limit 4,096 characters (RES-NR 1.2) | `aiWorkflowsCreateWorkflow`; TF `newrelic_workflow` | APPROXIMATE for matching, NONE for group wait/interval/repeat | Tree flattened to (filter, destinations) plans with a three-valued matcher and an "undecidable" list; read side and plan only, nothing created. WM "Grafana notifications" | M to wire |
| Simplified routing | Rule's `notification_settings` names a contact point directly (GTF-rule_group) | One workflow per contact point filtered on the condition | as above | APPROXIMATE | Not read | S |
| Contact points | 24 integration types in the repo's verdict table (22 blocks in TF `grafana_contact_point`, GTF) | 14 destination types (WEBHOOK, EMAIL, SERVICE_NOW, SERVICE_NOW_APP, PAGERDUTY_ACCOUNT_INTEGRATION, PAGERDUTY_SERVICE_INTEGRATION, JIRA, SLACK, SLACK_COLLABORATION, SLACK_LEGACY, MOBILE_PUSH, EVENT_BRIDGE, MICROSOFT_TEAMS, WORKFLOW_AUTOMATION) plus a channel per destination (RES-NR 1.1) | `aiNotificationsCreateDestination`, `aiNotificationsCreateChannel`; TF `newrelic_notification_destination`, `newrelic_notification_channel` | APPROXIMATE (email, webhook), DIFFERENT (12 webhook templates), NONE (threema, LINE, mqtt, wechat, wecom app mode); 6 are manual steps | Classified only: email and webhook convert, 12 webhook templates, 6 manual (slack, pagerduty, teams, jira, pushover, sns), 4 no equivalent. Secrets never read (RES-GN 1.4, WM) | M |
| Mute timing | Recurring interval, attached to child policies; stops notifications, evaluation continues (GA-mute) | Muting rule: condition on alert-event attributes plus one window with daily, weekly or monthly repeat; notifications withheld, only for workflows whose `mutingRulesHandling` is not `NOTIFY_ALL_ISSUES` (RES-NR 1.3) | `alertsMutingRuleCreate`; TF `newrelic_alert_muting_rule` | APPROXIMATE | Time ranges with weekdays or days 1 to 28 plus a time zone become schedules in a plan; the rest is reported. Not created | S to M |
| Active time interval | Notifications suppressed unless the time matches (GA-mute) | Complement of a window; needs several muting rules | same | DIFFERENT | Reported, not converted | M |
| Silence | Label matchers plus fixed start and end (GA-sil) | One-off muting rule | same | APPROXIMATE | Not read | S |
| Templates | Go templates for title and message | Handlebars message templates (RES-NR 1.2) | channel `payload`, `subject` | DIFFERENT | Only Grafana default templates map; custom ones reported, never translated | do not build |
| Inhibition rules | `source_matchers`, `target_matchers`, `equal` (TF `grafana_apps_notifications_inhibitionrule_v1beta1`, GTF) | Decisions (global, suggested, custom) merge related issues by time, attributes, topology (ND-dec); compound alert with NOT can express "A and not B" (ND-compound) | TF `newrelic_alert_compound_condition` | DIFFERENT | Not read | L |
| Contact point test, provenance | `X-Disable-Provenance: true` unlocks provisioned objects in the UI (GA-http) | Importer manifest and rollback only | n/a | NONE | n/a | n/a |

### 1.6 Provisioning surface, side by side

| Grafana | New Relic |
|---|---|
| `grafana_rule_group` (GTF) | `newrelic_alert_policy` + `newrelic_nrql_alert_condition` (+ `newrelic_alert_compound_condition`) |
| `grafana_contact_point` | `newrelic_notification_destination` + `newrelic_notification_channel` |
| `grafana_notification_policy` | `newrelic_workflow` |
| `grafana_mute_timing` | `newrelic_alert_muting_rule` |
| `grafana_message_template` | channel `payload` Handlebars |
| `GET /api/v1/provisioning/{alert-rules,contact-points,policies,mute-timings,templates}` (GA-http) | NerdGraph queries under `actor.account.alerts` and `aiNotifications`/`aiWorkflows` (RES-NR) |

## 2. Incident response

Grafana OnCall and Incident are packaged as Grafana IRM, a paid Cloud add-on billed by monthly active IRM users (GC-irm, GC-irmset). IRM receives alerts through integrations, a route picks an escalation chain, alerts join alert groups, and responders are paged through schedules and personal notification rules; incidents are declared and managed beside it (GC-irm-intro). Terraform has `grafana_oncall_*` and `grafana_incident_*` resources (GTF list).

| Grafana concept | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort / recommended target |
|---|---|---|---|---|---|
| Integrations (alert sources into IRM) | Not needed: New Relic alerts are the source. Inbound from other tools: REST API issues source (ND-issues lists "REST API") | n/a | DIFFERENT | Contact point type `oncall` classified as a webhook template (RES-GN) | n/a |
| Schedules, shifts, overrides, rotations | None found. A search of the docs-website tree for oncall and on-call found no page: UNVERIFIED as a negative | n/a | NONE | Not read | Re-create in a paging product with a destination: PagerDuty (`PAGERDUTY_*_INTEGRATION`) or Opsgenie/Splunk On-Call via webhook templates (ND-integ). Vendor lifecycle of Opsgenie UNVERIFIED |
| Escalation chains, routes | Workflow with several destination configurations; escalation timing lives in the paging product | `newrelic_workflow` | DIFFERENT | Not read | Inventory report first |
| Personal notification rules (push, SMS, call) | `MOBILE_PUSH` and `EMAIL` destinations; no SMS or voice | `newrelic_notification_destination` | NONE | n/a | Paging product |
| Alert groups, ack, resolve | Issues with acknowledge and close; bulk actions in the issue feed (ND-issues) | UI, NerdGraph | APPROXIMATE | n/a | none |
| ChatOps (Slack, Teams) | Slack destination (OAuth sign-in per workspace, cannot be created by Terraform) and Teams destination (needs the New Relic Teams app); `update_original_message` is Slack only (RES-NR 1.1) | `newrelic_notification_destination` | APPROXIMATE | Manual step | Human step |
| Outgoing webhooks, automation | Workflow automation actions (PagerDuty incident create/resolve, Jira issue create/update; `workflow-automation/` tree) | TF `newrelic_workflow_automation` | APPROXIMATE | Not read | L |
| Incident declaration, roles, severities, timeline, postmortem | No declare-incident feature found. Issue page with AI summary (preview) and postmortems page exist (ND-resp; `alerts/alert-event-management/postmortems-applied-intelligence`) | UI | NONE | Not read | Jira or ServiceNow destination for ticketing (ND-integ) |
| Service Center | Entities, teams, ownership (ND-teams) | `newrelic_team`, `newrelic_entity_tags` | DIFFERENT | Not read | M |

What migrates: nothing automatically, and no importer code reads OnCall or Incident. Recommended: a read-only inventory of schedules, chains and routes into a human report (S), then rebuild in the chosen paging product.

## 3. SLOs

Grafana SLO: an SLI query returns a success ratio; an SLO is a target and a time window (30d in the provider examples); the app generates Prometheus recording rules and fast-burn and slow-burn alert rules into a Mimir or Prometheus-compatible data source (GC-slo intro and create; TF `grafana_slo` with `query`, `objectives {value, window}`, `alerting { fastburn, slowburn }`, `destination_datasource`, GTF-slo).

New Relic service levels: an SLI is a good-events and valid-events NRQL over any NRDB event; SLOs are a target and a window, rolling windows supported; single account only; subqueries and `sum(a) + sum(b)` unsupported; creating needs a full platform user (ND-slm create). Burn-rate alerts: the UI offers recommended fast-burn and slow-burn alerts and the page gives the formulas for custom thresholds (ND-slm alerts).

| Grafana concept | New Relic analogue | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|
| SLI (PromQL ratio) | SLI as good/valid event queries | `serviceLevelCreate` (ND-slmgql); TF `newrelic_service_level` (NTF) | APPROXIMATE (query must be rewritten over NRDB data) | None for Grafana. Dynatrace plans exist: `plan-service-levels`, DT-SL leaves queries as TODO | M (reuse DT-SL) |
| SLO target and window | SLO on the SLI | `serviceLevelUpdate` | EXACT for target; window APPROXIMATE (periods allowed are UNVERIFIED) | none | S |
| Generated burn-rate alert rules | NRQL conditions written on the service level; whether the API creates the recommended ones is UNVERIFIED | `newrelic_nrql_alert_condition` | APPROXIMATE | none | M |
| Generated recording rules | None needed | n/a | NONE | none | n/a |
| Sloth specs | Sloth emits Prometheus recording and multi-window multi-burn alert rules from a spec (SLOTH README). The generated rules use `rate`, which the translator keeps manual; map the spec, not the rules | `serviceLevelCreate` | DIFFERENT | none | M |
| Pyrra objectives | Pyrra generates burn-rate alerts (PYRRA README) with Kubernetes CRDs; CRD field names UNVERIFIED | same | DIFFERENT | none | M |
| SLO maintenance windows | Service level maintenance windows (`service-level-management/maintenance-windows-slm`) | UI | UNVERIFIED | none | n/a |

## 4. Synthetics and k6

Grafana Synthetic Monitoring groups checks as API Endpoint (ping, HTTP, DNS, TCP, traceroute), Multi Step (MultiHTTP), Scripted (k6) and Browser (k6 browser) (GC-sm checks); TF settings blocks also list `grpc` (GTF-synthetic_monitoring_check). HTTP, ping, DNS and TCP are built on Prometheus blackbox exporter (GC-sm intro). Results are Prometheus metrics and Loki logs, so alerting is ordinary Grafana alerting. Private probes are the open source Synthetic Monitoring Agent (GC-sm private).

New Relic monitor types: ping (simple), simple browser, scripted API, scripted browser, step monitor (codeless), certificate check, broken links (ND-syn, ND-synapi). NerdGraph names: `syntheticsCreateSimpleMonitor`, `...SimpleBrowserMonitor`, `...ScriptApiMonitor`, `...ScriptBrowserMonitor`, `...StepMonitor`, `...CertCheckMonitor`, `...BrokenLinksMonitor`, `syntheticsCreatePrivateLocation`, `syntheticsCreate{Once,Daily,Weekly,Monthly}MonitorDowntime` (ND-synapi). TF: `newrelic_synthetics_monitor` (SIMPLE, BROWSER), `_script_monitor`, `_step_monitor`, `_cert_check_monitor`, `_broken_links_monitor`, `_private_location`, `_secure_credential`, `_alert_condition`, `newrelic_monitor_downtime`.

| Grafana | New Relic | Fidelity | Importer today | Effort |
|---|---|---|---|---|
| Ping check (ICMP) | Ping monitor is an HTTP request, not ICMP (ND-syn) | DIFFERENT | none | S |
| HTTP check (assertions, TLS, redirects) | Ping monitor for status; scripted API for assertions | APPROXIMATE | none | M |
| Certificate expiry in HTTP check | Certificate check monitor | APPROXIMATE | none | S |
| DNS, TCP, traceroute | None found as monitor types | NONE | none | X (scripted API monitor can approximate TCP and DNS: UNVERIFIED) |
| MultiHTTP | Scripted API monitor | DIFFERENT (script rewrite) | none | L |
| k6 scripted check | Scripted API monitor (Node-based, not k6) | DIFFERENT | none | L |
| k6 browser check | Scripted browser or step monitor (Selenium-based per ND-syn) | DIFFERENT | none | L |
| Probe frequency from 10 s (API) and 60 s (browser) (GC-sm checks) | Periods differ; New Relic limits UNVERIFIED | DIFFERENT | none | n/a |
| Public probe locations | Public locations | APPROXIMATE (names differ) | none | S |
| Private probes (agent via APT, YUM, Docker, Kubernetes) | Private locations of containerised job managers (ND-synpl) | APPROXIMATE | none | M |
| Check alerts | `newrelic_synthetics_alert_condition`, multilocation condition | APPROXIMATE | none | S |
| Managed secrets | `newrelic_synthetics_secure_credential` | APPROXIMATE | none | S |
| Grafana Cloud k6 load testing | No load-test product found | NONE | n/a | keep k6 |
| k6 results into New Relic | k6 sends to New Relic through the StatsD integration (needs a custom `xk6-output-statsd` build) or the OpenTelemetry output (K6NR) | EXACT as a data path | n/a | S (guide) |

Dynatrace synthetic inventory and plan code already exists in the repo (WM "Dynatrace dashboards and inventory"), which is the starting point for a Grafana plan. Billing: included monthly checks are Free 500, Standard 10K, Pro 1M, Enterprise 10M, except ping (ND-synlim); Grafana bills browser and API executions separately and counts each probe run (GC-sm checks).

## 5. Frontend: Faro vs Browser and session replay

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Faro Web SDK sending RUM metrics, errors, logs and client traces (GC-faro) | Browser agent; TF `newrelic_browser_application`; browser key for ingest (ND-keys) | DIFFERENT (re-instrument; no history) | none | S per app, guide |
| Frontend Observability app and dashboards | Browser monitoring UI | DIFFERENT | none | n/a |
| Session replay | Browser session replay (ND-replay); also mobile session replay | UNVERIFIED that Faro has session replay | none | n/a |

## 6. Profiles: Pyroscope

Pyroscope is continuous profiling in the LGTM family, with flame graphs, SDK or Alloy auto-instrumentation (PYRO, GC-prof). New Relic: real-time continuous profiling for Java through JFR (ND-jfr), the APM thread profiler page, and Pixie CPU flame graphs on Kubernetes (ND-pixie). No multi-language profile store found.

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Profiles, flame graphs, all languages | JFR profiling for Java; Pixie CPU flame graphs (Kubernetes) | DIFFERENT for Java and Kubernetes; NONE for other languages | none | X |
| Profile data and history | None migrates | NONE | none | X |

## 7. Kubernetes

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Kubernetes Monitoring (Alloy-based collection, built-in alerts, Loki logs) (GC-k8s) | Kubernetes integration with cluster explorer (ND-k8s); OpenTelemetry Kubernetes path (ND-k8sotel); Pixie auto-telemetry (ND-pixie); predefined alert policy (`kubernetes-pixie/kubernetes-integration/installation/predefined-alert-policy`) | DIFFERENT | none | re-install, guide |
| Built-in alert rules and mixins | Predefined and recommended alert policies | APPROXIMATE | none | n/a |
| Dual write during transition | Alloy dual write to OTLP/remote write, planned by `plan-grafana-datapath` (RES-GN 6.3) | APPROXIMATE | plan only | done |

## 8. Application observability, Beyla, eBPF

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Application Observability: OTel SDKs + Alloy + prebuilt views (GC-app) | APM agents, or OpenTelemetry sent to the OTLP endpoint (RES-GN 6.2); TF `newrelic_application_settings` | DIFFERENT | OTLP path planned | n/a |
| Beyla (eBPF, no code change; HTTP/gRPC, SQL, Redis, Kafka; donated to OpenTelemetry as OBI) (BEYLA) | eBPF APM, no-code and language-agnostic (ND-ebpf); Pixie (ND-pixie). The `ebpf/overview` page is wrapped in a comment in the source, so its publication status is UNVERIFIED | APPROXIMATE | none | n/a |
| Tempo span metrics, service graph | Distributed tracing, service maps | DIFFERENT | none | n/a |

## 9. Cost and usage management

| Grafana | Details | New Relic analogue | API / Terraform | Fidelity | Importer | Effort |
|---|---|---|---|---|---|---|
| Adaptive Metrics | Recommends and applies aggregation rules from usage; rules via UI, API or Terraform (GC-adm) | Metric pruning rule strips named attributes from dimensional metrics before storage | TF `newrelic_metric_pruning_rule` (NTF) | DIFFERENT (manual, no usage engine) | none | M |
| Cardinality management | Dashboards and limits. Page not found: UNVERIFIED | Per-metric daily cardinality limit, default 100,000, overridable | TF `newrelic_cardinality_management` | APPROXIMATE | none | S |
| Adaptive Logs, drop at client | Docs read cover dropping at Alloy with `stage.drop` (GC-logs); Adaptive Logs content UNVERIFIED | Pipeline Control cloud rules (NRQL-based drop, per account, charged on data dropped) and gateway rules (ND-pc); NRQL drop rules are end of life (dates in the two pages read conflict, 2026-06-30 and 2026-08-31, both past) | TF `newrelic_pipeline_cloud_rule`; legacy `nrqlDropRulesCreate` | DIFFERENT | none | M |
| Usage alerts | Usage metrics `grafanacloud_*` (GC-lim) | Data ingest budgets (monthly GB, track and alert) (ND-bud); usage queries on `NrConsumption` | UI | APPROXIMATE | none | n/a |
| Log obfuscation | Alloy stages | Obfuscation rules | TF `newrelic_obfuscation_rule`, `_expression` | APPROXIMATE | none | S |

## 10. Organisation and access

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Organisation: isolates dashboards, data sources, folders, teams, alerts inside one instance; users and Enterprise licence shared (GD-org) | Account is the data and access unit; organisation holds accounts, users, data; Free and Standard have one account, Pro and Enterprise can add accounts (ND-acct); multi-tenancy of organisations (ND-mt) | DIFFERENT | none | S to map |
| Cloud stack | Account (plus organisation) | APPROXIMATE | none | n/a |
| Folders with permissions | No folder concept found in the dashboard pages read: UNVERIFIED | NONE | none | X |
| Teams | User groups for access (ND-um); product "teams" for ownership (ND-teams) | APPROXIMATE | none | M |
| Basic roles Viewer, Editor, Admin (GD-roles); RBAC fixed and custom roles in Enterprise and Cloud (GD-rbac) | Groups, roles (sets of permissions) and access grants over targets; custom roles and groups need Pro or Enterprise; user type must also allow the feature (ND-um) | DIFFERENT | none | M |
| Service accounts and tokens (GD-sa) | User key (NerdGraph), license key and browser key (ingest); system identities (ND-keys) | APPROXIMATE | none | n/a |
| Cloud access policies and tokens | Ingest keys and user keys | APPROXIMATE | none | n/a |
| Resource ownership | Entity tags, teams | APPROXIMATE | none | S |

NerdGraph names: `userManagementCreateGroup` (groups, ND), `entityManagementCreateTeam` (teams, ND). TF: `newrelic_group`, `newrelic_team`, `newrelic_api_access_key`, `newrelic_account_management`.

## 11. Dashboards and related

| Grafana | New Relic | API / Terraform | Fidelity | Importer today | Effort |
|---|---|---|---|---|---|
| Dashboard (classic, v1, v2 JSON) | Dashboard with pages and widgets | `dashboardCreate`; TF `newrelic_one_dashboard`, `_json`, `_raw` | APPROXIMATE | Read + plan only; no Grafana dashboard created (WM "Grafana dashboards") | M |
| Folders | No equivalent found (UNVERIFIED) | n/a | NONE | folders exported only | X |
| Library panels | Inline copy per dashboard | n/a | DIFFERENT | read; missing ones are manual | S |
| Variables | Template variables: nrql, enum, string (RES-GD 6; ND-dash) | dashboard JSON | APPROXIMATE | Variable model not in the IR yet (RES-GD) | M |
| Playlists | TV mode on a dashboard (ND-dash); no playlist | n/a | DIFFERENT | none | X |
| Snapshots | Snapshot URL via `dashboardCreateSnapshotUrl` (ND-pdf) | NerdGraph | APPROXIMATE | none | S |
| Public dashboards (Enterprise, Cloud) | Public dashboards via live URL, Pro and Enterprise (ND-dash) | UI | APPROXIMATE | none | n/a |
| Reports (scheduled PDF or CSV email; Enterprise and Cloud) (GD-reports) | No native scheduled report found; PDF/PNG export through the API for external scheduling (ND-pdf) | `dashboardCreateSnapshotUrl` | DIFFERENT | none | M |
| Annotations | Change tracking markers on charts; deployments and change events (ND-ct); RES-GD says dropped | NerdGraph | DIFFERENT | Dropped and reported | M |
| Panel types | See RES-GD 4.1: line, area, bar, billboard, pie, table, markdown in the IR; heatmap, histogram, gauge etc. exist but are not yet created | n/a | APPROXIMATE | Planned | M |

## 12. Explore and ad hoc

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| Explore (GD-explore) | Query builder and data explorer (ND-qb, ND-de) | APPROXIMATE | n/a | n/a |
| Query history, kept two weeks per user, not shared (GD-qhist) | History in the query builder: not confirmed in pages read; UNVERIFIED | UNVERIFIED | n/a | n/a |
| Query Library | Notebooks (`newrelic_notebook`) as saved queries; Query Library page 404, UNVERIFIED | DIFFERENT | n/a | n/a |
| Query language: PromQL, LogQL, TraceQL | NRQL; PromQL-to-NRQL translation exists (RES-GN D8) | DIFFERENT | Subset translated | M |

## 13. Plugins and data source breadth

New Relic has no live federation of external data sources into dashboards in the pages read: data must be ingested.

| Grafana data source (GD-ds) | New Relic route | Fidelity |
|---|---|---|
| CloudWatch | AWS integrations and Metric Streams (repo: `docs/research/aws-native-coverage.md`, `metric-names-verified.md`) | APPROXIMATE |
| Azure Monitor | Azure integrations (`azure-monitor` page in docs tree) | APPROXIMATE |
| Google Cloud Monitoring | GCP integrations (repo: `docs/research/gcp-native-coverage.md`) | APPROXIMATE |
| Elasticsearch | Elasticsearch host integration (docs tree) for cluster metrics; log search content does not move | DIFFERENT |
| MySQL, PostgreSQL, MSSQL | Database monitoring integrations (docs tree has Azure DB and `opentelemetry/db360/mssql`, `postgresql`); querying business tables is NONE | DIFFERENT |
| Prometheus, Mimir, Loki, Tempo | Remote write, OTLP, Alloy dual write (RES-GN 6) | APPROXIMATE |
| Zabbix and other plugins | No route found (UNVERIFIED) | NONE |

## 14. Authentication and administration

| Grafana | New Relic | Fidelity | Importer | Effort |
|---|---|---|---|---|
| SAML (Enterprise, Cloud), OAuth/OIDC providers, LDAP (GD-auth, GD-ent) | SAML SSO in paid editions (Azure AD, Okta, OneLogin, generic SAML 2.0) via authentication domains (ND-authd); OIDC not found, UNVERIFIED | APPROXIMATE | none | n/a |
| SCIM provisioning (Enterprise, Cloud) (GD-scim) | SCIM 2.0 service provider with IdP apps for Azure AD, Okta, OneLogin (ND-scim) | APPROXIMATE | none | n/a |
| Audit logs (Enterprise, Cloud; file, Loki or console exporters) (GD-audit) | `NrAuditEvent` queried with NRQL (ND-audit) | APPROXIMATE | none | n/a |
| Service account tokens | User keys; license and ingest keys (ND-keys) | APPROXIMATE | none | n/a |

## 15. Licensing and pricing facts that affect migration

Facts only, from official pages.

| Topic | Grafana | New Relic |
|---|---|---|
| Free tier | Forever-free Cloud plan: lower usage limits, shorter retention, community support (GC-intro). Tier names Pro and Advanced: not read, UNVERIFIED | Free edition; 100 GB ingested per month free (ND-price); editions Free, Standard, Pro, Enterprise |
| Features gated by edition | Enterprise and paid Cloud: RBAC, data source permissions, reporting, team sync, SAML, SCIM, audit, query caching (GD-ent, GD-reports, GD-scim, GD-audit) | SAML SSO needs a paid edition; multiple accounts, custom roles and groups, public dashboards need Pro or Enterprise (ND-acct, ND-um, ND-dash) |
| Billing units | Usage limits by metrics, logs, traces (GC-lim); IRM per monthly active user (GC-irm); browser checks billed separately, each probe run counts (GC-sm) | Data (GB ingested, Original or Data Plus with up to 90 days retention) plus Users (Basic, Core, Full platform) or Core Compute (preview) (ND-price) |
| Users | Roles per user | Basic free; Full platform needed for APM/infra/browser/synthetics UIs and to create service levels (ND-price, ND-slm) |
| Not counted as ingest | n/a | Basic alert data such as `NrAiIncident`, synthetic checks, `NrConsumption`, `NrAuditEvent` (ND-price) |
| Data reduction cost | n/a | Pipeline Control cloud rules charged on data dropped; gateway on data received (ND-pc) |

## 16. Manager summary (20 rows)

| # | Grafana concept | New Relic equivalent | Fidelity | Importer status |
|---|---|---|---|---|
| 1 | Alert rule, simple Prometheus or Loki | NRQL alert condition | APPROXIMATE | Converts (approximate) |
| 2 | Alert rule with math, several queries, SQL, resample | NRQL or compound condition | DIFFERENT | Manual |
| 3 | Recording rule | none | NONE | Manual |
| 4 | Pending period `for` | Threshold duration | APPROXIMATE | Converts |
| 5 | `keep_firing_for`, recovery threshold, Error state | none | NONE | Ignored with a note |
| 6 | No-data handling | Loss of signal | APPROXIMATE | Partial |
| 7 | Multi-dimensional alerts | `FACET` | APPROXIMATE | Converts |
| 8 | Notification policy tree | Workflows with issue filters | APPROXIMATE | Plan only |
| 9 | Contact points (24) | Destinations and channels (14) | APPROXIMATE | Classified; nothing created |
| 10 | Mute timings and silences | Muting rules | APPROXIMATE | Plan only |
| 11 | Notification templates | Handlebars | DIFFERENT | Defaults only |
| 12 | Inhibition rules | Decisions, compound NOT | DIFFERENT | Not read |
| 13 | OnCall and Incident (IRM) | none native | NONE | Not read |
| 14 | SLO | Service levels | APPROXIMATE | Not built |
| 15 | Synthetic Monitoring | Synthetics monitors | APPROXIMATE | Not built |
| 16 | k6 | none; k6 sends to New Relic | DIFFERENT | n/a |
| 17 | Dashboards | Dashboards | APPROXIMATE | Plan only |
| 18 | Faro, Pyroscope, Beyla | Browser agent, JFR/Pixie, eBPF APM | DIFFERENT | Not built |
| 19 | Adaptive metrics and logs | Metric pruning, pipeline cloud rules | DIFFERENT | Not built |
| 20 | Org, RBAC, SSO, SCIM | Accounts, groups and roles, SAML, SCIM | DIFFERENT | Not built |

## 17. Areas with no migration path and the manual approach

- Recording rules: write the aggregate inline in the NRQL, or build an events-to-metrics rule by hand.
- Error state, `keep_firing_for`, recovery threshold: accept a different alert shape; consider `fill_option` and a longer threshold duration (UNVERIFIED effect).
- Range thresholds: two conditions.
- SQL expressions, resample, cross-source math: rewrite as NRQL over ingested data.
- Custom Go templates: rewrite messages as Handlebars by hand.
- Inhibition: decisions or compound NOT, designed per case.
- On-call schedules, escalation chains, incident declaration, timelines: rebuild in a paging or ticketing tool reachable by workflow destinations.
- k6 load testing: keep k6, send results to New Relic.
- DNS, TCP, traceroute checks: scripted API monitor or keep Grafana SM.
- Faro data, Pyroscope profiles, history of any kind: none carries over.
- Folders with permissions, playlists, scheduled reports: re-create with groups, TV mode, and an external scheduler on the PDF API.
- Grafana annotations: change tracking markers.
- Data sources that are not ingested (Zabbix, many plugins): ingest or keep Grafana.
- Fine-grained RBAC and data source permissions: redesign with groups, roles and access grants.

## 18. What to build next (impact x effort)

| Rank | Item | Impact | Effort | Exact target |
|---|---|---|---|---|
| 1 | Create email and webhook destinations, channels and workflows from the existing Grafana notification plan | High | M | `aiNotificationsCreateDestination`, `aiNotificationsCreateChannel`, `aiWorkflowsCreateWorkflow`; TF `newrelic_notification_destination`, `newrelic_notification_channel`, `newrelic_workflow` |
| 2 | Map folder or rule group to a policy and issue preference | Medium | S | `alertsPolicyCreate` (`incidentPreference`); TF `newrelic_alert_policy` |
| 3 | Mute timings to muting rules (convertible subset) | Medium | S to M | `alertsMutingRuleCreate`; TF `newrelic_alert_muting_rule`; set workflow `mutingRulesHandling` accordingly |
| 4 | Create Grafana dashboards from the existing plan, with a variable model | High | M | `dashboardCreate`; TF `newrelic_one_dashboard` |
| 5 | No-data and KeepLast mapping, stale series | Medium | S | NRQL condition `fill_option = LAST_VALUE`, `close_violations_on_expiration`, `expiration_duration` |
| 6 | Write rule labels as entity tags | Medium | S | TF `newrelic_entity_tags` |
| 7 | Grafana SLO reader to service level plans | Medium | M | `serviceLevelCreate`, `serviceLevelUpdate`; TF `newrelic_service_level` |
| 8 | Synthetic Monitoring reader to monitor plans (ping/HTTP, cert, private locations) | Medium | M | `syntheticsCreateSimpleMonitor`, `syntheticsCreateCertCheckMonitor`, `syntheticsCreatePrivateLocation`; TF `newrelic_synthetics_monitor`, `newrelic_synthetics_cert_check_monitor`, `newrelic_synthetics_private_location` |
| 9 | Two-query AND rules to compound conditions | Medium | L | TF `newrelic_alert_compound_condition` (NerdGraph name UNVERIFIED) |
| 10 | Read-only inventory of OnCall, IRM and RBAC | Low to medium | S | none (report only) |
| 11 | Cost rules guidance | Low | S | `newrelic_metric_pruning_rule`, `newrelic_pipeline_cloud_rule` |

## 19. UNVERIFIED list

1. No on-call scheduling, no declare-incident, no folders, no scheduled reports, no query history, no OIDC in New Relic: negatives from absence in the pages and tree search only.
2. Opsgenie and OnCall OSS lifecycle status.
3. Grafana Cloud Pro and Advanced tier contents; Adaptive Logs, Adaptive Traces and cardinality pages (404 or redirect).
4. Whether the New Relic SLO API generates burn-rate alert conditions; allowed SLO periods.
5. NerdGraph mutation names for compound conditions, entity tags and pipeline cloud rules.
6. Semantics of `newrelic_events_to_metrics_rule` as a recording-rule analogue.
7. Whether `fill_option = LAST_VALUE` approximates KeepLast.
8. Publication status of the eBPF overview page; Pixie lifecycle.
9. Sloth and Pyrra field names beyond their READMEs.
10. New Relic synthetics periods and scripted API ability to cover TCP and DNS.
11. Whether Faro offers session replay.
12. Everything inherited from RES-GN and RES-NR is as verified there.
