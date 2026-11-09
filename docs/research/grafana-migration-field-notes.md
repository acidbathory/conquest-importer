# Grafana migration field notes: what others report, what tools exist, what to test next

Read on 2026-11-09. Scope: field knowledge about moving Grafana dashboards, alerts and data to other
observability platforms, and to New Relic in particular, from public articles, forum threads, issue
trackers, vendor documentation and existing converter tools. This file adds to
`docs/research/grafana-dashboards.md`, `docs/research/grafana-notifications-datapath.md` and the
Grafana sections of `docs/what-migrates.md`; it does not repeat them.

## 0. How to read this file

Evidence keys, as in the researcher rules:

- DOCS: vendor documentation page, read on the date given (all pages were fetched on 2026-11-09
  unless a publication date is shown).
- ARTICLE: blog post or article. Reliability is noted per source.
- THREAD: forum, Hacker News or issue-tracker discussion.
- CODE: a repository README, source file or issue list that I read.
- REPO: this repository (file given).
- UNVERIFIED: from memory, inferred, or read only in part. Never build on these without a check.

Source ids (S1 to S62) are defined in section 1. A claim carries the ids it rests on. "Count" means
the number of distinct source ids in my sample that show the problem; the sample is not random (see
section 7), so counts rank themes, they do not measure how common a problem is.

Method limits that matter:

- Reddit: both `https://www.reddit.com/search.json` and `https://old.reddit.com/search` answered HTTP 403
  with a normal User-Agent. Not retried or bypassed. No Reddit content is used.
- DuckDuckGo HTML search returned a bot challenge. Not bypassed. I could not use a general web search,
  so discovery came from the GitHub, Hacker News, Stack Exchange, Grafana community and dev.to APIs,
  vendor sitemaps and the pages those pointed at.
- GitHub search API allows about ten unauthenticated calls a minute and 60 calls an hour; it was
  rate limited (HTTP 403) several times. Where I needed issue titles I read the public issue list
  HTML pages instead.
- New Relic Forum (`forum.newrelic.com`) needs a login (HTTP 401 on `search.json`), and
  `discuss.newrelic.com` returned a JavaScript shell with no search results. No New Relic forum
  content is used. This is the largest gap for the New Relic specific findings.
- Splunk documentation: `docs.splunk.com` sitemap returned 403 and a page request timed out after
  about three minutes. Splunk Observability was not assessed.

## 1. Source log

Order: tools and repositories, then vendor documentation, then articles and threads, then discovery
listings. Reliability notes are short: "primary" means the text is written by the project or vendor
that owns the thing described.

### 1.1 Converter tools and their repositories

| id | title and URL | date | covers | reliability |
|---|---|---|---|---|
| S1 | elastic/observability-migration-platform README, https://github.com/elastic/observability-migration-platform | read 2026-11-09 (CI badges current) | `obs-migrate` / `grafana-migrate` / `datadog-migrate` to Kibana; license Elastic-2.0 (README badge and LICENSE file) | primary, vendor project |
| S2 | same repo, `docs/sources/grafana.md` (Grafana Source Adapter), https://github.com/elastic/observability-migration-platform/blob/main/docs/sources/grafana.md | 2026-11-09 | the most detailed public list of Grafana-to-other-backend edge cases I found (about 1,600 lines); read the overview, extraction scope, grafana-specific notes, current boundaries, time state and variables sections | primary; written as engineering notes, high detail |
| S3 | elastic/dashboard-alert-migration README (Instruqt workshop around S1), https://github.com/elastic/dashboard-alert-migration | 2026-11-09 | how the engine is used in a lab: 20 Grafana dashboards, 2 alert rules, OTLP via Alloy; known gap on `counter_long` aggregations | primary, workshop material |
| S4 | issue list of S1, https://github.com/elastic/observability-migration-platform/issues | 2026-11-09 | about 25 open or recent issue titles: bare label names on non-Prometheus targets, `count(A or B)`, PromQL comments, vector matching, gauge versus counter warnings, axis units and legend labels | primary; titles only, I did not open bodies |
| S5 | perses/perses `docs/migration.md`, https://github.com/perses/perses/blob/main/docs/migration.md | 2026-11-09 | `percli migrate`: scope, version range, variable regex behaviour | primary |
| S6 | perses/perses issue list filtered on "migrate grafana", https://github.com/perses/perses/issues?q=is%3Aissue+migrate+grafana | 2026-11-09 | about 25 titles: row repeat, column overrides, dashboard links, annotations, regex filter for variables, math expressions, tag naming | primary; titles only |
| S7 | RomaLoginovskiy/grafana-to-coralogix-migrator README, https://github.com/RomaLoginovskiy/grafana-to-coralogix-migrator | 2026-11-09 | .NET 9 CLI, MIT; assess, convert, migrate, verify, backup, grafana-import; panel and query tables | primary, single-author project, small (2 stars at search time) |
| S8 | damoke012/grafana-dynatrace-migration README, https://github.com/damoke012/grafana-dynatrace-migration | 2026-11-09 | field toolkit: export, inventory, metric audit "REWRAP vs REMAP", PromQL to DQL by hand, `monaco` deploy, sanitiser | primary, one person, no license shown in README |
| S9 | Chronosphere (now Palo Alto Networks Cortex XCOR) "Import Grafana dashboards", https://docs-xcor.paloaltonetworks.com/observe/dashboards/classic-dashboards/grafana-import | 2026-11-09 | `chronoctl convert grafana`, Terraform inline JSON, `ImportDashboardFromClassic`; Grafana version limit | primary vendor docs |
| S10 | SigNoz "Migrate from Grafana (LGTM Stack)", https://signoz.io/docs/migration/migrate-from-grafana/ | 2025-11-30 | overview of metrics, traces, logs, dashboards, alerts migration | primary vendor docs |
| S11 | SigNoz "Migrate Dashboards from LGTM Stack", https://signoz.io/docs/migration/migrate-from-grafana/dashboards/ | 2025-11-30 | no converter; templates; panel type parity table | primary |
| S12 | SigNoz "Migrate Alerts from LGTM Stack", https://signoz.io/docs/migration/migrate-from-grafana/alerts/ | 2025-11-30 | channel parity table, rule type mapping, recording rules unsupported | primary |
| S13 | Last9 "Migrating from alertmanager", https://last9.io/docs/migrating-from-alertmanager/ | 2026-11-09 | HTTP endpoint that converts Prometheus alert rule YAML into Last9 entities | primary |
| S14 | Last9 "Observability platform migration" guide, https://last9.io/blog/observability-platform-migration/ | 2026-11-09 | TCO, dual-shipping, exit criteria, lift-and-shift warning, "clean up labels first" | vendor marketing with sound checklist; treat cost claims as vendor claims |
| S15 | Last9 changelog "Import dashboards from embedded Grafana", https://last9.io/changelog/import-dashboards-from-embedded-grafana-to-last9/ | 2025-04-29 | Grafana dashboards imported into an embedded Grafana, then optional conversion | primary, short |
| S16 | oodle-ai/oodle-cli README, https://github.com/oodle-ai/oodle-cli | 2026-11-09 | `oodle grafana migrate` (dashboards, folders, data sources, alert rules) and `oodle newrelic export` (New Relic dashboards and alerts out); Apache-2.0 | primary |
| S17 | ncandio/graang README, https://github.com/ncandio/graang | 2026-11-09 | Datadog to Grafana dashboards (reverse direction); says queries are kept and need manual translation; MIT | primary, alpha, self-described |
| S18 | grafana/grizzly README, https://github.com/grafana/grizzly | 2026-11-09 | Grafana as code; marked for deprecation in favour of grafanactl; Apache-2.0 | primary |
| S19 | grafana/grafanactl README, https://github.com/grafana/grafanactl | 2026-11-09 | itself deprecated in favour of `gcx`, repository to be archived 2026-06-01 | primary |
| S20 | Grafana Mimir `mimirtool` docs, https://github.com/grafana/mimir/blob/main/docs/sources/mimir/manage/tools/mimirtool.md | 2026-11-09 | `analyze grafana/dashboard/rule-file/prometheus` (which metrics are used), `rules lint/prepare/sync`, `alertmanager load/get`; AGPLv3 | primary |
| S21 | greenszpila/promQLtoNRQL (README and `app.py`), https://github.com/greenszpila/promQLtoNRQL | pushed 2024-07-11 | tiny Flask page that posts PromQL to a New Relic "promql-gateway translate" endpoint and shows the NRQL; no license | primary, hobby code, not endorsed by New Relic as far as I can tell |
| S22 | dpacheconr/promql-nrql-comparison README, https://github.com/dpacheconr/promql-nrql-comparison | pushed 2025-12-15 | demo stack: Prometheus, Grafana with 11 dashboards, remote write to New Relic, multi-cluster NRQL example; "demo only" | primary, demo |
| S23 | newrelic/newrelic-grafana-plugin README and issue list, https://github.com/newrelic/newrelic-grafana-plugin | issues dated 2026-10 | Grafana data source plugin that runs NRQL; Grafana 10.4+; AGPL-3.0; two open issues (install instructions, EU auth) | primary vendor repo |
| S24 | VictoriaMetrics `vmalert-tool` docs, https://docs.victoriametrics.com/victoriametrics/vmalert-tool/ | 2026-11-09 | unit testing of alert and recording rules; no conversion | primary |
| S25 | VictoriaMetrics `vmctl` docs, https://docs.victoriametrics.com/victoriametrics/vmctl/ | 2026-11-09 | data migration modes (Prometheus, Thanos, Cortex, Mimir, InfluxDB, ...); no dashboard or rule conversion | primary |
| S26 | slok/sloth README, https://github.com/slok/sloth | 2026-11-09 | SLO spec to Prometheus recording and multi-window burn alert rules; Apache-2.0 | primary |
| S27 | pyrra-dev/pyrra README, https://github.com/pyrra-dev/pyrra | 2026-11-09 | SLO objects to Prometheus rules plus a UI; Apache-2.0 | primary |
| S28 | Honeycomb "Prometheus" integration page, https://docs.honeycomb.io/integrations/metrics/prometheus | 2026-11-09 | Prometheus data arrives through an OpenTelemetry Collector scrape; no Grafana import | primary |

