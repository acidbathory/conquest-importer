# Observability migrations in the field: practices, failure modes, and what our tool does about them

Date: 2026-11-10. Research only: public web pages read with plain HTTP fetches. No tenant, cloud or
New Relic call was made. Written for the Conquest Importer (Grafana, Dynatrace, CloudWatch and GCP
to New Relic).

## 0. How to read this, and how far to trust it

Evidence labels used on every claim that is not obvious:

| Label | Meaning |
|---|---|
| VERIFIED | I read the page in this session and it says this (vendor documentation of its own product behaviour, a published book, a specification). It is true of that product on that date; it is not proof that it works for a customer. |
| VENDOR CLAIM | A vendor says it about its own service, case study, price or timeline. Marketing incentives apply. |
| PRACTITIONER ANECDOTE | One named or anonymous user describes what happened to them. Useful, not statistics. |
| UNVERIFIED | Common knowledge or my own inference that I did not confirm against a source in this session. Treat as a hypothesis. |

Honest limits of this research (read before relying on it):

- Most "how to migrate" material is written by a vendor that wants you to move to it. That is a
  bias on every playbook below. The only neutral sources I could read are the Google SRE books, the
  Prometheus and OpenTelemetry documentation, and product documentation of the semantics involved.
- I could not read: Reddit (r/sre, r/devops, r/observability: blocked or script-rendered), conference
  talk videos and slides (KubeCon, SREcon, Monitorama), Splunk and Chronosphere pages (HTTP 403),
  Datadog monitor documentation (page text empty without a browser), most consultancy write-ups, and
  GitHub issue bodies (the API rate limit was reached after a few repository searches). Hacker News
  was searchable but produced almost no first-hand migration accounts. So the "practitioner" layer
  here is thin. Section 8 lists exactly what to read next if someone has a browser.
- I found no public source for the commonly repeated figure that automatic dashboard conversion
  reaches "60 to 80 percent". See section 3.1 for what evidence exists instead.
- Nothing here was tested against a live system.

## 1. Sources read (with labels)

