# Datadog source inspection, 2026-11-15 (AGENTS.md rule 9)

Worktree measured: integrate-1111 (8970521). Corpus manifest and SHAs: /tmp/research/datadog-corpus-manifest.md.
No network control was bypassed; only github.com clones. Nothing downloaded was executed. Experiments ran on throwaway copies of our src in /tmp/community-tools/exp{1,2,3}.

## Baselines (scoreboard-style, notes excluded, placeholders never counted)
- Fixtures: monitors 50 of 70 (71%); dashboard widgets 286 of 454 by my count (scoreboard says 284 of 452) (63%).
- Fresh corpus: monitors 78 of 115 (68%; templates only 57 of 75 = 76%, terraform-shaped 21 of 40 = 53%); dashboard widgets 732 of 1280 by scoreboard (57%).
  My own count excluding notes and groups gives 766 of 1314 (58%); the two counters differ by 39 widgets (sunbursts), not reconciled.

## Ranked gaps, dashboards (placeholders in fresh corpus: 548)
| Construct | Corpus widgets | Sampled ceiling (alone, pp of corpus widgets) | NR form |
|---|---|---|---|
| Formula object with keys `limit` (top-N count and order) or `style`; widget is a bare query reference | 92 reason-first (limit 66, style 26); 86 convert in the experiment | style alone +18 (+1.4pp); limit on top +68 (+5.2pp); both +86 (+6.5pp) on corpus, +2 (+0.4pp) on fixtures | yes: FACET ... LIMIT n (converter already emits it for top()) |
| Event/log query `group_by` written as an object `{fields:[...], limit, sort, should_exclude_missing}` instead of a list of `{facet,...}` | 32 reported as "group by is not readable" | +32 (+2.4pp) alone; 17 of 65 corpus dashboards, 137 of 525 integrations-core dashboards contain the key, 2 of 30 fixtures | yes (log FACET; needs log forwarding) |
| `list_stream` (88 of 89 are logs_stream) | 91 in 31 dashboards | not measured as a converter experiment; NR form is a table of Log rows, unverified | unknown |
| Formula with function: derivative 35, count_nonzero 29, default_zero 20, monotonic_diff 14, per_second 8 | 71 (54 in 3 dashboards) | exclude_null and default_zero as pass-through only +14 (+1.1pp) | partly (NRQL derivative, rate); unknown otherwise |
| security_signals data source | 32 (4 dashboards) | n/a | no known form |
| query_value aggregator `last` | 24 (one dashboard) | concentrated, below 2pp | no (converter's own reason) |
| geomap 19, custom 8, manage_status 5, distribution 5, bar_chart 3, change 3, heatmap 3, hostmap 3 | 49 | each under 1.5pp | unknown |
Concentration warning: the formula-limit gain comes from 15 dashboards, 51 of 86 from three (Palo Alto Panorama, Checkpoint, 1E). The older fixture set only gains 2 widgets, so the lever is corpus-dependent.

## Monitors (37 blocked of 115)
Anomaly 9, `default()` over a formula 7 (all claranet idiom), unknown multi-query formula 4 (tailscale etc.), change/pct_change 3, default_zero/fill 3, window over 6h 2, service check 2, syntax errors 3 (probably from my terraform shaping). No single construct reaches 2pp that is not already known (anomaly/change are NR-limited or D5-planned).

## What New Relic cannot reproduce (from our own reasons; not re-verified against docs.newrelic.com in this run)
Datadog anomaly algorithms, change over two windows, cumulative windows, last-of-series across hosts, treemap area, security signals. NR docs citations: none fetched, so any NR form above other than FACET LIMIT is unknown.

## Source model (datadog-api-client-python v1 spec, commit 317c325)
- 23 monitor types: composite, event alert, log alert, metric alert, process alert, query alert, rum alert, service check, synthetics alert, trace-analytics alert, slo alert, event-v2 alert, audit alert, ci-pipelines alert, ci-tests alert, error-tracking alert, database-monitoring alert, network-performance alert, cost alert, data-quality alert, network-path alert, data-jobs alert, llm-observability alert.
- 43 widget definition types incl. new: heatgrid, point plot, embedded app, powerpack, run workflow, split graph, topology map, retention curve, product analytics funnel, journey map.
- Formula object keys: alias, cell_display_mode(_options), conditional_formats, formula, limit, number_format, style. Query types: metrics, events/logs/rum/apm/audit/..., process, apm_dependency/resource/metrics, cloud_cost, slo, product analytics, user journey, retention. Layouts: ordered, free.
- Event query group_by accepts either a list of facet objects or an object with `fields` (spec description).
- Our converter allow-list FORMULA_KEYS lacks `style` and `limit`, and the logs group_by reader lacks the object form.

## What is going on (changelog 2.55 to 2.62, May to Oct 2026)
Dashboards keep gaining widgets (point plot, heatgrid in 2.62, embedded app, topology map change), formula event queries get new data sources (errors, llm-observability), and group_by moves to the `fields` object form. Monitors gain types (data-jobs, llm-observability, data-quality) and options (critical_query, bundle_config, group-by source). Direction: more product-specific widgets and event data sources that have no NR twin; the metric/log core stays stable, so shape coverage of the core (formula options, group_by forms) is where migration gains are.

## Recommended levers (no building; all need an AGENTS rule 2 re-measure on a second corpus first)
1. Accept `style` and honour formula `limit` as FACET LIMIT (and order asc/desc): sampled +6.5pp on this corpus, +0.4pp on fixtures; concentrated in 15 dashboards. Wrong-value risk: limit on a timeseries ranks over the window, not per point.
2. Read the object form of event/log `group_by`: sampled +2.4pp on this corpus (32 widgets, 5 or more dashboards, mostly security-vendor log dashboards); 26 percent of integrations-core dashboards carry the key.
3. exclude_null/default_zero pass-through: +1.1pp, below the 2 point bar; do not build alone.

## Unverified
Monitor corpus terraform shaping (40 items) is approximate. The scoreboard widget total differs from my count by 39. No docs.newrelic.com page was read. Corpus skews to security-vendor integrations. The x1 experiment ignores `limit` semantics, so +6.5pp is a ceiling, not a result.