### 1.2 Vendor documentation on the data path and query language (New Relic and Grafana)

| id | title and URL | date | covers | reliability |
|---|---|---|---|---|
| S50 | New Relic "Set up your Prometheus remote write integration", https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/ | 2026-11-09 | endpoint setup, `prometheus_server`, `newrelic_metric_type`, relabel keep/drop, data visible after about five minutes | primary |
| S51 | New Relic "Metric API limits and restricted attributes", https://docs.newrelic.com/docs/data-apis/ingest-apis/metric-api/metric-api-limits-restricted-attributes/ | 2026-11-09 | cardinality, attribute and payload limits, extra remote write limits | primary |
| S52 | New Relic "Migrate from the Prometheus OpenMetrics integration" (to the Prometheus agent), https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-prometheus-agent/migration-guide/ | 2026-11-09 | renamed labels between nri-prometheus and the agent, metric type inference, scrape defaults | primary |
| S53 | New Relic "Supported PromQL features", https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/supported-promql-features/ | 2026-11-09 | which PromQL functions the PromQL-style mode translates, limits (366 steps, 100 series), instant look-back | primary; the coverage figure in it is a vendor claim |
| S54 | New Relic "Translate PromQL queries to NRQL", https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql/ | 2026-11-09 | metric type mapping (counter becomes delta count), NRQL equivalents | primary |
| S55 | New Relic "OpenTelemetry OTLP" best practices, https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/ | 2026-11-09 | attribute limits, delta temporality preference | primary |
| S56 | New Relic "NRQL syntax, clauses and functions", https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/ | 2026-11-09 | searched for a query length limit; found only a 4096 character limit on one string function result, none on query text | primary; negative result |
| S57 | Grafana "No Data and Error states" (docs source), https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/nodata-and-error-states.md | 2026-11-09 | state model, `DatasourceNoData` / `DatasourceError` alerts, only for Grafana-managed rules | primary |
| S58 | Honeycomb, Datadog, Dynatrace, Coroot, Chronosphere sitemaps (listed individually in 2.2) | 2026-11-09 | negative evidence: no page named for a Grafana import | primary but indirect |

### 1.3 Articles and threads

