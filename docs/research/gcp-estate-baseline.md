# GCP estate baseline: how much of a realistic Google Cloud Monitoring estate converts today

Date: 2026-11-11. Branch `improve/gcp-estate`. Offline only: no call to Google Cloud, New Relic or any tenant was made.
Corpus: `tests/fixtures/gcp-estate/` (sources and licences in its `SOURCES.md`). Numbers are reproduced by
`uv run python scripts/analysis/gcp_estate_measure.py` and are checked against the converter's own output files on every run.
Same shape as the [Grafana test estate note](grafana-test-estate-results.md).

## 1. Answers first

- Corpus: 347 alert policies (350 conditions), 109 dashboards (1,299 widgets), 20 notification channels, 12 uptime checks.
  276 policies and 96 dashboards are public (Apache-2.0, one repository: `GoogleCloudPlatform/monitoring-dashboard-samples`);
  71 policies, 13 dashboards, all channels and all uptime checks are SYNTHETIC, modelled on the API reference.
- Alert conditions: **60 of 350 converted (17.1%)**, 290 manual, 0 unsupported. Public part 35 of 276 (12.7%); synthetic part 25 of 74 (33.8%).
  Only **16 of the 60** have a metric name confirmed on live data (Compute Engine, Pub/Sub, Storage), so "converted and known to
  return data" is 16 of 350 (4.6%).
- Dashboard widgets: 1,148 data widgets (structural widgets excluded): **65 converted (5.7%)**, 1,083 placeholders. By the
  scoreboard's own rule (chart = converted, placeholder never converted, markdown ignored): 65 charts, 1,194 placeholders (5.2%).
  Of 108 dashboards with data widgets, 11 reach 50% or more, 6 reach 90% or more, 85 reach 0%.
- Notifications and uptime: nothing is created, by design (the export holds no addresses, URLs or keys). 20 of 20 channels need a person.
- Top blockers: PromQL (89 conditions, 519 widgets), metrics New Relic does not collect (115, 250), the filter grammar (36 conditions, 108 widgets),
  quoted label names (91 widgets, one bug), widget types with no mapping (68), MQL (12).
- **90% is not reachable on this corpus**, for alerts or for widgets, and it is not a matter of polish. The estate is 25% PromQL
  alerts and 45% PromQL widgets, and 33% of conditions and 22% of data widgets sit on metrics New Relic does not poll. On the slice of the
  estate that uses native Google service metrics, logs, SLOs and uptime checks, 90% is reachable with about ten code fixes (section 7).

## 2. Method, and what "converted" means

Inputs are assembled as `extract-gcp` writes them: policies and dashboards get resource names when the sample has none (the converter keys
a condition by its name), and channels and uptime checks pass through the extractor's own summary functions (labels, hosts, secrets dropped).
Then the project's commands run in-process: `convert --source gcp`, `convert-gcp-notifications`, `convert-gcp-uptime`,
`convert-dashboards --source gcp`, `plan-remediation`. The script counts the same way as the scoreboard (`docs/dev/overseer.md`):

- Alert: converted = IR confidence exact or approximate. A policy with several conditions gives one monitor per condition.
- Widget: converted = a real chart (`kind: chart`). A placeholder (`unsupported`, "Not migrated") is its own line and never converted. Markdown
  text widgets are neither. A chart with several data sets counts as converted when at least one converts (2 widgets are partial).
- "Data widgets" additionally leave out structural widgets: text (108), section header (9), collapsible group (26), single view group (7), blank (1).
  The scoreboard counts the placeholders among them; this note quotes both.
- Reason code: the first blocking reason of the item, normalised (`REASON_CODES` in the script). Conditions stop at the first failing check, so one
  blocker can hide the next; the "if fixed" figures below are upper bounds unless a prototype is named.
- The script also cross-checks: counts from the converter's output files must equal the counts from the library calls, or it fails.

What this does not measure: whether a converted query returns the right number (nothing was sent to New Relic), and whether the New Relic
metric names exist. `converted` means the importer produced a condition or chart, with the importer's own caveats.

Limits of the corpus, stated plainly:

- One public source. Other collections were looked for; the unauthenticated GitHub API refused further tree listings and no raw paths were guessed.
- The public files are Google's integration templates, not customer estates: 63 conditions use Ops Agent `workload.googleapis.com` metrics and 43 use
  Managed Prometheus metrics, which skews the mix toward what New Relic does not poll. They are also templates (`${PROJECT_ID}`, no channels).
- Synthetic files prove a shape is handled or not; they say nothing about how common it is. Compare the two origin columns before quoting a headline.
- First-failure masking: after a fix, the next check may stop the same item.

## 3. Alert conditions by type

| Condition type | Total | Converted | Manual | Converted % | Reason codes of the manual ones |
|---|---:|---:|---:|---:|---|
| conditionThreshold, plain metric | 193 | 41 | 152 | 21.2% | uncollected metric family 113, filter syntax 19, aligner 9, metadata label filter 5, multi-series trigger 2, reducer 1, comparison 1, alignment period 1, second-stage 1 |
| conditionThreshold, ratio (`denominatorFilter`) | 3 | 0 | 3 | 0% | ratio 3 |
| conditionThreshold, forecast | 2 | 0 | 2 | 0% | forecast 2 |
| conditionThreshold, SLO selector | 4 | 0 | 4 | 0% | slo selector filter 4 |
| conditionThreshold, uptime check | 3 | 0 | 3 | 0% | uptime-check alert 3 |
| conditionAbsent | 10 | 6 | 4 | 60.0% | uncollected metric family 2, filter syntax 1, alignment period 1 |
| conditionMatchedLog | 31 | 13 | 18 | 41.9% | log filter syntax 16, log filter comparison 2 |
| conditionMonitoringQueryLanguage (MQL) | 12 | 0 | 12 | 0% | mql 12 |
| conditionPrometheusQueryLanguage | 89 | 0 | 89 | 0% | promql 89 |
| conditionSql | 2 | 0 | 2 | 0% | sql 2 |
| unknown condition kind | 1 | 0 | 1 | 0% | unknown condition kind 1 |
| **All** | **350** | **60** | **290** | **17.1%** | |

By where the metric comes from (conditions): Google service metrics 47 of 83 converted (56.6%); log filters 13 of 31; Ops Agent third-party
(`workload`) 0 of 63; Managed Prometheus 0 of 43; Ops Agent legacy (`agent`) 0 of 7; log-based metrics 0 of 7; custom 0 of 5; SLO 0 of 4;
uptime 0 of 3; PromQL/MQL/SQL query languages 0 of 103. The "native slice" (Google service + log filter + SLO + uptime) is 60 of 121 (49.6%).

Multi-condition policies: AND/OR/AND_WITH_MATCHING_RESOURCE conditions convert one by one (7 converted conditions); New Relic evaluates each
alone, which the report notes. By the scoreboard rule they count as converted; semantically an AND policy becomes several OR-like conditions.

## 4. Dashboard widgets by type

| Widget type | Total | Converted | Placeholder | Markdown | Main reason codes |
|---|---:|---:|---:|---:|---|
| xyChart | 915 | 57 | 858 | 0 | promql 400, uncollected metric family 232, quoted label name 91, filter syntax 90, second-stage 23, sql 7, reducer 6, aligner 4, ratio 2, mql 2, slo 1 |
| scorecard | 165 | 8 | 157 | 0 | promql 119, filter syntax 18, uncollected metric family 18, mql 1, ratio 1 |
| timeSeriesTable | 37 | 0 | 37 | 0 | widget type |
| logsPanel | 15 | 0 | 15 | 0 | widget type |
| incidentList | 13 | 0 | 13 | 0 | widget type |
| alertChart, pieChart, errorReportingPanel | 3 | 0 | 3 | 0 | widget type |
| text (structural) | 108 | 0 | 68 | 40 | empty text widget 68 (titles only) |
| sectionHeader, collapsibleGroup, singleViewGroup, blank (structural) | 43 | 0 | 43 | 0 | widget type |
| **All** | **1,299** | **65** | **1,194** | **40** | |