| id | source and URL | what it gave me | label |
|---|---|---|---|
| F1 | Last9, "Observability Migration: Prove the TCO, Keep the Effort Small", https://last9.io/blog/observability-platform-migration/ (dated 2025-01-09 in the page) | The most complete playbook I found: TCO worksheet from invoices, four workstreams, dual-ship, written exit criteria, cutover order, checklist, five failure causes, "avoid lift and shift" | VENDOR CLAIM (competitor selling the target; practices are generic and plausible, numbers are made up examples) |
| F2 | Grafana docs, "Import data source-managed rules to Grafana-managed rules", https://grafana.com/docs/grafana/latest/alerting/alerting-rules/alerting-migration/ | Exactly which semantic settings Grafana adds when it imports Prometheus rules (query offset 1m, no-data preserved as Normal via a prometheus_math expression, "missing series evaluations to resolve" = 1, sequential evaluation, originals left intact, "test and validate your import process before migrating all", import notification config first) | VERIFIED (product behaviour) |
| F3 | Grafana blog, "How to migrate to Grafana IRM", https://grafana.com/blog/how-to-migrate-to-grafana-irm-find-the-right-path-for-your-organization/ | Typical timeframe "three to four weeks, 50 to 60 hours of professional service time"; audit, Terraform, test environment, office hours, post-migration support | VENDOR CLAIM |
| F4 | Grafana blog, Prezi: "Inside Prezi's cost-saving switch ... from PagerDuty", https://grafana.com/blog/inside-prezis-cost-saving-switch-to-grafana-alerting-grafana-oncall-and-grafana-incident-from-pagerduty/ | First-hand account on a vendor blog: 133 services, 94 users, 27 teams; about four weeks; deliberate lift-and-shift because of a deadline; alerts duplicated to the new tool early with an Alertmanager route using `continue: true`; onboarding guide; forwarding to PagerDuty disabled only after all teams confirmed | PRACTITIONER ANECDOTE (published by the target vendor) |
| F5 | Grafana blog, "From Datadog to Grafana Cloud", https://grafana.com/blog/from-datadog-to-grafana-cloud-why-companies-migrate-and-how-it-changes-business-for-the-better/ | LexisNexis: Datadog migration "in four months" with more than 50 percent cost saving (their quote); another firm moved over 20 million active series and hundreds of dashboards | VENDOR CLAIM |
| F6 | Elastic, "Grafana metrics migration" services package, https://www.elastic.co/consulting/services-packages/grafana-metrics-migration | A vendor's scoping of such a project in countable caps: 6 ingest sample configs, 15 assisted panels, 3 alert rules; phases: start gate, inventory and discovery, ingest then convert then validate, side-by-side walkthrough, written residual backlog. Out of scope: historical data copy, pixel-perfect parity, notification redesign, cutover ("Customer remains dual-running Grafana throughout") | VENDOR CLAIM (scope document, but a useful honest picture of how little is automated) |
| F7 | elastic/observability-migration-platform README, https://github.com/elastic/observability-migration-platform | "Unsupported translations are marked for manual review instead of being silently dropped"; offline migrate then review typed payloads then upload; `--validate` flag exists (seen in F6 text) | VERIFIED (what the README says); quality of the tool UNVERIFIED |
| F8 | Elastic Observability Labs, Prometheus metrics in Elasticsearch, https://www.elastic.co/observability-labs/blog/prometheus-metrics-elasticsearch-faster-cheaper-datadog | Claims "approximately 50% less than Datadog", "30x faster than Prometheus", and a dashboards-and-alerts migration path | VENDOR CLAIM (marketing numbers; ignore the numbers) |
| F9 | OpenTelemetry blog, dual .NET metrics export, https://opentelemetry.io/blog/2026/dual-dotnet-metrics-export-with-otlp-and-prometheus/ | The project's own rationale for dual export: migrate "without having a single cut-over event", export to both systems "so that before and after states can be compared ... no loss of production visibility ... or driving alerting" | VERIFIED (project documentation) |
| F10 | OpenTelemetry semantic conventions, HTTP migration, https://opentelemetry.io/docs/specs/semconv/non-normative/http-migration/ | A standard dual-emit mechanism: `OTEL_SEMCONV_STABILITY_OPT_IN=http/dup` emits old and new attribute names at once "allowing for a phased rollout"; old major version maintained at least six months. Lists renames such as `http.status_code` to `http.response.status_code` | VERIFIED |
| F11 | Google SRE Workbook, "Alerting on SLOs", https://sre.google/workbook/alerting-on-slos/ | Four parameters for judging an alert (precision, recall, detection time, reset time); burn rate; 5 percent of a 30-day budget in 1 hour is burn rate 36; 14.4 for 2 percent in 1 hour; multiwindow multi-burn-rate alerts | VERIFIED (published book) |
| F12 | Google SRE Workbook, "Implementing SLOs", https://sre.google/workbook/implementing-slos/ | Starting from user-visible indicators | VERIFIED (only skimmed) |
| F13 | Prometheus, "Alerting" best practices, https://prometheus.io/docs/practices/alerting/ | "keep alerting simple, alert on symptoms, have good consoles to allow pinpointing causes, and avoid having pages where there is nothing to do" | VERIFIED |
| F14 | Robust Perception, "Rate then sum, never sum then rate", https://www.robustperception.io/rate-then-sum-never-sum-then-rate/ | Only `rate`, `irate`, `increase`, `resets` are safe directly on counters; sum-then-rate produces spurious spikes at restarts | VERIFIED (author is a Prometheus maintainer; blog post, 2016) |
| F15 | Prometheus docs: functions https://prometheus.io/docs/prometheus/latest/querying/functions/ and querying basics (staleness) https://prometheus.io/docs/prometheus/latest/querying/basics/ | `increase()` extrapolates to the ends of the range; series are marked stale when they disappear | VERIFIED |
| F16 | Grafana docs, No Data and Error states, https://grafana.com/docs/grafana/latest/alerting/fundamentals/alert-rule-evaluation/nodata-and-error-states/ | No Data and Error are distinct alert states with a pending period; default behaviour raises a `DatasourceNoData` alert | VERIFIED |
| F17 | New Relic docs, NRQL alert conditions, https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/ | Loss of signal "requires a signal to be present before it can detect that the signal is lost"; gap filling options; evaluation delay up to 120 minutes; `COMPARE WITH` incompatible with NRQL alerting; sliding windows via `SLIDE BY` | VERIFIED |
| F18 | New Relic docs, "Migrate from Datadog to New Relic" tutorial series, https://docs.newrelic.com/docs/tutorial-dd-migration/migrating-from-dd.md (and `making-the-switch.md`, `migrating-dashboards.md`, `installing-monitor.md`) | The vendor's own Datadog migration guide is a product tour (entities, premade dashboards, "try New Relic without migrating your stack", pricing, free tier). It has no parity testing, no dual-run length, no alert-order or rollback guidance | VERIFIED (the absence), VENDOR CLAIM (the content) |
| F19 | New Relic docs, "Cloud migration: Create application baselines", https://docs.newrelic.com/docs/new-relic-solutions/new-relic-solutions/create-application-baselines.md | Measure a baseline before you move (answer: slower? less stable? losing customers?), list components with owners | VERIFIED (about cloud migration, same idea applies) |
| F20 | New Relic docs: Prometheus HA deduplication https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure/prometheus-high-availability-ha.md ; drop data with remote write ...remote-write-drop-data.md ; Grafana with PromQL ...grafana-support-prometheus-promql.md | New Relic dedupes HA replicas only if three label values are set on every replica; remote write can drop data; Grafana can query New Relic as a Prometheus data source | VERIFIED |
| F21 | Grafana Mimir docs, mimirtool, https://grafana.com/docs/mimir/latest/manage/tools/mimirtool/ | `mimirtool analyze` extracts metric usage from Grafana, dashboard JSON and rule YAML (the basis of "migrate only the metrics anyone uses") | VERIFIED |
| F22 | OpenTelemetry, Sampling, https://opentelemetry.io/docs/concepts/sampling/ | Head versus tail sampling; tail sampling keeps error and slow traces | VERIFIED |
| F23 | grafana/dashboard-linter README, https://github.com/grafana/dashboard-linter | Lints dashboard JSON for common mistakes; usable as a pre-check on dashboards before conversion | VERIFIED |
| F24 | Grafana blog, translating Datadog metrics into OTLP with the Collector and Alloy, https://grafana.com/blog/translate-datadog-metrics-into-otlp-with-the-opentelemetry-collector-and-grafana-alloy/ | A receiver path that keeps Datadog agents in place while data goes elsewhere (upstream review "a few weeks" at time of writing); Grafana offers to automate dashboard, alert and SLO migration | VENDOR CLAIM |
| F25 | Honeycomb docs, migration process and recommended migrations, https://docs.honeycomb.io/troubleshoot/product-lifecycle/migration-process | These are migrations between Honeycomb's own products (beelines, distributions, semantic conventions), not provider-to-provider playbooks. No vendor-to-vendor playbook found on Honeycomb | VERIFIED (the absence) |
| F26 | Hacker News, "Ask HN: Grafana or Datadog?", https://news.ycombinator.com/item?id=45176316 | One commenter frames it as paying with time versus money; another says they talked to companies paying about 40 thousand and 80 thousand dollars a month for Datadog and unhappy with it | PRACTITIONER ANECDOTE (numbers unverifiable) |
| F27 | This repo's own earlier notes: `docs/research/grafana-migration-field-notes.md` (S1 to S59, 38 edge cases), `docs/research/newrelic-grafana-platform-analogues.md`, `docs/research/promql-to-nrql-census.md` | Converter tool survey (Elastic, Perses, and others), pain points, cardinality and ingest effects, 15 ranked recommendations. Not repeated here; cited as "field notes case N / rank N" | as labelled there |

