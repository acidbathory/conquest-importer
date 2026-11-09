# Proposal: deeper Grafana alert-rule conversion (stream 3)

Branch `improve/grafana-rules2`, from `integration/all-threads` (8d1c9ac). Offline only: no
network, no cloud, no Terraform, no `apply`, `migrate` or `rollback`. `tests/golden/*` and
`docs/improvement/baseline.json` are untouched. One pinned file outside my paths changed; see
"Needs a person".

## What changed

- `src/importer/sources/grafana/models.py`: a rule-file rule keeps its `expr`; a recording rule
  written `record: name` is a recording rule.
- `src/importer/sources/grafana/translate.py`: one `_Plan` (query, how to read it, the test) for
  Grafana rules and rule-file rules, so both go through the same handlers; classic condition with
  one test; resample step; math reason with the number of queries; `PROM_FORMS` hook; `no data OK`
  note; folder, group, labels and runbook link; heartbeat output.
- `src/importer/sources/grafana/rules2/promql.py` (new): counters, ratios and absence.
  `rules2/fields.py` (new): origin note and runbook link, with the safety filters.
- `remediation_rules.py`, `__init__.py` (taxonomy rows, anchored): steps and categories for the
  new reasons and notes.
- Tests: `tests/test_grafana_rules2_{models,rulefile,counters,ratio,absent,pipeline,fields,
  hostile,fixtures}.py`, `tests/grafana_rules2_eval.py` (a small NRQL evaluator),
  `tests/test_grafana_rules.py` and `tests/test_grafana.py` updated. Fixtures:
  `tests/fixtures/grafana-rules2/` (registered with the measure).
- Docs: the Grafana (phase 0) section of `docs/what-migrates.md`, the lessons file.

## Decisions, with the evidence

| Question | Decision | Evidence |
|---|---|---|
| `rate()` in a condition | Not used. `rate(x_total[5m])` becomes `sum(x_total) / 300` with a 300 s window; `increase` becomes `sum(x_total)` | `newrelic-notifications-and-nrql-reference.md` 2.3: `rate()`, `derivative()` are not in the condition pages or the supported list: UNVERIFIED. `sum()` and arithmetic over aggregates are. Counters are stored as deltas (`grafana-native-coverage.md` B 4, P4 and P6) |
| Counter name | Only `_total`, `_count`, `_bucket` | New Relic guesses the type from the suffix (B 4); `_sum` becomes a summary, anything else a gauge. Others are manual with the `newrelic_metric_type` remedy |
| `absent()`, `absent_over_time()` | Heartbeat condition: `count(*)` below 1, loss of signal for the pending period or the range, not wrapped in `filter()` | Reference 2.3 (a lost-signal condition must not use `filter()`); same shape as `sources/gcp` `_absence_condition`. The Grafana threshold must fire on the value 1 that `absent()` returns, else manual |
| `up == 0` | An ordinary metric condition when it is a rule-file rule (comparison = threshold). In a Grafana rule with a threshold node it is manual | A comparison in the query filters; the node then tests the value that is left (0), so the two are not one threshold. The old reason said only "binary operator" |
| Top-level comparison in a rule-file `expr` | The comparison is the threshold; the Grafana threshold node is never also applied | `promrules.split_comparison` (reused, not copied) |
| Ratios | One SELECT with two terms, only when both sides are the same aggregate (avg of gauges, sum of counter rates) of different metrics with equal matchers, grouping and range. Error rates (one metric, two filters) are manual | The IR terms share one scope; `filter()` per term is not expressible |
| `histogram_quantile` | Stays manual | `bucketPercentile` exists in NRQL but is not on the alert-condition function list (UNVERIFIED) |
| `topk`, `offset`, subquery, `label_replace`, other regex | Stay manual | Reference 2.3 (no `LIMIT`, subqueries, `COMPARE WITH`); `RLIKE` anchoring unverified |
| `keep_firing_for` | Note (exists) | No hold in New Relic |
| no data | `Alerting` is loss of signal (exists); `OK` is a new note; `NoData` and `KeepLast` keep their notes | `KeepLast` is not `fill_option LAST_VALUE`: gap filling happens only after a later data point arrives (NG-GAP), so it would not cover total silence |
| Classic condition | One test converts (avg, min, max, sum, last); more than one is manual | Same reducer rules as the reduce node |
| Resample | Ignored with a note when it sits between the query and the reduce | Approximate: values may differ at window edges |
| Math node | Manual; the reason says how many queries it combines | A condition reads one query |
| `within_range` / `outside_range` | Manual (the IR has one threshold per severity, and two conditions would double the alerts) | Unchanged |
| Annotations | `runbook_url` becomes the condition's runbook URL when it is https with no credentials, fragment or token-like parameter. `description`/`summary` stay in the IR | The emitters do not write `message_template` (see Requests) |