Data widgets only (1,148): 65 converted (5.7%). Public part 43 of 1,103 (3.9%); synthetic part 22 of 45 (48.9%). By metric source: Google
service metrics 64 of 305 (21.0%); `workload` 0 of 161; custom 0 of 90; PromQL queries 0 of 524; MQL 1 of 5; SQL 0 of 7; legacy agent 0 of 14.

## 5. Ranked blockers

"Alone" = this is the only blocker on the item. Class: **C** fixable in our code; **D** needs data to exist in New Relic first (a decision,
then a name rule); **N** not expressible in NRQL (workaround given); **X** needs input the extractor does not read yet.

Conditions (290 manual):

| # | Blocker | Count | Class | Notes |
|---|---|---:|---|---|
| 1 | uncollected metric family | 115 | D | workload 62, Managed Prometheus 37, log-based 7, custom 5, agent 4 |
| 2 | promql | 89 | C/D | 29 would convert through the Grafana translator (section 8) |
| 3 | filter syntax | 20 | C | parentheses, OR, `one_of`, regex function, quoted label names |
| 4 | log filter syntax | 16 | C | `log_id()`, parentheses, OR, `jsonPayload.@type`, `=~`, NOT |
| 5 | mql | 12 | C (translator) | 8 of 17 MQL texts are single-stream |
| 6 | aligner | 9 | C | ALIGN_COUNT_TRUE 3, STDDEV 2, NEXT_OLDER 2, INTERPOLATE 1, FRACTION_TRUE 1 (and PERCENTILE_90 1) |
| 7 | metadata label filter | 5 | D | `metadata.system_labels.*` / `user_labels.*`: New Relic attribute names unverified |
| 8 | slo selector filter | 4 | X | `select_slo_burn_rate(...)`: SLO objects are not extracted; the message today is "cannot read" |
| 9 | ratio | 3 | C | |
| 10 | uptime-check alert | 3 | C | needs the uptime plan to name the monitor |
| 11 | log filter comparison | 2 | C | regex on a log field |
| 12 | alignment period | 2 | C | 30s period; an absence condition with no aggregation |
| 13 | forecast | 2 | N | workaround below |
| 14 | sql | 2 | N | |
| 15 | multi-series trigger | 2 | N/C | count or percent of series |
| 16 | reducer, comparison, second-stage, unknown kind | 4 | C | one each |

Widgets (1,083 placeholders among data widgets):

| # | Blocker | Count | Class | Notes |
|---|---|---:|---|---|
| 1 | promql | 519 | C/D | 277 of 558 PromQL queries convert through the Grafana translator after a GCP pre-processing step |
| 2 | uncollected metric family | 250 | D | workload 150, custom 77, agent 13, Managed Prometheus 9 |
| 3 | filter syntax | 108 | C | 76 are quoted label names in a filter (`metric.label."type"="x"`), 24 are bare `${variable}` terms |
| 4 | quoted label name | 91 | C | a bug: `resource.label."project_id"` in `groupByFields` makes the invalid attribute `gcp."project_id"` |
| 5 | widget type | 68 | C | timeSeriesTable 37, logsPanel 15, incidentList 13, alertChart, pieChart, errorReportingPanel 1 each |
| 6 | second-stage aggregation | 23 | C | `secondaryAggregation` |
| 7 | sql | 7 | N | |
| 8 | reducer | 6 | C | REDUCE_PERCENTILE_nn (51 reasons, 30 widgets once the filters are fixed) |
| 9 | aligner, ratio, mql, slo | 11 | C/X | |

Structural placeholders (not data widgets, but counted by the scoreboard): empty-content text 68, collapsible group 26, section header 9, single view group 7, blank 1 = 111.

Prototypes (scratch patches, not committed; numbers are the corpus re-measured with the patch applied): stripping quotes in label names 65 -> 143 data widgets
converted; ignoring `secondaryAggregation` with a note -> 166; reading quoted field names in filters -> 190; dropping bare `${variable}` terms (naive) -> 208 (18.1%).
With those four, Google-service-metric widgets are 207 of 305 (68%).

## 6. Fixable in our code: the top 15, each with a failing test to write first

Each test goes in the file named, fails today for the stated reason, and passes when the fix is right. "Gain" is the corpus effect (upper bound unless prototyped).