| id | title and URL | date | covers | reliability |
|---|---|---|---|---|
| S29 | "I wrote a Datadog to Grafana monitor translator", dev.to, https://dev.to/kaushik94/i-wrote-a-datadog-to-grafana-monitor-translator-250f | 2026-09-29 | monitors translated to Grafana rules; evaluation semantics lost; PromQL comparison filtering; `for` versus no-data timeframe | personal article, self-reported tests; the PromQL and Grafana behaviours it describes match S32 and S57 |
| S30 | "We Ditched New Relic for Grafana 11.0 and Prometheus 3.0 and Saved $80k/Year", dev.to, https://dev.to/johalputt/we-ditched-new-relic-for-grafana-110-and-prometheus-30-and-saved-80kyear-a-2026-observability-2658 | 2026-05-02 | cost story, direction New Relic to Grafana | low: reads as generated, contains implausible claims (a 10 panel dashboard limit, a "native Prometheus 3.0 connector" for Grafana 11.0, a "New Relic StatsD integration" scrape latency comparison); used only as a warning that such claims circulate |
| S31 | "Prometheus Alertmanager vs Grafana Alerting (2026)", dev.to, https://dev.to/alexandrev/prometheus-alertmanager-vs-grafana-alerting-2026-architecture-features-and-when-to-use-each-48d7 | 2026-05-05 | two alerting systems in one estate, silences in two places, config in one YAML versus a database | medium; general explainer |
| S32 | "Why Your Grafana 5xx-Rate Alert Spams DatasourceNoData Every 4 Hours", dev.to, https://dev.to/anand_rathnas_d5b608cc3de/why-your-grafana-5xx-rate-alert-spams-datasourcenodata-every-4-hours-4fk9 | 2026-09-11 | 0/0 in PromQL returns an empty result, which Grafana turns into a no-data alert that carries the rule's own annotations | medium; single incident write-up, mechanism matches S57 |
| S33 | "Grafana 11 Provisions Alerts Before Dashboards, and That Breaks folderUid", dev.to, https://dev.to/anand_rathnas_d5b608cc3de/grafana-11-provisions-alerts-before-dashboards-and-that-breaks-folderuid-2bln | 2026-09-04 | provisioning order creates a folder before the dashboard provider, then fails | medium; log excerpts given |
| S34 | Grafana community: "Migrating New Relic Alerts to Grafana Labs", https://community.grafana.com/t/151553 | 2025-05-29 | one question, no answer in the thread: user can export New Relic alerts with Terraform, asks how to script them into Grafana | THREAD, unanswered |
| S35 | Grafana community: "How to properly migrate a dashboard", https://community.grafana.com/t/119942 | 2024-04-22 | after an upgrade the dashboard fails with "Datasource was not found"; the data source uid in the JSON had to be replaced | THREAD |
| S36 | Grafana community: "Migrating Multiple Grafana Instances into a Single Instance", https://community.grafana.com/t/127293 | 2024-07-29 | 20,000 dashboards and about 400 data sources per instance; uid collisions are the main risk | THREAD |
| S37 | Grafana community: "Change datasource in dashboard without messing up", https://community.grafana.com/t/71509 | 2022-08-31 | switching data source type resets the query part of every panel | THREAD |
| S38 | Grafana community: "(Datasource)NoData alerts stick around forever", https://community.grafana.com/t/160058 | 2025-12-09 | stale NoData alerts after the data source was down; reply says set `noDataState: OK` | THREAD |
| S39 | Grafana community: "Create dashboard Error via new Dashboard APIs", https://community.grafana.com/t/153855 | 2025-07-03 | the `/apis/dashboard.grafana.app` create call returns 422 for a plain classic JSON; needs `metadata` and `spec` wrapping and a schema version above 36 | THREAD |
| S40 | Grafana community: "BigQuery: DoiT International to native data source migration", https://community.grafana.com/t/163303 | 2026-06-15 | data source references live in four places (panels, query targets, variables, annotations); updating only the name is not enough; Angular plugin removed in Grafana 13 | THREAD, answers partly from a user, not Grafana staff |
| S41 | Grafana community: "Ghost alerts from a destroyed organization/dashboard", https://community.grafana.com/t/52122 | 2021-08-19 | legacy-to-unified alerting migration failed because a dashboard of a deleted org was missing | THREAD, old (Grafana 8.1) |
| S42 | Grafana community: "Multi Variable Chaining", https://community.grafana.com/t/122900 | 2024-05-30 | chained variables with multi-select expand into SQL conditions the author did not expect | THREAD |
| S43 | Grafana community: "Exporting and importing YAML dashboards", https://community.grafana.com/t/159660 | 2025-11-24 | Grafana 12.3 exports v2 as YAML but cannot import it; experimental | THREAD |
| S44 | Grafana community: "New warning about dynamic dashboards being unavailable", https://community.grafana.com/t/161936 | 2026-03-03 | Grafana 12.4: a v2 export did not import into another 12.4; library panels break during classic/v1 export-import; feature toggle workaround | THREAD |
| S45 | Grafana community: "Variables inside a library element don't change dynamically...", https://community.grafana.com/t/64419 | 2022-04-21 | library panel keeps the variable values of the dashboard it was made in | THREAD, old |
| S46 | Grafana community: "Grafana 8 - Export/Import alert rules - how to?", https://community.grafana.com/t/52505 | 2021-08-27 | no JSON export for alert rules at the time; the GET API response lacked fields | THREAD, old |
| S47 | Hacker News comments (eight): items 42660321, 44428684, 36496186, 45845895, 45951345, 23229723, 25399738, 23907092 on https://news.ycombinator.com/ , read through https://hn.algolia.com/api/v1/items/ID | 2020 to 2025 | experience of moving between Datadog, New Relic and Grafana stacks; PromQL compatibility claims | THREAD, anecdotal; mostly the reverse direction |
| S48 | newrelic/terraform-provider-newrelic issue 2818 "variables not getting imported when applying dashboard with JSON file" and issue list, https://github.com/newrelic/terraform-provider-newrelic/issues/2818 | 2025 | dashboard variables lost when a dashboard JSON export is applied through `newrelic_one_dashboard_json`; reply says use a newer provider; related titles on `ignore_time_range` and multi-account widgets | THREAD, primary tracker |
| S49 | newrelic/newrelic-prometheus-configurator issue 235, https://github.com/newrelic/newrelic-prometheus-configurator/issues/235 | undated in page text, Jira link in thread | customer with 300+ counters not ending in `_total`, stored as gauges; workaround is metric override rules | THREAD, primary tracker |
| S59 | grafana/grafana issue list filtered on "export alert rules migration", https://github.com/grafana/grafana/issues?q=is%3Aissue+export+alert+rules+migration | 2026-11-09 | titles: alert rules missing after upgrade with Git Sync, cannot import Grafana 10 rules into 12, legacy provisioned alerts not migrated, no way to import Prometheus rules into Grafana's Alertmanager | THREAD, titles only |

### 1.4 Discovery listings (read, but not counted as evidence of a claim)

| id | listing | use |
|---|---|---|
| S60 | GitHub repository search results for "grafana newrelic", "promql nrql", "grafana to datadog dashboard", "grafana dashboards migrate", and about twenty other queries (2026-11-09) | showed that no public Grafana-to-New-Relic dashboard or alert converter exists on GitHub by name or description; found S16, S21, S22, S23 |
| S61 | Hacker News Algolia searches (five queries), Stack Exchange search (two queries), Grafana community `search.json` (ten queries), dev.to API tag listings (about 15 pages) | candidate threads; Stack Overflow returned nothing relevant (one New Relic alerts in AWS Managed Grafana question, 75339357, not read in full) |
| S62 | dev.to titles seen but not opened: "Alerting as Code: Grafana rules, contact points, and Jenkins dry-runs" (2026-09-28), "Grafana as Code: Surviving Destroy-Recreate With Provisioning Files" (2026-09-18) | listed so a later pass can read them |

Count of distinct sources actually read: S1 to S57 and S59 are 58 documents or threads, 59 if the
negative-evidence aggregate S58 is counted. S60 to S62 are listings. The eight Hacker News comments
(S47) count as one row.

## 2. Existing tools

### 2.1 What each tool does

