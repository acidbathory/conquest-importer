# Grafana ecosystem survey (clean-room, facts and ideas only)

Date 2026-11-12. Clones in /tmp/community/grafana-eco/. Helper: /tmp/community-tools/eco_measure.py. Nothing executed from downloads.

## Licences (from LICENSE files; GitHub API agrees unless noted)
- grafana/mimir, grafana/alerting, grafana, loki, tempo: AGPL-3.0. Ideas only. (mimir and alerting read via API, not cloned.)
- grafana/grafana-foundation-sdk, grafana/cog, grafana/dashboard-linter, grafana/grizzly, kubernetes-mixin, kube-prometheus, node_exporter: Apache-2.0.
- grafana/jsonnet-libs: root LICENSE is Apache-2.0 (Grafana Labs 2020); API says NOASSERTION; only one LICENSE file in the tree, so all mixins fall under it.
- terraform-provider-grafana: MPL-2.0 (file-level copyleft; do not copy code).
- samber/awesome-prometheus-alerts: CC BY 4.0 for rules (dist/rules, _data/rules.yml), MIT for site code. Usable as fixtures with attribution.
- newrelic-experimental/nr-grafana-migration: Apache-2.0, archived (last commit 2024-02-16).

## 1. Bad news
- No maintained Grafana-to-New-Relic converter exists. Searches for grafana/newrelic/nrql/datadog/dynatrace converters returned one New Relic tool (archived), one Dynatrace script set, one SUSE tool. No Datadog target tool found.
- kubernetes-mixin commits no rendered output (jsonnet only); kube-prometheus has rendered YAML but dashboards sit inside ConfigMaps.
- Most of the new corpus is Prometheus-datasource, so it adds volume but few new panel shapes (types: 1983 timeseries, 655 stat, 562 row, 176 table, 74 graph, 63 gauge, 57 bargauge).