| # | Fix | Affects | Failing test (file: name) and assertion |
|---|---|---|---|
| 1 | Quoted label names in `groupByFields` and in filters | 91 widgets + 76 in filter syntax (prototype: +78, then +24) | `tests/test_gcp_dashboards.py: test_group_by_with_quoted_label_name_converts` : a chart with `groupByFields: ['resource.label."project_id"']` is approximate and `series[0].signal.group_by == ["gcp.projectId"]`; `test_filter_with_quoted_label_name_converts`: `metric.label."type"="search"` gives `TagFilter(key="metric.type", value="search")`. Today: manual, "does not make a valid New Relic attribute name" |
| 2 | Route GCP PromQL through the Grafana translator (with the two pre-processing steps in the script: quoted-name selectors and whole-matcher variables) | 89 conditions, 519 widgets (ceiling 29 + 277) | `tests/test_gcp_promql.py: test_promql_widget_converts_to_nrql` : `prometheusQuery: 'sum(rate(http_requests_total[5m]))'` becomes a chart whose NRQL contains `rate(`; `test_promql_condition_with_trailing_comparison_becomes_threshold`: `rate(x[5m]) > 0.5` gives threshold 0.5. Today: "PromQL conditions are not converted" |
| 3 | Filter grammar: parentheses, OR of one label, `one_of()`, `monitoring.regex.full_match`, `starts_with` | 20 conditions, about 15 widgets | `tests/test_gcp.py: test_one_of_and_or_group_become_an_in_list`: `resource.label.zone = one_of("a","b")` gives `TagFilter(values=["a","b"])`; `test_regex_function_becomes_rlike_wildcard`. Today: "cannot read" |
| 4 | Log filter grammar: `log_id("...")`, parentheses, OR, `jsonPayload.@type`, NOT, `=~` | 18 conditions (31 log conditions in all) | `tests/test_gcp.py: test_log_filter_with_log_id_and_or_group_converts`: filter from `google-gce__host-error-log-detected.v1.json` gives a `CountSignal` with the log id filter. Today: "log filter not supported: cannot read 'log_id(...' " |
| 5 | Structural widgets become markdown: empty-content text with a title, section header, collapsible group, single view group, blank | 111 placeholders (scoreboard) | `tests/test_gcp_dashboards.py: test_title_only_text_widget_becomes_a_heading`: `{"title": "Topic", "text": {"content": ""}}` gives kind markdown with `## Topic`. Today: "text widget has no content" placeholder |
| 6 | `timeSeriesTable` as `viz.table` over the same series | 37 widgets | `tests/test_gcp_dashboards.py: test_time_series_table_becomes_a_table`: kind chart, visualization `viz.table`. Today: "dashboard widget type is not supported" |
| 7 | `secondaryAggregation` (second stage) | 23 widgets, 1 condition | `test_second_stage_aggregation_converts_with_a_note`: approximate chart whose notes name the second stage (or a nested query). Prototype: +23 |
| 8 | `logsPanel` as a Log table; `incidentList` as a table over `NrAiIssue` | 15 + 13 widgets | `test_logs_panel_becomes_a_log_table`: NRQL starts `SELECT timestamp, message FROM Log`; `test_incident_list_becomes_an_issue_table` |
| 9 | Aligners ALIGN_COUNT_TRUE / COUNT_FALSE / FRACTION_TRUE / STDDEV / NEXT_OLDER / INTERPOLATE / PERCENTILE_nn other than 50, 95, 99 | 9 conditions, 6 widgets | `tests/test_gcp.py: test_align_stddev_maps_to_stddev` : `ALIGN_STDDEV` gives `Aggregation` stddev (or manual with a precise reason if the IR has none). Today: "aligner ... is not supported" |
| 10 | `REDUCE_PERCENTILE_nn` cross-series reducer | 1 condition, 30 widgets after fix 1 | `test_reduce_percentile_95_becomes_percentile_function`. Today: "reducer ... is not supported". Approximate: percentile across series of averages |
| 11 | Bare `${variable}` filter terms in dashboards become New Relic variables, or are dropped with a note (the prototype drops them) | 24 widgets (prototype +18) | `test_dashboard_variable_term_in_filter_is_noted_not_unreadable`: a filter `${job_id}` no longer produces "cannot read"; the widget is approximate with a note naming the variable |
| 12 | Ratio conditions and ratio widgets | 3 conditions, 3 widgets (2 synthetic of the 3 conditions) | `test_denominator_filter_becomes_a_ratio`: IR `MetricSignal` has two terms and an expression, NRQL divides two aggregates. Needs per-term label filters; check `MetricTerm` first |
| 13 | Uptime-check alerts reference the monitor from the uptime plan | 3 conditions | `test_uptime_alert_converts_to_synthetic_check_failures`: with the check list as input, NRQL is `FROM SyntheticCheck WHERE monitorName = '...' AND result = 'FAILED'`. Today: "depends on the check itself, which is not migrated" |
| 14 | SLO selectors: a precise reason now, a conversion later | 4 conditions, 1 widget | `test_slo_selector_is_named_in_the_reason`: reason contains "SLO" and points to `plan-service-levels`, not "cannot read 'select_slo...'". Conversion needs the extractor to read SLOs (class X) |
| 15 | MQL single-stream subset (fetch, filter, group_by, align, condition) | 12 conditions, 5 widgets: 8 of 17 texts are single-stream | `tests/test_gcp_mql.py: test_single_stream_mql_condition_converts` using `mql-basic-condition.json`. Today: "MQL conditions are not converted" |