| tool | license, language | approach | dashboards | alerts and rules | queries | panel types | tests and checks | known limits |
|---|---|---|---|---|---|---|---|---|
| Perses `percli migrate` (S5, S6) | Apache-2.0, Go with CUE migration scripts supplied by each plugin | parse classic JSON into a Go struct, then each plugin's CUE script migrates its variables, panels and queries; `--online` fetches current plugin versions | yes | no (alerts, users stated out of scope) | per plugin; unsupported variables become a static list that spells "grafana migration not supported" | whatever plugins exist; best effort | open issues show regressions with CUE v0.17+ | Grafana 9.0 to 11.x only; Grafana 12 v2 resources rejected; variable `Regex` deliberately not migrated; open issues on row repeat, column overrides, dashboard links, annotations, math expressions |
| Elastic observability-migration-platform (S1, S2, S3, S4) | Elastic-2.0, Python 3.11+ | rule engine on parsed PromQL (`promql-parser`), native `PROMQL` command on Kibana when supported, ES\|QL translation otherwise, optional local-AI fallback; rule packs and plugins; preflight, verification packets, smoke validation | yes, 40+ panel types, variables to Kibana controls, links, time state | Grafana legacy and unified rules become disabled Kibana rules with no actions; LogQL alerts always manual | PromQL, LogQL, existing ES queries | 40+ with layout kept; some types skipped (`dashlist`) | CI badge, quality and license workflows; semantic gates and live ES\|QL validation optional | counter typing (`counter_long`) failures; element-wise arithmetic between different metric names needs a different path; subqueries, `count_values`, `__name__` introspection stay manual; API extraction caps 500 dashboards per search; basic auth or token |
| grafana-to-coralogix-migrator (S7) | MIT, C# .NET 9 | convert Grafana JSON to Coralogix custom dashboards; `assess` converts in memory and gives Clean, Degraded, Rejected or Failed per dashboard; checkpoint and resume; round-trip `verify`; Playwright comparison | yes | no | PromQL and Elasticsearch passed through; LogQL converted to Lucene; no DataPrime generation | five: time series, stat and gauge, table (as aggregated line), logs, text | tests in repo (not counted) | few panel types; Table becomes a line chart |
| `chronoctl convert grafana` (S9) | license not checked (Chronosphere CLI) | directory of Grafana JSON to Chronoctl YAML; or Terraform with inline JSON; or API `ImportDashboardFromClassic` with dry run | yes, as "classic" dashboards then optionally standard | no | PromQL is native in that product | not listed | not read | documented support for dashboards exported from Grafana up to 7.5; the Terraform provider ignores the dashboard `uid` |
| SigNoz (S10, S11, S12) | docs only | no converter: rebuild with templates and the query builder; raw PromQL often pastes in | no automation | manual, rule type and channel tables | PromQL pasted; LogQL rebuilt with the builder | parity table: gauge, node graph, state timeline, status history, candlestick, XY chart, geomap, canvas, news not supported | n/a | recording rules not supported (use scheduled queries); Discord, Telegram, VictorOps, WeChat, Webex, SNS channels absent |
| Last9 (S13, S14, S15) | SaaS | alertmanager YAML posted to an endpoint, response is Last9 entities with breach and threat indicators; Grafana dashboards import into an embedded Grafana, optional later conversion | via embedded Grafana | alertmanager rules only | PromQL kept | n/a | not read | the guide itself says to scope gaps with a trial of the ten most used dashboards |
| Oodle CLI (S16) | Apache-2.0, Go (language not checked) | exports from Grafana, uploads, imports; filters by tag; keeps non-Prometheus data sources with their original ids; Prometheus panels repointed to the vendor data source | yes | alert rules | Prometheus kept | not listed | `--skip-import` to review first | data source credentials re-entered by hand; a TLS verification fix was an open pull request in October 2026 (search listing S60) |
| Dynatrace toolkit by damoke012 (S8) | no license shown | shell scripts: export, inventory, metric audit that classifies each metric REWRAP or REMAP, then PromQL to DQL by hand, then `monaco` | assembly by hand | no | manual | n/a | a sanitiser that redacts hosts and emails before sharing inventories | personal toolkit, no automation of the translation step |
| Datadog, Honeycomb, Dynatrace, Coroot docs (S58, S28) | n/a | no page with "grafana" in the URL beyond integrations that monitor Grafana itself, from the sitemaps | no | no | Honeycomb takes Prometheus data via an OpenTelemetry Collector | n/a | n/a | UNVERIFIED that none exists: sitemap titles only, no site search |
| VictoriaMetrics `vmalert`, `vmalert-tool`, `vmctl` (S24, S25, S47) | open source, Go (UNVERIFIED for the licence text) | `vmalert` evaluates Prometheus-format rule files; `vmalert-tool` unit tests rules; `vmctl` moves data | no | native Prometheus rule files, no conversion needed | MetricsQL superset; a maintainer states drop-in PromQL compatibility on HN (vendor view) | n/a | `vmalert-tool` | not a converter |
| grizzly, grafanactl, gcx (S18, S19) | Apache-2.0 (grizzly), Go | Grafana to Grafana resources as code (dashboards, folders, data sources, library elements, contact points, policies, templates, rule groups) | yes | yes, Grafana-managed | none | n/a | n/a | both superseded in 2026 (grizzly to grafanactl, grafanactl to `gcx`): a moving target to build on |
| `grafana-terraform-exporter` | not read: `raw.githubusercontent.com/grafana/grafana-terraform-exporter/main/README.md` returned 404 and the issues page 404 | | | | | | | UNVERIFIED |
| mimirtool (S20) | AGPLv3, Go | `analyze grafana|dashboard|rule-file|prometheus` extract the metrics used by dashboards and rules and compare with what the Prometheus stores; `rules lint|prepare|sync`; `alertmanager load` | metric usage only | rule files and Alertmanager config for a Mimir | extracts metric names | reports `unsupported panel type: "news"` as a parse error | n/a | not a converter; a good idea to borrow (section 2.3) |
| Sloth, Pyrra (S26, S27) | Apache-2.0, Go | SLO spec to Prometheus recording and burn rate alert rules | Grafana dashboard for the SLOs | generated Prometheus rules | PromQL | n/a | n/a | not converters; relevant only because estates using them have rule names the importer must see as recording rules |
| promQLtoNRQL (S21) | no license, Python Flask | forwards PromQL to a New Relic endpoint and prints the `nrql` field | no | no | PromQL to NRQL through New Relic's own service | n/a | none | calls an endpoint (`promql-gateway.service.newrelic.com/api/v1/translate`) that I did not find in public documentation; UNVERIFIED that it is supported or stable |
| newrelic-grafana-plugin (S23) | AGPL-3.0, TypeScript | Grafana reads New Relic through NRQL | reverse: keeps Grafana UI | no | NRQL | Grafana panels | not read | two open issues; AGPL license affects redistribution |
| promql-nrql-comparison (S22) | none stated | demo stack with Prometheus remote write to New Relic and 11 Grafana dashboards | sample dashboards only | no | n/a | n/a | n/a | useful as a fixture source: Node Exporter Full, ArgoCD, Istio, cert-manager, External Secrets, kube-state-metrics v2 |

Not read and so not assessed: Splunk Observability dashboard import (blocked or timed out); Coroot
(the docs home was read, no Grafana import page appears in its sitemap); Honeycomb beyond the
Prometheus page.

Result for New Relic: I found no public tool that converts Grafana dashboards or alert rules into
New Relic. I found one that goes the other way and exports from New Relic (S16, `oodle newrelic
export`), a hobby page that wraps a New Relic translation service for single queries (S21), and a
New Relic plugin that lets Grafana read New Relic (S23). The Importer therefore has no direct
open-source peer for the Grafana-to-New-Relic direction.

### 2.2 Negative-evidence method (S58)