Consultancies, Datadog, Dynatrace (Monaco overview only, https://docs.dynatrace.com/docs/deliver/configuration-as-code/monaco),
Splunk/SignalFx, Chronosphere and Better Stack published no provider-to-provider migration playbook
that I could read. Better Stack and Last9 publish comparison pages (UNVERIFIED content, not read in
depth). Dynatrace Monaco is configuration-as-code for Dynatrace itself, not a migration tool to
another vendor.

## 2. The phases that recur

Synthesis of F1 to F6, F9 to F11, F19 and the field notes. Where only one vendor says it, I say so.

### 2.1 Inventory and triage: decide what NOT to migrate

- Pull what is actually used: dashboards, monitors and saved views opened in the last 90 days;
  "everything else is a candidate to leave behind" (F1, VENDOR CLAIM). Copying dashboards nobody
  opens is listed as a top failure cause (F1).
- Use metric usage, not just dashboard usage: `mimirtool analyze` against Grafana, dashboard JSON
  and rule files shows which metric names anything references (F21, VERIFIED). Unreferenced metrics
  need not be shipped at all. This is the biggest cost lever (section 2.6).
- Clean up labels before moving. Inconsistent `service`, `env`, `team` become broken dashboard
  variables on the other side (F1, VENDOR CLAIM; matches field notes cases on variables).
- Name an owner per dashboard and alert before the move (F1, F19). Alerts nobody owns are the
  first to be orphaned.
- Do not lift and shift noisy alerts: "if an alert fired fifty times last month and nobody acted on
  it, a faithful copy fires fifty times on the new platform too" (F1). Counter-evidence: Prezi chose
  lift and shift on purpose under a deadline and said so (F4); the choice is a trade-off, not a rule.
- Start with a time-boxed shortlist. Elastic scopes its service as 15 panels and 3 alert rules
  (F6), i.e. even a vendor paid to do it does not promise the whole estate. VENDOR CLAIM about
  its own scoping.

### 2.2 Instrumentation strategy: OpenTelemetry-first versus agent swap

- Standard advice: send OpenTelemetry or Prometheus data and the move is "an endpoint and
  credentials change"; vendor-specific SDKs and custom agent checks are the exceptions to list up
  front (F1, VENDOR CLAIM). The OpenTelemetry project itself argues for incremental migration over
  a single cut-over (F9, VERIFIED).
- The vendor agent can often stay: the Datadog Agent accepts additional endpoints (F1) and a
  Collector receiver can translate Datadog metrics to OTLP (F24, VENDOR CLAIM). So the choice is
  usually "keep the old agent for now, add a second destination", and replace agents later.
- OpenTelemetry has its own migrations: attribute renames (for example `http.method` to
  `http.request.method`) mean dashboards and alerts that group by attribute can silently break when
  an SDK is upgraded mid-migration. The project provides a dual-emit switch
  (`OTEL_SEMCONV_STABILITY_OPT_IN=http/dup`) for exactly this (F10, VERIFIED). Do not change
  SDK versions and backends at the same time (UNVERIFIED as folk advice, but follows from F10).

### 2.3 Dual-write or parallel run, and how long

- Sources agree it is the lowest-risk path: send the same telemetry to both for a period, compare on
  real incidents, back out if the new platform falls short; the cost is a temporary double bill
  (F1, F9). Mechanisms: second `remote_write` block, second exporter in the Collector pipeline,
  `additional_endpoints` for the Datadog Agent (F1).
- Length: no neutral source gives a number. F1 says "a few weeks" and "a full window" for alerts to
  match; Prezi's whole migration was about four weeks (F4); LexisNexis four months total (F5);
  Grafana's own IRM estimate is three to four weeks (F3). All are VENDOR CLAIM or ANECDOTE, and they
  measure different things (incident tooling versus metrics). My reading: weeks, not days, and long
  enough to contain at least one real incident, one deploy cycle and one weekly/monthly pattern
  (UNVERIFIED inference).
- The overlap needs an end date set in advance, because "nobody set an end date" is the first
  failure cause in F1. Put the old vendor's contract end date in the plan before starting.
- Duplicate alert routing is part of the pattern. Prezi sent alerts to both on-call tools with an
  Alertmanager route using `continue: true` so the old path kept working (F4).
- Gotcha for our target: New Relic needs HA replica labels set on every replica or it stores
  duplicates (F20, VERIFIED). A dual-write that copies a two-replica Prometheus HA pair doubles the
  data and the bill unless deduplication is configured.
- Historical data is usually not copied. Elastic lists historical data copy as out of scope (F6).
  Plan for dashboards that look empty for the first weeks, and keep the old system read-only for
  history until retention is no longer needed.

### 2.4 Parity testing: how teams prove that alerts and dashboards match

What the sources actually describe (none is a rigorous method):

- Exit criteria written before any data flows (F1): "every alert that fired on the old vendor in the
  window also fired on the new one", at least one real incident resolved from the new tool, top ten
  dashboards match old numbers "within a tolerance you chose". Test with a noisy incident, not a
  quiet week (F1).
- Baselines first (F19): record current numbers before the change so you can say "slower or not".
- Side-by-side walkthrough of agreed dashboards and a `--validate` re-run after ingest (F6).
- Import "safely" by leaving originals intact, then compare (F2: originals remain, "test and
  validate your import process before migrating all alert rules").
- Shadow alerts (create the new alert with notifications routed nowhere or to a low-urgency channel
  and compare firing history) is the natural extension of "alert routing moves last" in F1; no
  source I read gives a worked method. UNVERIFIED as a documented industry standard; it is what the
  exit criteria in F1 imply.
- Golden-signal comparison: compare request rate, error rate, latency percentiles and saturation for
  the same service and window in both systems. This is implied by F11/F13, not described as a
  migration procedure anywhere I read.

What parity cannot mean (UNVERIFIED but follows from section 4): equal values to the digit. Windows,
extrapolation, offsets and scrape alignment differ. Compare breach intervals and incident counts, and
agree a tolerance.

### 2.5 Alert migration order

- Alert routing moves first only after new alerts have matched for a full window; dashboards second;
  old vendor off last (F1). Prezi disabled forwarding only after every team confirmed (F4).
- Alert on symptoms; keep pages to what a human can act on (F13, F11 "precision"). So a migration
  is a chance to demote cause-based pages to tickets (F1 "rebuild noisy alerts").
- Tiers (UNVERIFIED as a documented migration rule, standard practice): move ticket-level and
  informational alerts first (low blast radius if wrong), pageable symptom alerts after parity is
  shown, and keep the old pager path alive until the new one has fired in anger.
- Import notifications before rules (F2: "Import your notification configuration first").
- Grafana's own Prometheus-rule importer shows what a careful conversion carries over: query offset,
  no-data-as-normal, resolve-after-one-missing-evaluation, group labels, sequential evaluation (F2).
  Those are exactly the semantic items our converter must set explicitly (section 4).

### 2.6 Cost and volume control

- Build a TCO worksheet from the last three invoices, including small lines, overlap cost, egress
  and ops hours; rerun with the peak month, not the average; compute break-even (F1, VENDOR CLAIM but
  arithmetic is sound).
- Meters differ: Datadog custom metrics per tag combination, Grafana Cloud active series (scrape
  interval multiplies series), New Relic ingest GB (F1 for the first two, VENDOR CLAIM including an
  unverified "about 4x at 15 s versus 60 s"; NR pricing in the field notes section 4.3).
- Do not ship what nobody queries (F21). Drop at the edge with remote-write relabeling (F20) or in
  the Collector. Cut cardinality before cutting retention.
- Sampling for traces: tail sampling to keep errors and slow traces (F22).
- Beware savings claims: 50 percent (F5), 50 percent (F8) are marketing numbers with no method
  published.

### 2.7 Cutover and rollback

- Criteria-based cutover, in order: alert routing, dashboards, vendor off (F1). Put the date in the
  contract before the run starts (F1).
- Rollback is "keep the old path alive until the end date" (F1, F4, F6). Reversible because nothing
  on the source side was changed. This is where our "create-only, disabled, private" design is
  aligned with practice.
- Train on-call on the new query language and alert flow with real incidents, not slides (F1);
  office hours and an onboarding guide (F3, F4).

### 2.8 People and process

- Ownership: map teams to the services they own and set SSO and access before cutover; "the first
  question after a cutover is who owns the alert that just fired" (F1).
- Buy-in: send each owner the list of what will move and let them strike what they do not use; let
  them replay their last incident in both tools (F1).
- Runbook links: Prometheus practice says alerts should link to consoles (F13). Keep `runbook_url`
  and annotations through conversion (field notes cases on annotations).

### 2.9 Typical failure stories and what they cost

Honest status: I found lists of failure causes (F1) but no detailed named post-mortem of a failed
provider-to-provider migration in the sources I could read. The causes listed:

| failure | source | cost (as claimed) |
|---|---|---|
| Overlap with no end date, two bills | F1 | the modelled saving disappears (VENDOR CLAIM, no figure) |
| Copying dashboards nobody uses, then maintaining them | F1 | ongoing toil |
| Metric names, label conventions, query semantics differ; discovered late | F1; field notes | rework; wrong or silent alerts |
| Validation on a quiet week | F1 | fails on first real incident |
| Owners not told | F1 | they "route around it" |
| Morale drops while the old vendor keeps billing | one-line Hacker News comment under a Datadog-to-Grafana post (https://news.ycombinator.com/item?id=48910300) | PRACTITIONER ANECDOTE, no detail |
| Lift-and-shift copies old problems | F1 | but F4 did it deliberately and finished in four weeks |

## 3. Dashboards and alerts: rebuild or convert

### 3.1 Dashboards

- Where converters exist they classify rather than promise: Elastic's tool marks "Unsupported
  translations ... for manual review" (F7) and its service sorts panels into assisted and outside,
  capped at 15 assisted panels (F6). The honest ceiling is "some panels convert, the rest need a human".
- The "60 to 80 percent at best" figure: no public source found (UNVERIFIED). Our own count is
  lower by widget: the HANDOFF reports 56 percent of dashboards migrated by count of created
  dashboards versus about 43 percent by widget in the sandbox (that is our measurement on test data,
  not a customer result; the owner has not yet decided whether to weight by widgets).
- Vendors recommend testing "your ten most important dashboards in a trial" before estimating effort,
  because that is where unsupported widgets and query functions show up (F1).
- Practice that recurs: rebuild the top dashboards around a few SLO / golden-signal views and retire
  the long tail (F1 "retire dashboards that duplicate each other"; Dell moved to persona-based
  dashboards and plans SLO-based alerting, from a Grafana-hosted case study, VENDOR CLAIM). Use
  templates and variables so one dashboard serves many services (F5 reusable alerts and dashboards
  for all teams, VENDOR CLAIM).
- Lint before converting: dashboard JSON with duplicate ids, missing datasources and aggressive
  refresh rates converts badly (F23, VERIFIED tool; effect on conversion UNVERIFIED).
- Option to avoid dashboard migration at all: keep Grafana as the visual layer and read New Relic as
  a Prometheus data source (F20, VERIFIED). Alerts still need conversion.

### 3.2 Alerts

- Rewrite versus convert: F1 says rebuild noisy ones, convert the rest; F2 shows that a faithful
  conversion needs explicit settings to preserve no-data and resolve behaviour. A converter that
  does not set these produces an alert that looks identical and behaves differently.
- Threshold re-tuning: the SRE Workbook's four parameters (precision, recall, detection time, reset
  time) are the vocabulary for judging a rewritten alert (F11). A multiwindow burn-rate alert
  (for example 14.4 over 1 hour with a short confirming window) replaces many static thresholds on
  one SLI (F11, VERIFIED). Whether New Relic service levels and their burn alerts match this
  exactly is covered in the platform notes, not re-verified here.
- Noise: measure before and after. Count pages per week per service in the old tool; the new tool
  should not exceed it without reason. No neutral benchmark found.

## 4. What changes semantically between systems, and the tests people run

Each row: the difference, the source, the test.

| difference | source | validation test |
|---|---|---|
| `rate`/`increase` extrapolate to the edges of the range, so a value rarely equals a plain difference of two samples | F15 VERIFIED | compare over a window with a known increment; expect near, not equal, values |
| Only rate-like functions are safe on counters; sum-then-rate spikes at restarts | F14 VERIFIED | restart a test target; check no spike appears in the converted query |
| Counter versus gauge typing: after remote write, the type decides which functions are valid in the target | field notes cases 1, 2, S49 to S52 | `grafana-prom-hints` reads the type; compare converted result with the source on the same window |
| Staleness: Prometheus marks vanished series stale; a missing series then ends the alert instance. New Relic needs loss-of-signal or gap filling to mimic it, and loss of signal only works after a signal has existed | F15, F17 VERIFIED | stop a test series; time how long until the alert closes in each system |
| No data versus error are separate states in Grafana, with a pending period; Prometheus rules simply produce no alert; Grafana's importer adds an expression to keep No Data as Normal | F16, F2 VERIFIED | return an empty result on purpose; expect Normal in the source |
| Query offset / evaluation delay: Grafana imports with a 1 minute offset; New Relic aggregates after a delay (our measured incident about 5 minutes after the first breaching sample for a 60 s window and 2 minute duration, repo runbook); New Relic allows an evaluation delay up to 120 minutes | F2, F17 VERIFIED; repo runbook | firing test: measure time from first breaching sample to incident in each system and write the difference down |
| `for:` / pending period versus consecutive-windows thresholds | F2, F17; field notes | a breach shorter than the duration must not page in either |
| Sequential versus unordered rule evaluation inside a group (matters for recording-rule dependencies) | F2 VERIFIED | list rules that read a recorded series produced in the same group |
| `COMPARE WITH` is incompatible with NRQL alerting; sliding windows exist | F17 VERIFIED | reject or rewrite as baseline/anomaly condition |
| Attribute renames under OpenTelemetry semantic-convention versions change group-by keys | F10 VERIFIED | compare attribute sets on a sample of series before and after |
| Duplicate series from HA replicas without deduplication labels | F20 VERIFIED | compare series count per metric in both stores |
| Percentiles: histogram buckets and quantile estimation differ from pre-aggregated percentiles | field notes (S53, S54); UNVERIFIED in detail | compare p99 from the same traffic in both; expect a tolerance, record it |

The common method behind all of these is replay with known inputs: a controlled series, a fixed
window, then compare outputs. Teams that do it describe doing it by hand per rule (F1, F6); I found
no open tool that automates it across a whole estate.

## 5. Checklists

### 5.1 Practitioner checklist (merged from F1 to F6, F9 to F11, F19)

Before:
- [ ] Triggers named and tied to a bill line or incident gap; TCO worksheet from three invoices with
      overlap, egress, ops hours; rerun at peak month; break-even date computed.
- [ ] Inventory used (last 90 days) dashboards, alerts, saved views, metrics; unused ones listed to skip.
- [ ] Owner named for each dashboard and alert; notification routes and on-call schedules exported.
- [ ] Labels normalised (`service`, `env`, `team`); high-cardinality labels reviewed; metric type known.
- [ ] Baseline numbers recorded (request rate, error rate, latency, page counts per week).
- [ ] Written exit criteria: alerts that fired before also fire now; dashboards within a stated
      tolerance; one real incident handled in the new tool; on-call trained.
- [ ] End date for the overlap, and the old contract date, written down.
Run:
- [ ] Dual-write on (second remote_write / exporter / agent endpoint); HA deduplication set.
- [ ] Notifications imported first, tested with a throwaway receiver.
- [ ] New alerts created disabled or silent; firing test per alert class; timing difference recorded.
- [ ] Top ten dashboards compared side by side; differences explained, not ignored.
- [ ] Noisy alerts rebuilt or retired, not copied.
Cutover:
- [ ] Alert routing moves to the new path, old path left running until all owners confirm.
- [ ] Dashboards redirected; old links redirected or documented.
- [ ] Old vendor turned off last; history kept or exported as needed; access revoked.
After:
- [ ] Series and GB per day against the estimate; cardinality offenders found.
- [ ] Pages per week compared with baseline; alerts that never fire reviewed.

### 5.2 What a "migration-ready" report from our tool should answer (derived)

For every alert: converted? tested live? parity shown on date X? who owns it? pages or tickets? what
changes at no-data? For every dashboard: used in last 30 days? widgets converted by count? variables
survive? For the estate: expected ingest, expected overlap cost, cutover order.

## 6. Gap analysis: best practice versus this repository

Effort: S under a day, M a few days, L a week or more. Value: expected benefit for a real customer
migration (H/M/L), my judgement (UNVERIFIED as a measured quantity).

Repository evidence I checked: `README.md` support table and Grafana prerequisites; `docs/user/runbook-grafana.md`
sections 3 to 8; `docs/what-migrates.md`; `importer --help` in this worktree (command list below);
HANDOFF.md end-of-day 2026-11-10 sections.

| # | best practice | what the repo does today (file or command) | missing | effort | value |
|---|---|---|---|---|---|
| 1 | Inventory used versus unused dashboards | `triage-grafana-dashboards --usage usage.csv` ranks migrate now / later / quickstart / skip (runbook step 8); `plan-grafana-quickstarts` | Same for alerts (last fired, never fired, no notification); metric-level usage (mimirtool-style) for all sources; no triage for Dynatrace/GCP/CloudWatch | M | H |
| 2 | Name an owner, keep runbook links | Runbook URL handling in Grafana alert conversion is mentioned in the field notes (UNVERIFIED here whether it is carried through); no owner model | Owner field in IR and report; list of ownerless items | S | M |
| 3 | Label hygiene before moving | Nothing; `grafana-prom-hints` reads metric types and series counts | Preflight listing inconsistent `service`/`env`/`team` spellings across rules and dashboards | M | M |
| 4 | OpenTelemetry-first / agent coexistence | `plan-grafana-datapath` writes guidance, `alloy-dual-write.alloy` and `alloy-otlp.alloy` snippets (runbook section 5); `ingest-setup` and `ingest-logs-setup` (CloudWatch Terraform); `gcp-link-terraform`; `plan-dynatrace-reinstrumentation` | No Datadog-agent or Prometheus-HA-aware plan; no semantic-convention rename check | M | M |
| 5 | Dual-write / parallel run with end date | Alloy dual-write snippet only; `rollback`, `rollback-run` for what we created | Run-length planner, overlap cost, end-date tracking, HA dedupe check, Alertmanager `continue: true` duplicate-routing recipe | M | H |
| 6 | Parity testing | CloudWatch only: `importer validate` compares converted NRQL with CloudWatch numbers over a window; Grafana/GCP/Dynatrace: `check-*-data` only checks that data arrives (exit 0/2); manual firing test script `scripts/live-test/grafana-sandbox/alert-fire/fire.py` | Automated source-versus-target comparison for Grafana, GCP, Dynatrace; breach-interval comparison; dashboard widget comparison; "verified on date" label (this is HANDOFF "Next up" item 1) | L | H |
| 7 | Shadow alerts / incident reconciliation | Conditions are created disabled; no comparison of firing history | Reconciler: source alert state history versus `NrAiIncident` over the same window | L | H |
| 8 | Alert tiering and order | Everything created disabled; manual enablement ("Enable conditions one by one", runbook section 6); `--incident-preference` option exists | Tier assignment (page/ticket/info) from severity and routing; per-tier enablement plan | M | M |
| 9 | Semantic differences | Notes per item ("Migrated, review", `docs/what-migrates.md`); no-data approximations documented; census docs | A structured per-rule semantic-risk list (no-data, offset, window, counter type, `for`) with a test case for each (field notes rank 5) | S to M | H |
| 10 | Dashboard rebuild versus convert | `convert-grafana-dashboards`, `plan-grafana-dashboards` with tiers, page splitting, variables; `plan-remediation` playbooks | Headline percent weighted by widgets (open owner decision); no SLO/golden-signal rebuild suggestion; no dashboard lint pre-step | M | M |
| 11 | SLO-first alerting | `plan-service-levels` for Dynatrace SLOs only | Detect RED/golden-signal panels and ratio alerts; propose service levels and burn-rate alerts | L | M to H |
| 12 | Cost and volume control | `grafana-prom-hints` (series counts), `plan-grafana-datapath` (region, sender name), field notes rank 7 not built | Ingest estimator (series x scrape interval x bytes) per metric, allow-list generation, cost versus current vendor | M | H |
| 13 | Cutover and rollback plan | Rollback of created objects (`rollback`, `rollback-run`, `runs`); sandbox strictness `ckg-`; menu | A cutover checklist/plan with exit criteria, date, order, and a rollback section for the source side routing | S | H |
| 14 | People and process | Runbooks per source (`docs/user/`), troubleshooting | Training/onboarding material generation; owner confirmation tracking | S (docs) | L to M |
| 15 | Notification import before rules | `convert-grafana-notifications`, `apply-notifications`; offline plans, notifications not tested live (README) | Live proof; ordering enforced in `migrate.sh` menu (UNVERIFIED whether enforced) | M | H |
| 16 | Historical data | Not covered; 48-hour history seeding is HANDOFF item 4 | Seed or archive export | M | L to M |
| 17 | Validation on a real incident | None | Replay of a past incident window through both systems (needs source data access) | L | M |

## 7. Ten most valuable next capabilities (ranked)

Ranking = value for a real customer migration divided by effort, weighted by how many sources of
section 2 call it essential. Each has a one-paragraph design.

1. **Parity verifier (Grafana first).** For each converted alert rule, take the original PromQL
   from the extract, run it through the Prometheus-compatible query API of the source over a window,
   run the converted NRQL through NerdGraph over the same window, resample both to a common step, and
   compare breach intervals (when the condition would have been true) rather than raw values. Output
   per rule: number of breach intervals in each, overlap ratio, time offset, and a verdict
   (match / explainable difference / mismatch) with the semantic reason from section 4 attached when
   known. For dashboards, do the same per widget on a handful of series. Write "verified on <date>
   against <window>" into the manifest and report. Read-only on both sides. This generalises
   `importer validate` (CloudWatch only) and is HANDOFF "Next up" item 1.
2. **Cutover and exit-criteria generator.** From a run's manifest, write a plan document: written
   exit criteria (templates taken from F1: alerts that fired in the window also fired; tolerance for
   dashboards; one real incident handled), dated overlap end, enablement order by tier, who confirms
   each owner, rollback steps for source-side routing, and the old-vendor end date. Offline, no
   credentials. Mostly templates filled from facts we already have (counts, tiers, notification
   plans).
3. **Semantic-risk report per rule.** Make the section 4 table machine-checked: for every rule
   record no-data behaviour, offset, window, `for`/duration, counter versus gauge (from
   `grafana-prom-hints`), `increase`/`rate` extrapolation, `absent()` floors, comparison-in-query,
   and write a one-line expected behaviour difference plus a test recipe. Field notes rank 5; it is
   cheap because most facts are already in the IR.
4. **Dual-write planner with overlap model.** Extend `plan-grafana-datapath` to take the count of
   Prometheus replicas, scrape interval and series counts, and output remote-write config with HA
   deduplication labels (F20), a relabel allow-list, an Alertmanager duplicate-route recipe
   (`continue: true`, F4), the expected daily GB and the overlap bill for a stated number of weeks,
   and an end date.
5. **Ingest volume and cost estimator.** From series counts per metric (already read by
   `grafana-prom-hints`), scrape interval and retention, estimate samples per day and GB per day,
   mark metrics unused by any dashboard or rule (mimirtool-style analysis, F21) as candidates to
   drop, and compare with a user-entered current monthly bill. State the bytes-per-sample
   assumption explicitly and let the user override it; do not quote New Relic prices as fact.
6. **Alert triage (usage-driven).** Mirror `triage-grafana-dashboards` for alert rules: last
   firing time and fire count from Grafana state history, has a notification route, has an owner,
   has a runbook. Output migrate now / migrate as ticket / skip / rebuild (fires often, never acted).
   Needs read access to state history (UNVERIFIED which endpoint and permission).
7. **Shadow-alert reconciler.** After conditions are enabled with a silent or low-urgency workflow,
   fetch source alert state history and `NrAiIncident` for the same period, line them up by rule
   name and time, and report missed, extra and late incidents with the lag distribution. This is the
   production-time counterpart of the parity verifier and gives the "every alert that fired before
   also fired now" criterion of F1 as data.
8. **Tier assignment and staged enablement.** Classify each converted condition as page, ticket or
   info from source severity and routing, and generate an enablement order file the apply step can
   consume, so the first wave is the lowest-risk tier and the pager tier waits for parity evidence.
   Fits the existing disabled-by-default rule.
9. **SLO-first rebuild suggestions.** Detect request-rate, error-ratio and latency panels and ratio
   alerts for the same service, and propose a New Relic service level plus multiwindow burn-rate
   alerts (F11) as a replacement for several static-threshold alerts, labelled as a suggestion
   with the original items listed. Never auto-create; show the deleted-alert count as the benefit.
10. **Label and ownership preflight.** Scan rules and dashboards for label keys, report near-duplicate
    spellings (`service`, `service_name`, `app`), missing `team`/`owner`, and variables that depend on
    them; emit a relabel snippet for the ingest side. Prevents the broken-variable failure of F1.

## 8. What practitioners say tools like ours get wrong

Be careful with provenance: the claims below come from vendor playbooks (F1, F6), the tool authors'
own warnings (F2, F7) and my reading of the field notes. I found no first-hand rant from a user of a
converter (see limits in section 0). Treat as hypotheses to test with customers.

1. **They count what converted, not what is equivalent.** Headline percentages that count a
   dashboard as migrated when it can be created hide widgets that became notes. Our own number
   shows the gap (56 percent versus about 43 percent by widget, HANDOFF). Elastic's approach of
   labelling panels assisted / outside is the honest form (F6, F7).
2. **They copy the estate.** F1 names lift and shift of unused dashboards and noisy alerts as a
   top cause of stalled migrations. A converter that migrates everything by default encourages it.
   Triage is the antidote; ours exists for Grafana dashboards only (gap rows 1, 6 in section 6).
3. **They stop at "it parses".** A converted alert that compiles but fires at a different time or not
   at all is worse than a manual one because it is trusted. Grafana's own importer documents the
   settings needed to keep behaviour (F2); the repo's only live proof is one firing test (about
   +5 minutes later than Grafana, README/HANDOFF) and nothing proves notifications.
4. **No parity evidence.** Practitioners ask for "every alert that fired also fired" (F1). Our
   tool outputs "Migrated, review", never "verified". Until the parity verifier exists we cannot
   claim more.
5. **They ignore the data path.** A converted rule without the right metric type, labels and
   deduplication gives a blind condition (field notes; F20). We have `check-*-data`, but only for
   existence.
6. **They ignore people.** Ownership, training, notification routes, who is on call, and the
   old-vendor end date decide whether a migration finishes (F1, F3, F4). Our tool produces no
   owner or training output.
7. **They are single-shot.** Real migrations run for weeks with dual-write and staged cutover (F1,
   F4, F9); a tool that only does "convert, create, enable" has no model of time (no overlap end
   date, no re-run diff, no enablement waves). Rollback of our own objects exists; the plan around
   it does not.
8. **They quote savings.** Vendor material makes cost claims with no method (F5, F8). A tool should
   show its assumptions and let the user paste their own bill. Do not copy these numbers into our
   docs.
9. **They treat all sources alike.** Datadog and Dynatrace differ in what is exportable; the
   neutral documentation I could read was Grafana- and Prometheus-centred. Our Dynatrace support is
   the least proven (README). Do not extend Grafana-derived claims to it without checks.

## 9. What to read next (needs a browser or a human)

- Reddit r/sre, r/devops, r/observability threads on leaving Datadog, New Relic, Splunk (search
  "migrated from Datadog", "dual write", "parity").
- KubeCon / SREcon / Monitorama talks on moving metrics or alerting systems (search the CNCF
  YouTube channel and USENIX SREcon archive for "migration" and "alerting").
- GitHub issues of `grafana/dashboard-linter`, `elastic/observability-migration-platform`,
  `perses/perses` (`percli migrate`), Datadog-to-Grafana converters (for example `ncandio/graang`,
  `paul97ashish/datadog-to-grafana-migrator`: found by search, not opened), and `grafana/mimir`
  mimirtool issues. Field notes already list some (S1 to S7).
- Datadog monitor documentation on evaluation delay and no-data options; Splunk Observability and
  Chronosphere migration pages (blocked for me).
- Ask three customers how long their last migration ran in parallel and what they compared.

## 10. UNVERIFIED list

- That tiered enablement (ticket tier first, pager tier after parity) is documented industry
  practice. It is a consequence of F1's ordering plus common sense.
- That shadow alerts are a common published method; F1 implies the idea but gives no procedure.
- Any dual-run length in days; sources give weeks and months with no method.
- The "60 to 80 percent" dashboard conversion figure.
- The "about 4x at 15 s versus 60 s" Grafana Cloud scrape-interval cost figure quoted in F1.
- Which Grafana endpoint and permission expose alert state history and dashboard usage.
- Cost claims of 50 percent (F5, F8).
- Whether our notification import order is enforced in the menu.
- The effort and value ratings in sections 6 and 7 are my estimates.