Also found, not ranked: policies without a `name` that share a display name collide on `source_id` and silently overwrite each other in `ir/` (first trial run: 350 monitors read, 297 IR files, 56 emitted).
A real API export always has names, so it only bites hand-written or Terraform-derived JSON. Test: `test_two_unnamed_policies_with_one_display_name_are_both_kept` (or fail loudly).

## 7. What cannot be done in NRQL, and the workaround

| Shape | Why not | Workaround |
|---|---|---|
| Metrics New Relic does not poll (Ops Agent `workload.*`, `agent.*`, Managed Prometheus, custom, log-based): 115 conditions, 250 widgets | The GCP integration documents none of them; a converted query returns nothing | Send the data another way (OpenTelemetry collector or Prometheus remote write into New Relic), then give the importer a name rule for that path. Until then manual with the exact list of metrics (already the reason) |
| SQL (BigQuery) conditions and widgets: 2 + 7 | NRQL cannot query BigQuery tables | Keep the scheduled query in BigQuery and forward the result as an event, or stream the table into New Relic and write NRQL on it |
| AND combiner across conditions | NRQL conditions evaluate alone | One NRQL condition combining the signals with `filter()`, or accept OR-like behaviour (the report says so today) |
| Forecast conditions: 2 | A predictive condition type is not what the importer emits | `predictLinear()` in NRQL, or a static threshold; UNVERIFIED, test on a sandbox account before promising it |
| Trigger on a count or percent of series: 2 | A condition fires per series (facet) | `uniqueCount` over a faceted subquery with a `WHERE` on the threshold; UNVERIFIED |
| MQL `time_shift` (week over week), `top`: 3 of 17 texts | NRQL conditions have no `COMPARE WITH` | A baseline condition (different semantics) or manual |
| Calendar-window SLO periods | Service levels use rolling windows | Rolling window plus a note |
| errorReportingPanel, alertChart | No dashboard widget has these data sources | Link or markdown note |

## 8. Is 90% reachable, and what would it take

**Alert conditions: no, on this corpus.** Upper bound arithmetic (all figures are counts of conditions of 350):

| Step | Cumulative converted | % |
|---|---:|---:|
| Today | 60 | 17.1% |
| + every native-slice fix (filters 14, log filters 18, aligners 9, SLO, ratio, uptime, periods, reducers, comparison: 61) | 121 | 34.6% |
| + the data decision: accept `workload`, `agent`, custom, log-based and Managed Prometheus metric names (prototype: ceiling 166 with the existing blockers still in force; about 100 more with the filter fixes) | about 221 | about 63% |
| + PromQL through the Grafana translator (29 of 89) | about 250 | about 71% |
| + MQL translator (about 8 of 12 single-stream) | about 258 | about 74% |