I downloaded the sitemaps of `docs.honeycomb.io` (599 pages), `docs.datadoghq.com` (English sitemap,
gzip), `docs.dynatrace.com` (1.4 MB), `docs.coroot.com` (122 pages), and the Chronosphere docs
(785 pages), and searched the URLs for "grafana", "migrat" and "promql". The only Grafana import page
was Chronosphere's (S9). This shows that no page is named for it; it does not prove that no
feature exists.

### 2.3 Ideas worth borrowing, by tool

- Per-dashboard verdict vocabulary Clean, Degraded, Rejected, Failed, a conversion-only `assess`
  command that never uploads, and checkpoint and resume for bulk runs (S7). The importer's
  `plan-grafana-dashboards` has tiers and reasons (REPO `docs/what-migrates.md`); the verdict word
  per dashboard and a resumable bulk apply are missing.
- Never drop silently: unsupported translations are marked for manual review (S1 README); the same
  rule is stated by the monitor-translator article, "a caveat, never a silent drop" (S29). The
  importer already follows it for alerts and dashboards.
- Repeat clones are never migrated; work from the repeat template, fan out only when the values are
  known, otherwise keep one template panel with a warning (S2). Grafana rebuilds clones on load.
- Strip PromQL `#` comments before classification, except inside string literals, because both
  translation paths collapse newlines and a comment swallows the rest of the expression (S2, S4
  issue title "PromQL comments truncate the emitted native query").
- Element-wise arithmetic between two different metric names is a trap on targets that include the
  metric name in the vector-matching key (S2). New Relic's NRQL has no vector matching at all; the
  importer's T3 rule ("same group-by labels") is the right conservative form.
- Comparison operators in PromQL are filters, not booleans: `sum(x == 1)` and `a > b` change the
  series set (S2, S29, S32). For alerts use query plus threshold, never the comparison inside the
  query.
- Grouping variables (`by ($var)`), literal-valued variables, variables in metric or label names,
  variables with "All" equal to `.*` each get an explicit outcome (convert, degrade with warning,
  not feasible) (S2). A variable that cannot be inlined must never leave a phantom column (S2).
- `timeShift` has no equivalent on the target and becomes a report warning, `timeFrom` becomes a
  per-panel time range (S2). The importer plan marks both manual.
- Perses documents exactly how the Grafana variable regex differs from a JavaScript one: Grafana
  anchors a regex written without slashes, uses only the first capture group, and applies it to
  values as the Grafana data source returned them (S5). Worth a fixture set.
- `mimirtool analyze grafana` plus `analyze prometheus` give "metrics used by dashboards and rules"
  versus "metrics stored" (S20). The same list is the right allow-list for remote write
  `write_relabel_configs` with `action: keep` (S50), which limits ingest to what the converted
  content reads.
- The Last9 guide's cutover order: alert routing first after alerts matched for a full window,
  dashboards second, old vendor last; exit criteria written before data flows; copy only what was
  used in the last 90 days; rebuild noisy alerts rather than copy them (S14).
- The Dynatrace toolkit classifies each metric REWRAP (same meaning, new name) or REMAP (different
  meaning) before touching dashboards (S8). The importer's name-mapping problem is the same split.
- SigNoz publishes a panel-type parity table and a channel parity table (S11, S12); the importer
  already has both in its research notes, which is the form customers will ask for.

## 3. Pain points and edge cases reported

Counts are the number of distinct source ids in my sample that show the problem. Ids that only
list a title (S4, S6, S59) count as one each.

| # | theme | count | sources | what people report |
|---|---|---|---|---|
| 1 | Query language not portable, or only portable with care | 8 | S2, S7, S9, S10, S12, S17, S21, S53 | engines either pass PromQL through (Coralogix, Chronosphere, SigNoz) or translate it (Elastic, New Relic); translating needs a long tail of rules |
| 2 | Counter, rate and delta semantics | 8 | S2, S3, S49, S50, S52, S53, S54, S55 | New Relic stores counters as deltas; rate functions on the wrong type give wrong answers; Elastic reports `counter_long` failures and gauge-rate warnings |
| 3 | Metric type inferred from the name | 4 | S49, S50, S52, S54 | counters without `_total`, `_count`, `_sum`, `_bucket` become gauges and the reverse; one customer has 300+ such metrics |
| 4 | Dashboard variables (chained, regex, multi-value, All) | 6 | S2, S5, S6, S14, S42, S45 | regex not carried over; chained values expand unexpectedly; labels not standardised break variables on the other side; library panel keeps old variable values |
| 5 | Alert evaluation semantics: no data, pending, recovery, evaluation delay | 7 | S2, S29, S32, S38, S41, S57, S59 | `for` is not a no-data timeframe; no equivalent of hysteresis, evaluation delay, new group delay; stale NoData alerts |
| 6 | Notification routing, channels, silences | 6 | S2, S12, S13, S14, S20, S31 | tree to destinations, channels with no equivalent, silences kept in two systems, notification policies not mapped to connectors |
| 7 | Dashboards and alerts as code (provisioning, Terraform, tool churn) | 7 | S9, S11, S12, S18, S19, S33, S48 | provisioning order bug, dashboard JSON applied through Terraform loses variables, official CLI replaced twice in a short time |
| 8 | Schema and version drift of the dashboard JSON | 5 | S5, S9, S39, S43, S44 | converters limited to Grafana 7.5 (S9) or 9 to 11 (S5); v2 export without import; wrapping rules for the `/apis` endpoint |
| 9 | Data source references: uid, name, type, `${DS_*}`, mixed | 6 | S2, S5, S16, S35, S37, S40 | references live in panels, targets, variables and annotations; changing type resets queries |
| 10 | Label and attribute naming differs by ingest path | 5 | S2, S3, S4, S14, S52 | nri-prometheus labels `namespaceName` versus agent `namespace`; OTel names versus Prometheus names; bare Prometheus labels on a non-Prometheus target return zero rows |
| 11 | Panel type coverage gaps | 4 | S2, S5, S7, S11 | state timeline, status history, geomap, canvas, node graph, XY, news have no counterpart; Coralogix converts only five |
| 12 | Cardinality, series and ingest limits | 4 | S14, S51, S53, S55 | per-account, per-metric-name and attribute count limits; 100 series and 366 steps on the PromQL-style API |
| 13 | Logs: LogQL conversion | 3 | S2, S7, S12 | LogQL to ES\|QL or Lucene; Elastic keeps LogQL alerts manual always |
| 14 | Recording rules | 3 | S12, S26, S27 | not supported in SigNoz; SLO tools generate dozens of recording rules per objective; dashboards that use them need the rule to keep running |
| 15 | Transformations, overrides, value mappings | 3 | S2, S6, S11 | transformations flagged as redesign; column overrides dropped |
| 16 | Folders, nested folders, uid collisions | 4 | S7, S16, S33, S36 | merging estates collides uids; folder uid pinning breaks provisioning |
| 17 | Library panels | 2 | S44, S45 | break during export and import in 12.4; variable scope surprise |
| 18 | Plugins and Angular | 3 | S2, S5, S40 | plugin panels need a counterpart; Angular support removed in Grafana 13 |
| 19 | Annotations | 3 | S2, S6, S11 | dropped or reduced to candidate events |
| 20 | Repeats (panel and row) | 2 | S2, S6 | clones must not be migrated; row repeat requests |
| 21 | Time shift and compare-to | 2 | S2, S47 | no equivalent on the target; DataDog users miss easy time shifting in Grafana |
| 22 | Units, axis settings, legends | 2 | S2, S4 | a curated-pack issue states axis units and legend labels are not checked |
| 23 | Mixed data source panels | 2 | S2, S16 | per-target resolution; non-Prometheus sources need their own adapter |
| 24 | Contact point and data source secrets | 3 | S8, S16, S7 | credentials are re-entered after migration or kept out of shared inventories |
| 25 | Extraction limits and TLS | 3 | S2, S7, S16 | 500 dashboards per search page; retry settings; certificate verification options |
| 26 | Empty-result semantics (0/0, comparison filter) | 2 | S29, S32 | an empty vector is "no data" not zero; alerts spam or never clear |
| 27 | Parity validation and dual running | 4 | S2, S7, S14, S29 | verification packets, round-trip verify, dual shipping, exit criteria, live test of the translator output |
| 28 | Alert rule groups and evaluation intervals | 2 | S29, S57 | evaluation interval and timeout (30 s default, 3 attempts) shape the Error state |
| 29 | Alertmanager inhibition | 0 in the sources that I read | | not found in a source; it is a known Alertmanager feature (UNVERIFIED as a pain point) |
| 30 | Jsonnet and Grafonnet input | 0 | | not found in any source I read; one repository titled "Migrate Grafana json Dashboard to jsonnet" appeared in search results only (S60) |