## 2. Ideas (tool, licence, gain)
- mimirtool analyze (AGPL, ideas only): same job as our hints input step. Facts: it walks panel targets and templating variables of type query; unwraps label_values(...) and query_result(...) to the inner PromQL; swaps Grafana variables and $__rate_interval-style ranges for fixed placeholders ([5m], [5m:1m]) so the Prometheus parser accepts the expression; walks the AST for vector selectors and also takes __name__ matchers; reports parse errors separately instead of failing. Gain: check our extractor handles templating queries and the range-placeholder trick; it also supplies a metric inventory per dashboard, which feeds the "does the data exist in NR" check.
- mimirtool rules (AGPL): parse/compare/lint of rule groups with namespace handling. Gain: rule-group model idea only.
- dashboard-linter (Apache-2.0, Go): 16+ rules documented in docs/rules/*.md: rate-interval, job/instance label matchers, datasource template variable, units, uneditable, element-reference. Gain: its rules are a ready checklist of what makes a dashboard portable; a dashboard that fails "datasource as variable" or "$__rate_interval" is a known conversion risk and could be a preflight tag in our report. Its lint/testdata and model_test.go give small permissive fixtures.
- grafana-foundation-sdk + cog (Apache-2.0): typed models for dashboard v1, v2beta1 (tabs and rows), alerting v0alpha1/v1beta1, plus per-datasource query schemas (cloudwatch, azuremonitor, bigquery, athena ...) under .cog/resources. Gain: authoritative field lists for the v2 dashboard schema we will meet as Grafana 12+ exports spread; schemas for non-Prometheus targets (CloudWatch query fields) to validate our reading of them. 146 JSON files, mostly package/config, not dashboards.
- grizzly (Apache-2.0): resource-as-YAML envelopes (apiVersion/kind/metadata/spec) for dashboards, folders, alert rules; terraform provider uses grafana_rule_group with data blocks. Gain: input shapes to accept besides API JSON.
- nr-grafana-migration (Apache-2.0, archived): see 4.

## 3. Corpora NEW to us (permissive)
- jsonnet-libs rendered dashboards: 258 JSON under */dashboards_out/ (253 unique titles; 5 title overlaps and 3 uid overlaps with /tmp/gfcorpus). 3781 panels, 4880 targets (4774 with PromQL expr), 1342 template variables, 218 panels with transformations. Apache-2.0. Ours for comparison: 183 dashboards, 4211 panels, 626 variables, 169 transformation panels. Note variables per dashboard are about 5 vs 3.4 ours, so this set stresses variable handling more. Path: /tmp/community/grafana-eco/jsonnet-libs/<mixin>/dashboards_out/*.json (about 90 mixins: cilium, kafka, mongodb, redis-enterprise, haproxy, clickhouse, vsphere ...).
- jsonnet-libs rendered Prometheus alert rules: 101 prometheus_alerts.yaml files, 445 alert rules; paths */prometheus_rules_out/prometheus_alerts.yaml; plus */tests/prometheus_alerts_test.yaml (promtool unit tests, give expected firing cases). Apache-2.0.
- awesome-prometheus-alerts: 117 YAML files under dist/rules/<tech>/, 1185 alert rules, CC BY 4.0 (attribution needed), plus test/*.test.yml. Real-world PromQL breadth is wide (azure, aws-cloudwatch, stackdriver exporters included).
- kube-prometheus manifests: 33 dashboards embedded in manifests/grafana-dashboardDefinitions.yaml and 139 alert rules in manifests/*rometheusRule.yaml. Apache-2.0. Overlap with kubernetes-mixin content likely; needs JSON extraction from YAML block scalars.
- These are Prometheus rule files, not Grafana-managed alert JSON; they test PromQL to NRQL alert conditions, not our in-r3 shape.

## 4. Existing converters
- newrelic-experimental/nr-grafana-migration (Python, about 600 lines, archived 2024): pulls dashboards over the Grafana API, sends each PromQL expression to an undocumented New Relic service (address withheld here; it needs a logged-in session or user key) and gets NRQL back, caches results, strips template-variable label matchers with a regex, then calls dashboardCreate. Panel map: graph/timeseries to line, singlestat/stat/bargauge to billboard (instant), gauge to bullet (max from several config paths), table to table, text to markdown; anything else raises "unknown type". No transformations, no alerts, no variables beyond deletion. Also test-queries.py replays expressions against prometheus-api.newrelic.com/api/v1/query_range, meaning New Relic exposes a PromQL endpoint over its data. Unverified: whether the translate service is still live or available to us; do not call it without owner approval.
- damoke012/grafana-dynatrace-migration (no licence, tiny): export, inventory, metric audit (rewrap vs remap), PromQL to DQL, Monaco deploy. Approach worth noting: classify each metric as "same name in target" or "needs remapping" before converting.
- aeltai/odyssey (Apache-2.0): Grafana dashboards to SUSE Observability YAML.
- dpacheconr/promql-nrql-comparison (no licence): compares PromQL and NRQL results; the validation approach, not a converter.
- Others found are Grafana-to-Grafana (molu8bits, DashMove, grafana-dashboard-to-scenes MIT).

## 5. Pain points reported
Found little community text on our specific blockers; searches of GitHub issues for dashboard migration to New Relic or Datadog returned nothing relevant. Evidence available is structural: nr-grafana-migration drops transformations and variables silently and maps bargauge/stat to billboard with instant queries (so series identity is lost in any multi-series gauge); dashboard-linter's rate-interval rule shows rate on fixed ranges is common and judged a mistake in Grafana itself, so rate on non-counters and fixed windows will appear in real corpora. mimirtool treats unsupported panel types as errors, not best-effort. Unverified: no thread found about gauge sum without series identity.

## 6. Levers (ceilings sampled, not converted)
1. Add jsonnet-libs dashboards_out (258) as a second Grafana scoreboard corpus. Ceiling: +137% dashboards, +90% panels over gfcorpus; 218 transformation panels (5.8% of panels) and 1342 variables show where the transformation/variable blockers weigh. Gain is measurement, but it tests whether our rates hold on unseen data; run the existing scoreboard on it first.
2. Run our PromQL-to-NRQL alert path over 445 + 1185 + 139 Prometheus rule alerts (all permissive). Ceiling: 1769 rules against the 200 Grafana alerts we have, a 9x sample of PromQL shapes (rate, histogram_quantile, absent, vector matching). Measure convertible share before building anything.
3. Adopt mimirtool's extraction tricks in the hints step (placeholder ranges, label_values/query_result unwrapping, parse errors counted not fatal). Ceiling: count templating variables whose query we currently fail to read; in the new corpus there are 1342 variables to test it on. Idea-level only, size unmeasured.

## Unverified
- Conversion rates on the new corpora (not run; no builds).
- Overlap beyond title/uid matching; whether jsonnet-libs rendered files match latest source.
- Count of unique PromQL expressions vs gfcorpus.
- Grafana Cloud migration assistant: not examined (it moves Grafana to Grafana Cloud, not to other vendors).
- Terraform provider and grizzly shapes were not read in detail.