The remaining 26% is PromQL the translator cannot read (60 of 89: vector matching, `label_replace`, `_over_time`, offsets), SQL, and conditions that need an SLO
or a data path the owner has not chosen. To pass 90% (315 of 350) with about 229 conditions coming from everything else, the PromQL path would need to convert about 97% of the 89 queries,
against 33% (29 of 89) for the existing Grafana translator on these queries and 42% panel-level in the Grafana census. **90% of alert conditions is a PromQL-engine project, not a polish project.**

**Alert conditions, native slice: yes.** The 121 conditions on Google service metrics, logs, SLOs and uptime checks are at 49.6% today and all 61 manual ones are
class C, X or N-with-workaround. Fixes 3, 4, 9, 12, 13 and 14 above (plus ratio) take that slice past 90% in code. Two cautions: only 16 of the 60 converted
have a confirmed New Relic metric name, and the unverified names need a live check per service before "converted" means "returns data".

**Dashboard widgets: no, on this corpus.** 65 of 1,148 today (5.7%). Prototyped cheap fixes: 208 (18.1%). Adding tables, logs and incidents (65 widgets) and ratios: about 275.
Adding the data decision (about 265 more): about 540. Adding PromQL through the Grafana translator (277 ceiling): about 800 (70%). Remainder: PromQL
the translator cannot read (about 250 widgets), SQL, and 111 structural placeholders that are not data. Reaching 90% of data widgets (1,033) with about 540 coming
from everything else needs about 493 of the 524 PromQL widgets (94%), i.e. a PromQL engine plus a metric-name strategy.

**Dashboard widgets, native slice: yes.** On Google-service-metric widgets the four prototyped fixes reach 207 of 305 (68%); the rest are percentile reducers (30), tables,
logs and incident panels (29), three filter families (30) and aligners/ratios (9), all class C. With fixes 1, 3, 6 to 11 the slice reaches 90% or more.

**What would move the headline most, in order of work per point**: (1) the quoted-label bug and the filter grammar (small, about +10 points of widgets); (2) the
structural and table widgets (small, +13 points of the scoreboard figure); (3) a decision on the data path for non-Google metrics, because it is worth more than
any translator and costs no code until a name rule exists; (4) PromQL reuse (medium, the largest single block); (5) an MQL translator for the single-stream subset
(small corpus share today, because Google ended new MQL; existing policies still exist and still need it). An MQL-to-NRQL translator alone is worth at most 12
conditions and 5 widgets here (3% and under 1%), so it is not the route to 90%.

## 9. Notifications and uptime checks

- 20 channels: email 3, Slack 3, SMS 3, PagerDuty 2, Pub/Sub 2, webhook with token 2, webhook with basic auth 1, mobile 2, Campfire 1, HipChat 1.
  Created automatically: **0 of 20**. The extractor keeps type, name and enabled state only, so every channel is a manual step with instructions.
  Routing: 8 workflow sets for the 7 channels that converted conditions use; the 13 others appear as "no converted condition uses". Channels that only manual
  conditions use get no routing.
- Which types have a New Relic destination: email, Slack, PagerDuty and webhook (11 of 20) do; SMS, Pub/Sub, Campfire and HipChat (7 of 20) are reported as "no destination";
  the two mobile channels are reported the same way although New Relic documents a mobile push destination type (`MOBILE_PUSH` in `grafana-notifications-datapath.md`), a small mapping gap.
  The privacy choice (no labels exported) is what holds this at 0%; keeping non-secret labels (Slack channel name, webhook URL without the token, email address) would let
  email and Slack plans be filled in, at the cost of putting addresses in the export. Owner decision.
- 12 uptime checks: 8 HTTP, 2 TCP, 2 other. All become inventory rows with the New Relic frequency (0 created; 1 sub-minute period is rounded up to every minute with a note;
  TCP has no Synthetics counterpart the tool knows of). 3 alert conditions depend on uptime checks and stay manual (fix 13).
- Remediation plan: 1,975 items in 16 groups, 1,580 with a full playbook. Its top five fixes agree with this note: PromQL (532 items), widgets that did not convert (491),
  metrics not collected (363), filters (155), widget types (111).