Notes on the table:

- Rows 1 and 2 dominate because the Elastic engine (S2) and New Relic's own pages (S52 to S54) are
  detailed. A sample of end-user forum threads, if the New Relic forum were open to me, would likely
  shift counts toward rows 4 and 5. I could not test that (see section 0).
- Rows 29 and 30 are zero because I did not find a source, not because the problem is absent.

## 4. New Relic specific findings

### 4.1 How customers get Prometheus data in

| path | facts | source |
|---|---|---|
| Prometheus remote write to the Metric API | per-account endpoint, license key; `prometheus_server` becomes an attribute on every data point and is also the header to filter by when Grafana reads New Relic; data usually visible after about five minutes; metric type inferred from the name suffix, overridden by a `newrelic_metric_type` label (`counter`, `gauge`, `summary`, case sensitive) which is stripped on receipt; `write_relabel_configs` with `keep` or `drop` limits what is sent | DOCS S50 |
| New Relic Prometheus agent (Prometheus in agent mode, `newrelic-prometheus-agent`, configurator chart) | replaces the older `nri-prometheus` (Prometheus OpenMetrics integration); sends by remote write, so the same name-based type inference applies; scrapes targets annotated `newrelic.io/scrape: "true"` by default; node metrics not scraped by default | DOCS S52 |
| OTLP | prefers delta temporality for counters and histograms; attribute limits (strings 4095 characters, keys 255, resource 64, scope 8, record 128 attributes) | DOCS S55 |
| Grafana Alloy or Prometheus dual write | covered in `docs/research/grafana-notifications-datapath.md` section 6.3 | REPO |
| Honeycomb comparison | OpenTelemetry Collector with the `prometheus` receiver and an OTLP exporter | DOCS S28 |
| New Relic as a data source for Grafana | NRQL plugin (AGPL-3.0, Grafana 10.4+) or the Prometheus-compatible API with `X-Query-Key` | CODE S23; Prometheus API URL list: REPO notifications research 6.5 |

### 4.2 Gotchas and limits

- Type inference by suffix is a two-way error. Counters without the suffixes land as gauges, and
  gauges with `total`, `count`, `sum` or `bucket` land as counters (DOCS S52). A customer reports
  300+ counters affected and a workaround through metric override rules (THREAD S49).
- Cumulative counters become deltas inside New Relic; the PromQL-style mode "does not handle these
  functions on the wrong data type" and gives different or incorrect answers (DOCS S53, S54).
  Consequence for the importer: a `rate()` panel is only correct if the type was inferred right.
- Label renames between ingest paths: `namespaceName` versus `namespace`, `podName` versus `pod`,
  `clusterName` versus `cluster_name`, `scrapedTargetKind` versus `job`, `scrapedTargetName` versus
  `instance`; the agent chart offers `extra_write_relabel_configs` to keep both spellings; Kubernetes
  label names with dots are sanitised to underscores (DOCS S52). REPO check: the importer has no
  mention of these names (no match for `nri-prometheus` or `scrapedTargetKind` in `src/importer`).
- Metric API limits (DOCS S51): timestamps older than 48 hours or more than 24 hours ahead are
  dropped; unique time series per account per day 1 to 15 million (3 million on the free edition,
  up to 10.2 million for some paying organisations; 15 million on request); 100,000 unique time
  series per metric name per day; 100,000 payloads a minute; 150 attributes per metric; attribute
  name 255 and value 4,096 characters; payload 1 MB. When a cardinality limit is exceeded, raw data
  still arrives but rollups stop for the rest of the day, so windows over 60 minutes may be
  wrong unless `RAW` is used. For remote write there is an additional limit on unique count and
  summary series per account per five minutes, with excess series dropped. The repository's research
  note lists the account limit; the per-metric-name limit and the remote write five minute limit are
  not in `docs/research/grafana-notifications-datapath.md` section 6.1.
- OTLP attribute string limit is 4095 characters, one less than the Metric API's 4096 (DOCS S55 versus
  S51).
- PromQL-style mode (DOCS S53): supports a listed set of aggregations, `*_over_time`, `histogram_quantile`,
  arithmetic, `and/or/unless`, rate-like functions including `rate`, `irate`, `increase`, `delta`,
  `deriv`, `predict_linear`, sort functions and `label_values()`; `offset` only if every vector in
  the query has the same offset; 366 steps in a range query; 100 series returned unless an explicit
  `topk()` is added (and its `topk` differs from Prometheus); instant queries look back one
  minute, so a scrape interval over a minute gives "No data found". The page claims over 99.5% of
  queries in the 7.8 million top Grafana dashboards are supported; that is a vendor claim I could
  not check. It does not state whether `group_left`, `label_replace` or subqueries are supported
  (the list shown has no entry for them).
- Query length: I searched the NRQL reference for a query text limit and found only a 4,096
  character limit on a string function result (DOCS S56). The "4,096 character NRQL" figure in the
  brief is therefore UNVERIFIED for query text; the importer should still cap generated NRQL
  defensively.
- Dashboard widget and page limits: not found in the pages I read. The one article that states a
  10 panel limit (S30) is not credible. UNVERIFIED.
- Terraform: applying exported dashboard JSON through `newrelic_one_dashboard_json` lost variables
  with an older provider (THREAD S48); related open requests concern `ignore_time_range` on query
  variables and multi-account widgets. This matters for any Terraform output of dashboards with
  variables.
- Test-hook found in a hobby tool: a New Relic service that translates PromQL to NRQL exists and
  works with an API key (CODE S21). Whether it is public or supported is UNVERIFIED, and I did not
  call it. In a sandbox it could act as an oracle to compare against the importer's NRQL.

