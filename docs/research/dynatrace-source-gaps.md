# Dynatrace source inspection, 2026-11-16 (rule 9)

PROGRESS: one lever qualifies (scalar companion field in builder tiles, +20 of 454 data tiles = 4.4 points). Alert side: NO PROGRESS.

## Bad news
- Alert-type items on the public corpus: 0 convert. 19 workflows (all manual: event or manual triggers, or no DQL task, plus Jira/Slack/ServiceNow/Ansible tasks), 1 Davis detector, 3 metric events (all on RUM session metric with no NR signal), 7 alerting profiles, 4 notifications, 4 maintenance windows: all manual by design. Public repos hold almost no real alert definitions; 186 other Settings objects (bizevents capture, SLO, auto-tag, sampling, GitHub connections) are out of scope. 96 management zones have no NR form.
- SLO objects (20, builtin:monitoring.slo) are templated in Monaco (name, metric expression and filter are placeholders), so no real query shape can be sampled. Not read by dynatrace-classic ("schema not read"). Unmeasured value.
- Fixtures baseline (unchanged): classic 85 read, 24-25 approximate; workflows 2 of 8 emitted; dashboards 64 data tiles = 5 exact, 21 draft, 38 manual.
- Dashboard corpus is clustered: 5 dashboards (azure devops 103, gitlab 52, github 34, argocd 26, release 11) hold 226 of 299 fetch tiles. Any ceiling there is "a few dashboard families", not a broad population.

## Ranked gaps (Gen3 dashboards, 38 dashboards, 454 data tiles; baseline 60 exact + 7 draft)
| Construct | Tiles | Sampled ceiling | NR form |
|---|---|---|---|
| fetch events/bizevents/logs/spans (first reason: source not read) | 299 | strict-simple shapes only (filter/summarize/sort/limit/fields, count/countIf/avg/median/percentile/in/matchesPhrase): 147 (events 78, logs 33, bizevents 24, spans 11, entity 1) = 32%, but only as DRAFT: event type names, attribute names and the data itself must be ingested/mapped by the customer. The other 152 need fieldsAdd 59, dedup 45, expand 33, parse 27, join/lookup 8 each, array()/collectArray/if. | Partly. Custom events and Log/Span exist; join/lookup/parse/dedup/expand: no NRQL form. SDLC and Azure DevOps events are customer-defined |
| timeseries with `value.A = f(x, scalar:true)` builder companion | 20 (all single-reason; nvidia 16, nutanix 4; also 6 fixture tiles) | 20 tiles = 4.4 points; metric names are Prometheus passthrough, same as the 15+ siblings that already convert as exact | Yes: drop the scalar field, keep the series (or FROM ... without TIMESERIES for a billboard) |
| filter not a WHERE (entityAttr, nested subquery, MZ override if()) | 20 (19 from one sli dashboard) | about 0 | No: entity attributes and entity-fetch subqueries do not exist in NRQL |
| array arithmetic (a[]/b[], arrayAvg, arrayLast, arraySum) | 59 mention it, 11 single-reason; 30 without append/arrayLast | at most ~15 (k6, failure rate a/b) | Yes for ratio of two aggregates; arrayLast: latest() |
| shift (time) | 8, all argocd, with 2 reasons each | 0 alone | COMPARE WITH exists (12 hits in NR NRQL doc), but shift:-1m is a 1-minute offset, not a comparison |
| sort by expression (arraySum) | 6 | ~4 | partly (FACET ORDER BY a sum) |
| aggregation with no NRQL function / histogram buckets | 5 | 0-3 | percentile of bucket series: no |
| Davis problems/events | 4 | 0 | no |
| dedup, join, lookup, append, parse | 45/8/8/3/27 mentions | 0 | no |

## What NR cannot reproduce
join, lookup, append of another table, dedup, record-array expansion, parse (DPL) over raw text, entityAttr()/entity fetch, management zones, Davis problem records, makeTimeseries on arbitrary event fields over joined data. Workflow automation (Jira, ServiceNow, Ansible, remediation) has no NR counterpart in this tool.

## What is going on
- Dynatrace is moving to Gen3: dashboards are documents of DQL tiles (`dql-builder-metrics` subtype generates the scalar companion), workflows are the automation layer, OpenPipeline replaces classic log/event processing (config-as-code release 2.31.0 on 2026-10-23, Terraform 1.108.0 on 2026-11-05; recent commits add OpenPipeline download, remove old OpenPipeline functionality, add credential vault v2, segments data sources; terraform has OpenPipeline v2 resources per event kind).
- Classic objects keep shipping in the repos (alerting profiles, problem notifications, metric events, management zones: 96 zones in the config-as-code test data), but new schemas and demos are Gen3. For migration: dashboards will be DQL on `fetch` more than on metrics; alerting is heading to Davis anomaly detectors and workflows, which hold DQL.
- Terraform provider is Apache-2.0 per its LICENSE file, not MPL.

## Recommended levers
1. Builder scalar companion (`scalar: true` in a timeseries field list): +20 tiles on this corpus (4.4 points of data tiles), 6 more on fixtures (6 of 64 = 9 points but under the 10-item bar by itself). Verdict stays manual only for the single-value viz choice. Stop rule: the 20 must go from manual to exact/draft.
2. fetch logs and spans onto Log/Span with a mapping file (as for alerts): sampled 44 simple-shape tiles (logs 33 from one Azure DevOps dashboard, spans 11), up to 9.7 points but draft, clustered in 2 families; needs an attribute map the owner must approve. Medium confidence.
3. Ratio of aggregates via `a[]/b[]` fieldsAdd: about 15 tiles (3 points), k6 and LLM dashboards; do after 1.
Not recommended: fetch events/bizevents (118 simple tiles): the data is customer-ingested SDLC events with no NR event type; the converted query would be a guess.

## Unverified
- Which of the fetch-simple tiles would actually return rows in NR (no live data).
- Ceilings assume the tile has no second reason; sampled by regex on the query text, not by running a modified planner.
- HCL examples in the terraform provider (about 830 .tf) were not run through our converters (JSON only); detector/metric-event counts there are unmeasured. Classic dashboards: 9 planned, 17 tiles, 0 convert (not sampled).
- NR capabilities: NRQL COMPARE WITH, FILTER and percentile confirmed present in docs-website nrql/nrql-syntax-clauses-functions.mdx; custom events doc path not located.
- Counts are public samples, not customer results.