## 10. Reproduce

```
uv run python scripts/analysis/gcp_estate_measure.py --json /tmp/gcp-estate.json
uv run pytest tests/test_gcp_estate_measure.py
```

The script prints the tables above and, unless `--no-experiments`, the PromQL ceiling (existing Grafana translator on the GCP PromQL after pre-processing) and the MQL shape census.
Nothing in the repository was changed to produce these numbers; every code change above is a proposal.

## 9. After the combined GCP work (2026-11-11, branch `integrate/gcp-all`)

The baseline above (sections 1 to 8) is unchanged. This section is the same corpus and the same script
(`scripts/analysis/gcp_estate_measure.py`, run offline, cross-checked against the converter's own output files)
after merging the alert-field, dashboard and channel, data-path, filter and PromQL work. Placeholders ("Not migrated"
widgets) are never counted as converted; the script and the scoreboard now classify widgets with one shared function
(`scoreboard.widget_class`) and the script reads the widgets the converter really writes (group headers become pages, so
the corpus has 1,265 widgets written instead of 1,299 read).

| | Baseline | After |
|---|---:|---:|
| Whole corpus, alert conditions converted | 60 of 350 (17.1%) | **191 of 350 (54.6%)** |
| Whole corpus, data widgets converted | 65 of 1,148 (5.7%) | **786 of 1,148 (68.5%)** |
| Scoreboard widgets: charts / placeholders | 65 / 1,194 | 786 / 430 (of which 68 are empty text boxes) |
| Native slice, conditions (Google service metrics + log filters + SLO + uptime: 121) | 60 of 121 (49.6%) | **85 of 121 (70.2%)** |
| Native slice, widgets on Google service metrics (305) | 64 of 305 (21.0%) | **257 of 305 (84.3%)** |

"Native slice" is the definition of section 3 (conditions) and section 4 (widgets). Conditions: Google service metrics
57 of 83 (was 47), log filters 28 of 31 (was 13), SLO 0 of 4, uptime 0 of 3.

Converted, by the data path the customer must set up first (this is the honest reading of "converted": a query that
returns data only after the data is sent is not the same as one that works today):

| Data path | Conditions (191) | Data widgets (786) |
|---|---:|---:|
| native (New Relic polls it) | 57 | 526 |
| needs Ops Agent or OpenTelemetry | 63 | 165 |
| needs Prometheus remote write | 35 | 8 |
| needs log forwarding | 28 | 0 (15 logs panels count under native) |
| needs custom ingest | 4 | 87 |
| not classified (converted PromQL, no data path set) | 4 | 0 |
| **Manual (not converted)** | **159** | **362 placeholders** |

Only the native rows return data without extra work: **57 of 350 conditions (16.3%)** and **526 of 1,148 data widgets (45.8%)**.
The 13 of the old "16 with a confirmed metric name" that were log-match conditions are now classed as needing log forwarding, so
"confirmed names" fell from 16 to 3 (all native): that is a re-classification, not a loss.

By condition kind (converted of total): threshold on a metric 146 of 193, ratio 3 of 3 (was 0), forecast 1 of 2, absence 9 of 10,
log match 28 of 31, PromQL 4 of 89, MQL 0 of 12, SQL 0 of 2, SLO 0 of 4, uptime 0 of 3. Top remaining blockers: PromQL forms
(about 85 conditions and 271 widgets: template placeholders 23 conditions, range functions 14, same-aggregation ratios 12,
`_over_time` 9, binary operators 8), MQL 12, metrics with no known route 7, metadata labels 6, quoted label names 5.

Caveats. "Converted" means the converter wrote a query, not that it returns data; names on the non-native paths are
unverified. A logs panel is counted as a converted chart (a table over `Log`); 15 of the 786 widgets are of that kind.
A ratio of one metric to itself converts only with a summing aligner; without one it stays manual (an average of
parts over an average of totals overstates a share), which is why one widget fewer converts than in a first run. Numbers: `uv run python
scripts/analysis/gcp_estate_measure.py --json out.json` on tag `integrate/gcp-all`.