### 4.3 Pricing and ingest effects people mention

I found no source that quantifies the New Relic ingest cost of remote-written Prometheus data.
What I did find:

- Vendor-neutral advice to meter by cardinality: Datadog bills each tag combination above 100 per
  host as a custom metric, Grafana Cloud bills active series and a shorter scrape interval raises
  it (ARTICLE S14, vendor view of competitors).
- Remote write `keep` filters and the Prometheus metrics-used analysis (S20, S50) are the tools to
  limit what is sent; whether that matches New Relic billing is UNVERIFIED because the pricing page
  was not read.
- Anecdotes about leaving New Relic for Grafana for cost (S30, S47) go the other way and S30 is
  unreliable.

## 5. Derived edge-case checklist for the importer

"Handled" says whether the repository already deals with it: yes, partly, no. Severity is the harm if
missed: high (wrong result or paging error), medium (degraded or empty widget, manual work), low
(cosmetic or rare).

| # | edge case | severity | handled | evidence in repo | source |
|---|---|---|---|---|---|
| 1 | Name-based metric type inference misclassifies counters and gauges | high | partly | alert translator keeps `_total`, `_count`, `_sum`, `_bucket` manual; `newrelic_metric_type` override is written in the data path plan (`src/importer/sources/grafana/datapath/plan.py`); no tool finds the counters without suffix | S49, S50, S52, https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-prometheus-agent/migration-guide/ |
| 2 | `rate`, `increase` on a metric stored as a delta count (needs `rate(sum(x), 1 second)` style) and wrong type gives wrong answers | high | no (dashboards T2 not built) | `docs/research/grafana-dashboards.md` item 6 says unverified | S53, S54 |
| 3 | PromQL comparison used as a filter inside the query; empty vector semantics (0/0) | high | partly | alert path uses query, reduce, threshold nodes; rule files with one top-level comparison are split (`sources/grafana/notifications/rulefiles.py`); nothing warns about 0/0 | S29, S32, https://dev.to/anand_rathnas_d5b608cc3de/why-your-grafana-5xx-rate-alert-spams-datasourcenodata-every-4-hours-4fk9 |
| 4 | No-data and error states: Alerting maps to loss of signal; `NoData`, `KeepLast`, `DatasourceNoData` have none | high | partly | `sources/grafana/translate.py` `N_NODATA_ALERTING`; `docs/what-migrates.md` | S38, S57, https://github.com/grafana/grafana/blob/main/docs/sources/alerting/fundamentals/alert-rule-evaluation/nodata-and-error-states.md |
| 5 | `for` versus a window: do not apply the window twice | medium | yes | pending period becomes threshold duration in whole windows; documented | S29 |
| 6 | Recovery threshold, keep-firing, evaluation delay, new group delay have no New Relic equivalent | medium | partly | `keep_firing_for` and recovery threshold ignored with a note; others not mentioned | S29 |
| 7 | Dashboard variables used in 89% of PromQL targets | high | partly | read and classified in `sources/grafana/dashboards/variables.py`; the IR and emitters have no variable model (`core/dashboard_ir.py`) | S2, S5, S42, https://github.com/perses/perses/blob/main/docs/migration.md |
| 8 | Variable regex semantics (anchored, first capture group, applied to datasource values) | medium | partly | regex with lookahead goes manual; plain-word forms convert; no fixtures for capture group semantics | S5 |
| 9 | Chained and label-filtered query variables, interval and custom variables, All value `.*` | medium | partly | nested variables go manual (`what-migrates.md`); All handling not documented | S2, S42 |
| 10 | Data source reference rewriting in panels, targets, variables, annotations | medium | partly | `sources/grafana/dashboards/datasources.py` resolves types; the rewrite for emit does not exist yet | S40, S35, S37 |
| 11 | Dashboard JSON versions: classic, v1 envelope, v2, `schemaVersion` 12 to 42 | high | yes | `dashboards/classic.py`, `dashboards/v2.py`, `dashboards/load.py` | S5, S9, S39, S43 |
| 12 | Wrapped `/apis` create call needs `metadata` and `spec` | low | n/a | the importer only reads, and does not use `/apis` | S39 |
| 13 | Library panels missing from an export, library panel variable scope | medium | partly | library panels read via `/api/library-elements`; missing ones go manual | S44, S45 |
| 14 | Repeat panels and rows: migrate the template, not the clones | medium | partly | repeats reported; no rule that skips clones recorded in `what-migrates.md` | S2, S6 |
| 15 | `timeShift` and `timeFrom` | medium | yes | panels manual at plan stage (`dashboards/reasons.py`) | S2 |
| 16 | Transformations, value mappings, overrides, units | medium | partly | transformation manual; mappings and overrides lower the tier | S2, S6 |
| 17 | Panel types with no counterpart (state timeline, status history, geomap, canvas, node graph, xy, news) | medium | yes | counted and reported in the plan | S11, S7 |
| 18 | Annotations dropped | low | yes | reported as dropped | S2, S6 |
| 19 | Label and attribute spelling differs by path (nri-prometheus, agent, OTLP escaping) | high | partly | `check-grafana-data` asks which attributes arrive (`sources/grafana/datapath/check.py`); no table of renamed labels | S52, S4 |
| 20 | `prometheus_server` attribute and HA pairs | medium | partly | plan writes `prometheus_server`; HA dedupe not covered | S50 |
| 21 | Cardinality: 100k per metric name per day, 150 attributes, 15M account, remote write 5 minute limit | high | partly | `plan.py` mentions the cap from a second session's notes, flagged unverified; no preflight that counts series | S51, https://docs.newrelic.com/docs/data-apis/ingest-apis/metric-api/metric-api-limits-restricted-attributes/ |
| 22 | Histogram: `histogram_quantile`, `sum/count` mean, classic buckets as counts | high | partly | T2 classification only; alert side keeps `_bucket` manual | S2, S54 |
| 23 | Vector matching: `on`, `ignoring`, `group_left`; binary operators across different metrics | high | partly | manual for alerts and dashboards; T3 only for terms grouped by the same labels | S2, S4 |
| 24 | PromQL comments, `__name__` regex selectors, `label_replace` captures | medium | no | no comment stripping found in the query classifier; check before relying on it | S2, S4 |
| 25 | PromQL-style API limits (100 series, 366 steps, 1 minute instant look-back, scrape interval over 1 minute) | medium | no | the data path notes mention them for the reverse direction only | S53 |
| 26 | Notification policy tree, matchers, continue, mute and active timings | high | yes | `sources/grafana/notifications/policy.py`, `muting.py`; waits and intervals lost | S2, S31 |
| 27 | Silences and inhibition rules (Alertmanager) | medium | no | silences optional and unbuilt; no mention of inhibit rules in `src/importer` | S31 |
| 28 | Channels with no equivalent | medium | yes | verdict table of 24 integration types (`notifications/contact_point_verdicts.json`) | S12 |
| 29 | Contact point and data source secrets | high | yes | redaction at read time (`core/redact.py`, `sources/grafana/dashboards/redact.py`) | S16, S8 |
| 30 | Recording rules used by panels and alerts | medium | partly | recording rules go manual; no count of dependent panels | S12, S26, S27 |
| 31 | Provisioning drift and order (alerts before dashboards, folder uid collisions) | low for the importer | no | importer only reads and creates; relevant for any provisioning output | S33 |
| 32 | uid collisions when several stacks are merged | medium | partly | duplicate uid noted once at load | S36 |
| 33 | Extraction paging, 500 cap, retries, TLS | medium | partly | paging with `PAGE_SIZE` in `dashboards/extract.py`; retry in `core/apply.py`; `SSL_CERT_FILE` documented | S2, S16 |
| 34 | LogQL parser stages, `unwrap`, alerts on logs | medium | yes | manual list in `what-migrates.md` | S2 |
| 35 | Plugin and Angular panels | low | partly | `dashboards/panels.py` mentions angular; plugin panels are an unmapped type | S40 |
| 36 | Dashboard variables lost when dashboard JSON is applied through Terraform | high | no | no Terraform dashboard output with variables yet | S48 |
| 37 | Dual-run validation and exit criteria | medium | partly | `check-grafana-data` checks arrival, nothing compares values between systems | S14, S7, S2 |
| 38 | Estate hygiene: migrate only used dashboards, rebuild noisy alerts | medium | no | no usage input (Grafana usage stats are not read) | S14 |
| 39 | Generated NRQL length | low | no | no cap found; the figure is unverified (section 4.2) | S56 |
| 40 | Grafana-version-specific export limits (YAML export without import, dynamic dashboards toggle) | low | n/a | importer reads JSON | S43, S44 |