## The 17 sandbox rules, before and after

Before: 4 of 17 converted. After: 5 of 17.

| Rule | Before | After | Why |
|---|---|---|---|
| ckg-prom-cpu-gauge | converted | converted | unchanged except the new origin note |
| ckg-prom-regex-unagg | converted | converted | same |
| ckg-loki-errors | converted | converted | same |
| ckg-expr-keeplast-nodata | converted | converted | same |
| ckg-prom-absent | manual: absent() | converted | heartbeat, `count(*)` below 1, expiration 1 minute (`for` 0s); the threshold `gt 0` fires on 1 |
| ckg-prom-5xx-rate | manual: range function | manual: label matcher | `code=~"5.."` needs `RLIKE` or a regex the IR cannot express, so the rate is never reached; the reason is now the matcher |
| ckg-prom-up-zero | manual: binary operator | manual: compares inside the query | the Grafana node tests the value left by `== 0`; a Grafana-managed rule with this shape is almost certainly wrong in Grafana too |
| ckg-expr-flatten | manual: math | manual: math over 2 queries | needs one query |
| ckg-expr-multi-ds-manual | manual | manual | two data sources |
| ckg-expr-within-range | manual | manual | range threshold |
| ckg-loki-latency | manual | manual | `json` parser and `unwrap` |
| ckg-prom-disk-fill | manual | manual | `predict_linear` |
| ckg-prom-join-manual | manual | manual | `group_left` |
| ckg-prom-mem-ratio | manual | manual | `max_over_time` |
| ckg-prom-offset-manual | manual | manual | `offset` |
| ckg-prom-p95 | manual | manual | `histogram_quantile`, alert usability of `bucketPercentile` unverified |
| ckg-prom-topk-manual | manual | manual | `topk` |

The IR of the five that convert changed in one way only: a `grafana origin: ...` note was added
(folder, group, label), and `ckg-prom-absent` is new. The pinned output of `convert/grafana`
changed in 12 files (10 IR files, `main.tf`, `report.md`) and added one file
(`nerdgraph/9998536041402.json`).

New fixtures in `tests/fixtures/grafana-rules2/` (13 provisioning rules, a Prometheus rule file,
a Loki ruler namespace): 8 + 3 + 1 = 12 convert and 9 stay manual, each with its reason pinned in
`tests/test_grafana_rules2_fixtures.py`.

## Measure and gate

`importer improve-measure`, grafana: baseline 6 of 21 (29%); same estate after 7 of 21 (33%);
with the new fixtures 19 of 42 (45%). The other sources are unchanged (cloudwatch 84%, dynatrace
25%, dynatrace-classic 29%, gcp 75%). Gate: see the end of this file.

## Risks

- Counters: `sum(x)/300` equals `rate()` only if the metric reaches New Relic as a delta count.
  The name suffix decides that, and remote write may carry no type. Prometheus also extrapolates
  to the range ends; New Relic windows are tumbling, not sliding. Notes say so.
- Absence: loss of signal needs the metric to have arrived once, and with no facet it fires only
  when no series reports. A metric that never arrived will not alert.
- `up == 0` as a metric condition fires on the value 0 only while the target reports; a target
  that disappears needs an `absent()` rule as well.
- The origin note lists folder, group and up to 10 labels that are short, plain and not
  secret-looking. A label that looks like a name but is sensitive would be shown; remove the
  note's text in `rules2/fields.py` if that is a concern.
- Nothing was run against New Relic. Every NRQL here is checked only by the small evaluator in
  `tests/grafana_rules2_eval.py` on sample rows.

## Needs a person

- `tests/fixtures/emitter-output-hashes.json`: I regenerated the `convert/grafana` group only,
  because `test_output_bytes_are_unchanged[convert/grafana]` failed once the Grafana output
  improved. The other groups are byte-identical. A person should read the diff of the Grafana
  output (the origin note on five rules, one new condition) before this is merged.

## Requests (frozen files)

1. `core/emit/*`: write `notification.message_template` as the condition `description`, with the
   folder and group, and write `monitor.tags` (or a list of `key:value`) as entity tags. Today the
   annotation summary is kept in the IR and not emitted.
2. `core/ir.py`: a per-term scope on `MetricTerm` (or a `filter()` term) would convert error-rate
   ratios of one metric under two filters, the most common ratio alert.
3. Live tests the owner could approve, to settle the open evidence: `rate(sum(x), 1 minute)`,
   `derivative`, `bucketPercentile` in a disabled condition create call (reference 2.3, plan item
   5); `SELECT count(*) ... WHERE metricName = 'up'` with expiration as the absent() condition;
   whether `filter(count(*), ...)` is needed for `up == 0`; whether rule labels can be used in
   workflow filters.

## Gate

See the report of this run. Pending until the final gate below is recorded.