## 6. Recommendations: what to build or test next

Ranked by (customer impact times frequency in the sources) divided by effort. Impact and effort are
my estimates: S is under a day, M a few days, L a week or more. Items that need a live account are
marked; they must follow the project's sandbox rules and need explicit approval per session.

| rank | item | why (sources) | effort | needs live account |
|---|---|---|---|---|
| 1 | Metric type audit for converted queries: list every counter-like metric in the dashboards and rules that does not end in `_total`, `_count`, `_sum`, `_bucket`, plus gauges that do, and generate the exact `newrelic_metric_type` relabel block | edge cases 1, 2; S49, S50, S52 (8 sources on counter semantics) | S to M | no |
| 2 | Extend the data check to dashboards: for each T1/T2 widget, does the metric exist, with which type and attribute spellings (including `namespace` versus `namespaceName`) | cases 19, 21, 37; S52 | M | yes, read only |
| 3 | Sandbox test of `rate`, `increase`, `histogram_quantile` against remote-written counters (a handful of metrics), and a comparison with New Relic's own PromQL-style translation shown in the query builder | cases 2, 22; S53, S54; the item that decides the T2 tier | M | yes |
| 4 | Variable model in the IR and emitters, with a test that variables survive both NerdGraph and Terraform output | cases 7, 36; S2, S5, S48 | L | yes (create-only sandbox) |
| 5 | Alert semantics report per rule: no-data, error, pending, evaluation delay, comparison-in-query, 0/0 risk, keep-firing | cases 3 to 6; S29, S32, S38, S57 | S | no |
| 6 | Fixture set from public data: Perses and Elastic issue titles as test cases (row repeat, column overrides, regex variable, `count(A or B)`, PromQL comments, bare labels), and the 11 dashboards of S22 | S4, S6, S22 | S | no |
| 7 | Cardinality preflight in `plan-grafana-datapath`: read series counts per metric from the Prometheus API (read only) and warn at 100k per metric name, 150 attributes, account budget; borrow `mimirtool analyze` idea for a `keep` allow-list | case 21; S20, S50, S51 | M | no for logic; yes to read the stack |
| 8 | Per-dashboard verdict (Clean, Degraded, Rejected, Failed) and a resumable bulk apply | S7; fits the existing tiers | S | no |
| 9 | Variable regex fixtures (anchored, first group, `/.../` form, All as `.*`) and a clear manual reason for each | case 8; S5 | S | no |
| 10 | Data source reference rewrite test over four locations (panels, targets, variables, annotations) and `${DS_*}` inputs | case 10; S40, S35 | S | no |
| 11 | Recording-rule dependency report: which panels and alerts use names that look like recording rules (contain `:`), and the generated SLO rules from Sloth or Pyrra naming | case 30; S12, S26, S27 | S | no |
| 12 | PromQL pre-processing: strip comments (outside strings), reject `__name__` regex selectors, and report them | case 24; S2, S4 | S | no |
| 13 | Document the bridge option: keep Grafana dashboards and read New Relic through the NRQL plugin (AGPL-3.0) or the Prometheus-compatible API; list that alert rules still need conversion; test whether Grafana alert rules work against that API (UNVERIFIED in the notifications note) | S23; reduces migration scope for customers who only want data in New Relic | S doc, M test | yes for the test |
| 14 | Add inhibition rules and silences to the notification report, so no customer assumes they migrated | case 27; S31 | S | no |
| 15 | Usage-driven scoping: read dashboard view counts if Grafana exposes them for the stack (UNVERIFIED which endpoint), rank dashboards by use, and flag unused ones as candidates to skip | case 38; S14 | M | yes, read only |

## 7. Limits of this research

- The sample leans toward converter authors and vendors; it contains few end-user accounts of
  Grafana-to-New-Relic projects, and none from the New Relic forum or Reddit (blocked, section 0).
- Many sources are issue titles only (S4, S6, S59); a title proves a problem was reported, not its
  cause.
- Several dev.to articles were published in the last six months and may be generated; I used them
  only for mechanisms that another source (S57) confirms.
- Counts in section 3 rank themes in this sample; they are not frequencies in the customer base.
- The `promql-gateway` translate endpoint was not called and its status is unknown.

## 8. UNVERIFIED list

1. A public, supported New Relic endpoint for translating PromQL to NRQL (S21 uses one).
2. NRQL query text length limit; the figure of 4,096 characters applies to a string function result
   in the page I read (S56), not to a query.
3. New Relic dashboard widget count and page limits; none found.
4. The 99.5% PromQL coverage figure in New Relic's page (S53) is a vendor claim.
5. Whether New Relic supports `group_left`, `label_replace` or subqueries in the PromQL-style mode;
   they are absent from the list I read.
6. Licenses: Chronosphere CLI, Oodle CLI language, Dynatrace toolkit (S8), VictoriaMetrics text, and
   `grafanactl` (LICENSE file path returned 404).
7. `grafana-terraform-exporter` features; the README path I tried returned 404.
8. Splunk Observability, Datadog, Honeycomb, Dynatrace and Coroot having no Grafana import: only
   sitemaps and a few pages were checked.
9. Whether Grafana alert rules can query New Relic through its Prometheus-compatible API.
10. Pricing effects of remote-written Prometheus data on New Relic bills; no source found.
11. Whether `rate()`-style widgets built on remote-written counters give correct values for all
    counter types (no sandbox run).
12. Grafana dashboard usage statistics endpoint for usage-driven scoping.
13. The claim in S2 about Elasticsearch behaviour is about that product only; I did not verify it.
