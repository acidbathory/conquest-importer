# Remediation cookbook: what to do when New Relic cannot do it one-to-one

For practitioners finishing a migration into New Relic. The importer converts what it can and stops where
a conversion would give a wrong number. This guide covers the most common stops, measured on a test estate of
50 community Grafana dashboards and 55 alert rules (2026-11-10). For each blocker there are ranked methods; the
first is the one to try first. Every method says what it needs, what it changes, what it costs, and how to prove it.

This file is generated from `src/importer/core/cookbook/data/*.toml` by
`uv run python scripts/improve/render_cookbook.py`; edit the data, not this file.
`importer plan-remediation` prints the recommended method for each unconverted item.

## One-page decision guide

Ask these five questions in order. They pick the family of method before you look at a single query.

1. **Is it an alert?** Alerts are stricter than dashboards: no subquery, no JOIN, no LIMIT, no TIMESERIES, one
   number per signal. If a dashboard method uses a nested query or JOIN, look for the alert method (family D) or
   change the data at the source (family A).
2. **Do you control Prometheus, Alloy or the OpenTelemetry Collector?** If yes, family A (change at ingest) gives
   exact numbers and simple queries, but only for new data and at the cost of a deploy. If no, stay in NRQL
   (families B, C, D).
3. **Is history needed?** Family A does not rewrite history. Families B, C and D work on data already in New Relic.
4. **How much delay can the alert take?** Every condition adds its aggregation window plus delay (a live firing test
   measured about 5 minutes more than Grafana's evaluation). Sparse data needs a different streaming method.
5. **Is the difference acceptable?** Family F means writing down what is different and getting the owner to agree.
   Some things have no New Relic form at all; the section for each blocker says so.

Families: A change at ingest, B rewrite in NRQL, C restructure, D redesign the alert, E use a New Relic native
feature, F accept and document the difference.

### Status labels

- **VERIFIED-VALUE**: every NRQL in the method was accepted by New Relic (read-only NerdGraph) and a result matched
  the arithmetic on test data (two independent ways of getting the number agree).
- **VERIFIED-VALID**: every NRQL in the method was accepted by New Relic; no value was compared.
- **DOC-ONLY**: read in public documentation; nothing was run (collector files, Terraform, GraphQL mutations,
  Prometheus rules). Test them in a sandbox first.
- **UNVERIFIED**: from memory or inference; the evidence line says so.

The run record is `docs/improvement/cookbook-verification.json`; `scripts/improve/verify_cookbook.py` rewrites it.
Test data carries `ckg_test=true`. Queries below use the real metric names of that estate; replace them with the
customer's.

### Recommended first method per blocker

| Blocker | First method | Family | Status | Alternatives |
|---|---|---|---|---|
| [sum() or count() of a gauge without a label list](#sum-or-count-of-a-gauge-without-a-label-list) | Nested query: latest() of each series, then sum() | B | VERIFIED-VALUE | average(x) * cardinality(x) for a time chart of a steady gauge, Aggregate once at the source (recording rule or collector) and chart the result, Alert on each series (FACET) instead of on the sum |
| [Comparison filters and set operators (== 1, > 0, and, unless)](#comparison-filters-and-set-operators--1--0-and-unless) | WHERE on the sample value | B | VERIFIED-VALUE | Nested query: newest value per series, then filter, filter() inside the aggregate (counts and percentages next to the total), Set operators: IN / NOT IN (SELECT uniques(...)), Alerts: move the comparison into the threshold |
| [Joins with on() / ignoring() / group_left()](#joins-with-on--ignoring--groupleft) | NRQL JOIN with the info metric as the subquery | B | VERIFIED-VALUE | Semi-join: filter by a label that lives on the info metric, Do the join once in Prometheus (recording rule) and ship the result, Add the label in the collector (k8sattributes, or a fixed map for small fleets), Alerts: threshold on the raw series, add the joined label in the notification |
| [Dashboard variables that stay manual](#dashboard-variables-that-stay-manual) | A query variable with uniques(), used as IN ({{name}}) | C | VERIFIED-VALID | Ad hoc filters and group-by choosers: a text variable with LIKE, an identifier variable after FACET, Replace an unreadable variable (query_result, regex capture) by a generated static list, Add the missing label at ingest (cluster, environment) so the variable has something to list, Accept: interval, data source and allValue variables disappear |
| [without() grouping](#without-grouping) | List the remaining labels in FACET (found with keyset()) | B | VERIFIED-VALUE | Let the user choose the grouping (identifier variable) or keep the two labels that matter, Drop the label at the source by aggregating (not by labeldrop), Alerts: FACET on the identifying labels; every facet is its own signal |
| [Counters read without rate()](#counters-read-without-rate) | rate(sum(x), 1 second) for the per-second rate | B | VERIFIED-VALUE | sum(x) for 'how many in the period' (increase, stat of a counter), Tell remote write that an oddly named metric is a counter, OTLP sources: convert cumulative counters to delta in the collector, Alerts on counters: sum() per window, or a ratio of counters, Accept: the raw running total cannot be drawn |
| [_over_time functions (avg_over_time, max_over_time, ...)](#overtime-functions-avgovertime-maxovertime-) | Sliding window: TIMESERIES <window> SLIDE BY <step> | B | VERIFIED-VALID | Stat with its own window (SINCE 15 minutes ago), Alerts: aggregation window with slide_by, Compute the range function in Prometheus and ship the result, Accept: changes() and resets() have no counterpart |
| [Panel transformations](#panel-transformations) | Merge by key: several metrics in one SELECT, faceted by the key | B | VERIFIED-VALID | Rows to columns with filter(), Filter by value, sort, limit: nested WHERE, ORDER BY, LIMIT, Split the panel: one widget per query, link them with a variable, Accept: no equivalent for config-from-query, partition and time formatting |
| [Heatmaps and histogram_quantile panels](#heatmaps-and-histogramquantile-panels) | bucketPercentile() for histogram_quantile | B | VERIFIED-VALID | Per-bucket counts with filter() differences, Send OpenTelemetry histograms so histogram() and percentile() work |
| [absent() and missing series](#absent-and-missing-series) | Loss of signal on a count(*) condition | D | VERIFIED-VALID | Alert on up below 1 per target, Faceted loss of signal: one signal per member, Use the native not-reporting condition for monitored hosts and services, Accept: a target that never reported cannot be detected |
| [histogram_quantile in an alert](#histogramquantile-in-an-alert) | Alert on the percentage of requests slower than the bound | D | VERIFIED-VALID | bucketPercentile in the condition (try it), Record the quantile in Prometheus and alert on the gauge |
| [predict_linear and deriv](#predictlinear-and-deriv) | Static condition with the predict-future-behavior option | D | VERIFIED-VALID | Alert on percent free instead, Record predict_linear in Prometheus, predictLinear() in a chart for triage |
| [Multi-query math in alerts](#multi-query-math-in-alerts) | One query with math on aggregates | D | VERIFIED-VALID | AND of two tests with if(), Minimum traffic guard, Compute cross-source math where both sides live |
| [References to recording-rule series](#references-to-recording-rule-series) | Remote-write the rule output and query with backticks | A | VERIFIED-VALID | Recompute the rule from raw metrics, Alert on the recorded series, Rename the metric at ingest |
| [for / pending duration, noDataState and execErrState](#for--pending-duration-nodatastate-and-execerrstate) | Map for to threshold duration and occurrences | D | DOC-ONLY | Map NoData to loss of signal and gap filling, Choose event_flow, event_timer or cadence, Accept: no keep-firing or recovery threshold |
| [Label-based notification routing](#label-based-notification-routing) | Workflow filter on accumulations.tag.<key> | D | DOC-ONLY | One policy per route, One condition per label value, Accept: no continue, ordering or regex semantics |
| [Mute timings with active intervals](#mute-timings-with-active-intervals) | One weekly muting rule | D | DOC-ONLY | Unroll months and days into several rules, Scope the rule to a policy or tag, Set mutingRulesHandling on workflows |
| [Slack and Teams contact points](#slack-and-teams-contact-points) | Native Slack or Teams destination | E | DOC-ONLY | Webhook destination with a payload template, Route through PagerDuty or Opsgenie, Accept: message look and buttons differ |

## Contents

1. [sum() or count() of a gauge without a label list](#sum-or-count-of-a-gauge-without-a-label-list) (4 methods)
2. [Comparison filters and set operators (== 1, > 0, and, unless)](#comparison-filters-and-set-operators--1--0-and-unless) (5 methods)
3. [Joins with on() / ignoring() / group_left()](#joins-with-on--ignoring--groupleft) (5 methods)
4. [Dashboard variables that stay manual](#dashboard-variables-that-stay-manual) (5 methods)
5. [without() grouping](#without-grouping) (4 methods)
6. [Counters read without rate()](#counters-read-without-rate) (6 methods)
7. [_over_time functions (avg_over_time, max_over_time, ...)](#overtime-functions-avgovertime-maxovertime-) (5 methods)
8. [Panel transformations](#panel-transformations) (5 methods)
9. [Heatmaps and histogram_quantile panels](#heatmaps-and-histogramquantile-panels) (3 methods)
10. [absent() and missing series](#absent-and-missing-series) (5 methods)
11. [histogram_quantile in an alert](#histogramquantile-in-an-alert) (3 methods)
12. [predict_linear and deriv](#predictlinear-and-deriv) (4 methods)
13. [Multi-query math in alerts](#multi-query-math-in-alerts) (4 methods)
14. [References to recording-rule series](#references-to-recording-rule-series) (4 methods)
15. [for / pending duration, noDataState and execErrState](#for--pending-duration-nodatastate-and-execerrstate) (4 methods)
16. [Label-based notification routing](#label-based-notification-routing) (4 methods)
17. [Mute timings with active intervals](#mute-timings-with-active-intervals) (4 methods)
18. [Slack and Teams contact points](#slack-and-teams-contact-points) (4 methods)

## Blockers

### sum() or count() of a gauge without a label list

**Measured:** 167 widgets on 33 of 50 community dashboards (the largest single blocker); 3 alert rules (2026-11-10 test estate).

In Prometheus `sum(kube_deployment_status_replicas)` adds the newest value of every series. NRQL aggregates
samples, not series: `sum()` adds every sample in the bucket, so the answer grows with the number of scrapes.
On the test estate the plain `sum()` of that gauge is 7,020 while the true total is 60 (117 times too high).
The correct answer needs "the latest value of each series, then add them up", and that needs to know which
labels identify one series. The converter cannot know that, so it stops and leaves the widget for a person.

**Decision tree**

- Is this a stat, table, bar or pie (one number or a list per group)? Use the nested query (method 1).
- Is it a time chart and the value is steady (replica counts, limits, capacities)? average() * cardinality() (method 2).
- Does the customer control Prometheus or the collector, and will the panel be used a lot? Aggregate once at the source (method 3): fastest dashboards, exact numbers, loses per-series drill-down.
- Is it an alert? A nested query is not allowed in a condition: alert per series with FACET (method 4).

**What to ask the customer**

- Which labels make one series unique for this metric (for example cluster, namespace, deployment)? Run `SELECT keyset() FROM Metric WHERE metricName = '<metric>'` and let them pick.
- Is the value steady between scrapes, or does it move quickly (a queue length, a connection count)?
- Do you run Prometheus or an OpenTelemetry Collector that you are allowed to change?
- Is this panel a total on a wall display, or is someone going to drill into one series?

**What New Relic cannot do:** New Relic cannot sum 'the latest value of every series' over time in one time-series chart. The nested query returns one number (or a list per group), not a chart with one point per bucket.

**Ranked methods**

#### Method 1: Nested query: latest() of each series, then sum()

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 10 minutes.

- **When to choose it:** Choose this first for any stat, table, bar or pie. It is exactly the PromQL instant-vector meaning: one value per series, then add. No collector access and no re-ingest needed.
- **Prerequisites:** The labels that make one series unique (use keyset() to list them). Query permission only.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None. The widget reads data as it arrives.
- **What changes (numbers, delay, cost):** Numbers become correct (60, not 7,020). The result is one number for the dashboard time range, not a point per bucket. It reads "the newest value inside the time picker window", so a series that stopped reporting an hour ago still counts if the window is wider than that.
- **Risk:** Too few labels in the FACET merge two series into one and under-count; more than 5 labels hit the FACET attribute limit. A series that stopped reporting still counts for the whole window.
- **How to verify:** Run the query, then run `SELECT latest(kube_deployment_status_replicas) FROM Metric FACET cluster, namespace, deployment, instance LIMIT MAX` and add the rows by hand: the two numbers must be equal. In Grafana compare with the same instant value. `importer verify-parity` does not cover widgets yet; compare by eye on one panel per dashboard.
- **Evidence:** Run 2026-11-10 on the sandbox account: the nested query returned 60.0; adding the 20 rows of the FACET query gives 60; plain sum() returned 7020 (see cookbook-verification.json).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/), [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/using-nrql/nested-aggregation-make-ordered-computations-single-query/)

*sum(kube_deployment_status_replicas) done right*

```nrql
SELECT sum(v) FROM (SELECT latest(kube_deployment_status_replicas) AS v FROM Metric FACET cluster, namespace, deployment LIMIT MAX)
```

*The wrong answer, for comparison (do not use)*

```nrql
SELECT sum(kube_deployment_status_replicas) FROM Metric
```

*count(...) by (namespace) done right: count series, not samples*

```nrql
SELECT uniqueCount(deployment) FROM Metric WHERE metricName = 'kube_deployment_status_replicas' FACET namespace
```

#### Method 2: average(x) * cardinality(x) for a time chart of a steady gauge

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 10 minutes.

- **When to choose it:** Choose this when the widget must be a time series and the gauge changes slowly (replica counts, limits, capacities). It is what New Relic's own PromQL translator writes for sum(gauge).
- **Prerequisites:** A gauge that holds about the same value for the whole bucket and a series count that does not change inside the window.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The bucket average is multiplied by the number of series seen in the whole time range. It is exact for a steady gauge (60 here), approximate when values move inside the bucket, and wrong if series appear or disappear during the range because cardinality() counts every series seen in the window.
- **Risk:** Looks right on a quiet day and drifts during rollouts, when series come and go. Use it only for steady gauges and say so in the widget title.
- **How to verify:** Compare one point of the chart with the nested query of method 1 over the same short window (SINCE 5 minutes ago). They must match for a steady gauge; a gap during a rollout shows the weakness.
- **Evidence:** Run 2026-11-10: the stat form returned 60.0, equal to the nested query; the TIMESERIES form was accepted. Behaviour during series churn was not tested.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/), [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql/)

*Stat: total number of replicas*

```nrql
SELECT average(kube_deployment_status_replicas) * cardinality(kube_deployment_status_replicas) FROM Metric
```

*Time chart of the same total*

```nrql
SELECT average(kube_deployment_status_replicas) * cardinality(kube_deployment_status_replicas) FROM Metric TIMESERIES AUTO
```

#### Method 3: Aggregate once at the source (recording rule or collector) and chart the result

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1-2 hours.

- **When to choose it:** Choose this when the panel is used all the time (wall display, SLO page), when cardinality is high, or when the customer owns Prometheus or the collector. The query stays trivial and fast and the number equals what Grafana showed.
- **Prerequisites:** Access to the Prometheus rule files or the OpenTelemetry Collector configuration, and a deploy window. New data only: history before the change is not rewritten.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule evaluation interval (typically 15 to 60 seconds) before the new series exists.
- **What changes (numbers, delay, cost):** A new, small series arrives (for example one per cluster). Per-series drill-down is lost unless the raw series is kept too; keeping both costs the ingest of both. Numbers are exactly the Prometheus numbers.
- **Risk:** Dropping the raw series to save cost removes the ability to drill down. The new metric name must be agreed with the dashboard authors. Recording rules evaluate in Prometheus, so a Prometheus outage also stops the new series.
- **How to verify:** After a few minutes run the NRQL below and compare it with the nested query of method 1 (they must agree within one scrape interval). Then compare with the original Grafana panel.
- **Evidence:** Rule file and processor syntax read in the Prometheus and OpenTelemetry documentation; not run (the sandbox Prometheus holds no such rule). The NRQL below was accepted by New Relic but returns no rows.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/), [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/), [github.com](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor)

*Prometheus rule file: one total per cluster, remote-written with everything else*

```yaml
groups:
  - name: kube-totals
    interval: 30s
    rules:
      - record: cluster:kube_deployment_status_replicas:sum
        expr: sum by (cluster) (kube_deployment_status_replicas)
```

*Same result in an OpenTelemetry Collector (keeps only the cluster label)*

```yaml
processors:
  transform/sum_replicas:
    metric_statements:
      - aggregate_on_attributes("sum", ["cluster"]) where metric.name == "kube_deployment_status_replicas"
service:
  pipelines:
    metrics:
      receivers: [prometheus]
      processors: [transform/sum_replicas, batch]
      exporters: [otlphttp/newrelic]
```

*Chart the recorded series (colon names need backticks)*

```nrql
SELECT latest(`cluster:kube_deployment_status_replicas:sum`) FROM Metric FACET cluster TIMESERIES AUTO
```

#### Method 4: Alert on each series (FACET) instead of on the sum

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for alert rules. A condition cannot contain a subquery, so the nested query of method 1 is not available; New Relic evaluates every FACET value as its own signal, which is also closer to how a person thinks about "a deployment is short of replicas".
- **Prerequisites:** A query that names the identifying labels in FACET (at most 5 attributes, at most 20,000 values).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay (default 1 minute plus 2 minutes); the live firing test measured about 5 minutes more than Grafana's evaluation.
- **What changes (numbers, delay, cost):** One alert per failing series instead of one for the total. More notifications when many series fail together; group them in the workflow. The threshold changes meaning from "total above N" to "any single series above N".
- **Risk:** Alert volume. A total of 60 checked against 50 becomes 20 separate checks; pick a per-series threshold with the customer.
- **How to verify:** Run the condition query in the query builder: each FACET row is one signal. Use the condition preview to see how many would have fired in the last day. Then run the firing test of docs/research/live-alert-firing-test.md on a throwaway policy.
- **Evidence:** Query accepted by New Relic 2026-11-10 (read-only). No condition was created for this method; the NRQL follows the condition rules on the NRQL alert condition page (no LIMIT, no subquery).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Condition query: any deployment with unavailable replicas (static threshold above 0 for 5 minutes)* (condition query)

```nrql
SELECT latest(kube_deployment_status_replicas_unavailable) FROM Metric FACET cluster, namespace, deployment
```

### Comparison filters and set operators (== 1, > 0, and, unless)

**Measured:** 166 widgets on 27 dashboards; 8 of 55 alert rules (2026-11-10 test estate). Also the top blocker of 'remediation: comparison or vector matching' (unlocks 204 items).

In PromQL a comparison is a filter: `kube_node_status_condition{condition="Ready",status="true"} == 1` keeps
only the series whose newest value is 1 and drops the others, and `a and on(cluster) b` keeps the series of `a`
that have a partner in `b`. NRQL has no operator that filters series after the fact. It has three honest
substitutes: a WHERE on the sample value, a nested query that filters the per-series result, and filter() inside
an aggregate. They differ in what "value" means (any sample in the window, or the newest one), and that
difference is the main thing to explain to the customer.

**Decision tree**

- Do you need 'series whose NEWEST value matches' (instant PromQL meaning)? Nested query (method 2).
- Is 'ever matched during the time range' acceptable, or is the data one sample per series per bucket? WHERE on the value (method 1).
- Do you need a count or a percentage of matching series next to the total? filter() inside the aggregate (method 3).
- Is it `and`, `unless` or `or` between two selectors on a shared label? IN (SELECT uniques()) (method 4).
- Is it an alert? Put the bound in the threshold (method 5) rather than in the query.

**What to ask the customer**

- Does the panel mean 'right now' (instant) or 'at any time in the range'?
- Which label do the two sides of the set operator share (cluster, instance, pod)? How many distinct values does it have (the sub-select returns at most 1,000)?
- Is the compared value an exact state (0 or 1) or a measurement? Exact comparisons on raw values need a range (see method 1).

**What New Relic cannot do:** New Relic has no post-aggregation filter on series and no `bool` modifier. Set operators only exist as a filter on one shared attribute (IN or NOT IN a sub-select); matching on several labels at once is not possible in a single operator.

**Ranked methods**

#### Method 1: WHERE on the sample value

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 5 minutes.

- **When to choose it:** Choose this for 'was it ever above/below the bound in the time range' panels, or for state metrics whose value does not flip often. It is the cheapest rewrite and works with FACET, TIMESERIES and alert conditions.
- **Prerequisites:** The metric name is an attribute you can compare in WHERE (it is for Prometheus data in the Metric event type).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The filter applies to every sample, not to the newest one. A deployment that had unavailable replicas for one minute an hour ago still counts for a one-hour range. Grafana's instant `> 0` looks at now. Equality on raw values is unreliable: `WHERE kube_node_status_condition = 1` matched nothing on the test account although the newest value was 1.0; use a range (`>= 1`, or `> 0.5 AND < 1.5`).
- **Risk:** Reading 'ever' as 'now' shows alarms that already cleared. Equality filters silently return nothing.
- **How to verify:** Run the query with `> 0` and with `>= 1` on the same metric and compare with the nested form of method 2 over SINCE 5 minutes ago: for a state that does not flip they agree. Add `SINCE 1 minute ago` to a copy to look at 'now'.
- **Evidence:** Run 2026-11-10: `kube_node_status_condition >= 1` counted 6 Ready nodes (same as the nested newest-value form); `= 1` counted 0; `kube_deployment_status_replicas_unavailable > 0` counted 1 deployment over an hour in which the newest value was 0 for all.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*kube_node_status_condition{condition='Ready',status='true'} == 1  (count the ready nodes)*

```nrql
SELECT uniqueCount(node) FROM Metric WHERE metricName = 'kube_node_status_condition' AND condition = 'Ready' AND status = 'true' AND kube_node_status_condition >= 1
```

*Deployments that had unavailable replicas at any time in the range, per cluster*

```nrql
SELECT uniqueCount(deployment) FROM Metric WHERE kube_deployment_status_replicas_unavailable > 0 FACET cluster
```

#### Method 2: Nested query: newest value per series, then filter

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 10 minutes.

- **When to choose it:** Choose this when the panel means 'right now' (the PromQL instant meaning) or when the value flips often. It is the faithful translation of `x == 1`, `x > 0`, `x != 0` for stat, table and bar widgets.
- **Prerequisites:** The labels that identify one series (FACET list). Not usable in alert conditions or time charts.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Exact instant semantics: one value per series, then the filter. The result is a single number or a list, not a time series. A series that stopped reporting still counts until the window passes it.
- **Risk:** Same as the sum-of-gauge nested query: too few labels in FACET merge series. A big window keeps stale series alive.
- **How to verify:** Count the rows of `SELECT latest(kube_node_status_condition) FROM Metric WHERE condition = 'Ready' AND status = 'true' FACET node LIMIT MAX` that equal 1 by hand; the nested count must match.
- **Evidence:** Run 2026-11-10: nested count with `WHERE v = 1` returned 6; the FACET query shows 6 nodes with latest 1.0.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/using-nrql/nested-aggregation-make-ordered-computations-single-query/)

*Number of Ready nodes, newest value only*

```nrql
SELECT count(*) FROM (SELECT latest(kube_node_status_condition) AS v FROM Metric WHERE condition = 'Ready' AND status = 'true' FACET node LIMIT MAX) WHERE v = 1
```

*Which deployments are short of replicas right now*

```nrql
SELECT * FROM (SELECT latest(kube_deployment_status_replicas_unavailable) AS unavailable FROM Metric FACET cluster, namespace, deployment LIMIT MAX) WHERE unavailable > 0
```

#### Method 3: filter() inside the aggregate (counts and percentages next to the total)

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 10 minutes.

- **When to choose it:** Choose this for 'bad / total' panels, percentages and charts over time, where a comparison is only used to count matching series.
- **Prerequisites:** None beyond the metric and a label to count (uniqueCount) or the samples to count (count).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Gives a time series (add TIMESERIES), so it is the only one of the three rewrites that charts over time. Like method 1 it tests samples, so it means 'at any sample inside each bucket'.
- **Risk:** Percentages divide by distinct series seen in the bucket, which can differ from Prometheus' count if series are missing in some buckets.
- **How to verify:** Run `SELECT uniqueCount(deployment) ...` for the numerator and denominator separately over the same range and check the ratio by hand.
- **Evidence:** Run 2026-11-10: filter() form returned bad=1, total=2, equal to the two separate queries.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Share of deployments with unavailable replicas, as a chart*

```nrql
SELECT filter(uniqueCount(deployment), WHERE kube_deployment_status_replicas_unavailable > 0) / uniqueCount(deployment) * 100 AS 'percent unavailable' FROM Metric WHERE metricName LIKE 'kube_deployment_status_replicas%' TIMESERIES 10 minutes
```

#### Method 4: Set operators: IN / NOT IN (SELECT uniques(...))

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for `a and on(label) b` (keep a where b exists) and `a unless on(label) b` (keep a where b does not exist) when both sides share exactly one label.
- **Prerequisites:** One shared label with at most 1,000 values in the time range (uniques() default is 1,000). Not allowed in alert conditions.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The sub-select is evaluated over the whole time range, not per timestamp, so 'a and b' becomes 'a, where b existed at some time in the range'. `or` has no equivalent; use two series in one chart.
- **Risk:** More than 1,000 distinct values are silently cut. Matching on two labels (on(cluster, namespace)) cannot be written.
- **How to verify:** Count a with and without the IN clause. Run the sub-select alone and check the number of values against the PromQL `count(b)`.
- **Evidence:** Run 2026-11-10: the IN sub-select form was accepted and returned the same count as the plain query (all clusters present). No negative case was compared.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*kube_deployment_status_replicas_unavailable and on(cluster) kube_pod_info*

```nrql
SELECT latest(kube_deployment_status_replicas_unavailable) FROM Metric WHERE cluster IN (SELECT uniques(cluster) FROM Metric WHERE metricName = 'kube_pod_info') FACET cluster, namespace, deployment
```

*The unless form: clusters that have deployments but no pod_info*

```nrql
SELECT uniqueCount(deployment) FROM Metric WHERE metricName = 'kube_deployment_status_replicas' AND cluster NOT IN (SELECT uniques(cluster) FROM Metric WHERE metricName = 'kube_pod_info') FACET cluster
```

#### Method 5: Alerts: move the comparison into the threshold

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for every alert whose PromQL ends in `> n`, `== 0`, `< 1`. In New Relic the comparison is the condition's threshold, not part of the query, so the query returns the raw number.
- **Prerequisites:** A query that returns one number per signal; FACET if the PromQL grouped.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay, as for any condition.
- **What changes (numbers, delay, cost):** The query no longer drops series: all series are evaluated, those that pass are simply below the bound. `x == 0` becomes 'below 1' for integers, never 'equals 0' on a float. The Grafana `for:` becomes the threshold duration.
- **Risk:** Equality thresholds on floats. A comparison between two selectors (`a > b`) cannot be a threshold: compute `a - b` or `a / b` in the query and threshold that.
- **How to verify:** In the condition preview check the line against the threshold. Fire it once with a deliberately low threshold on a throwaway policy.
- **Evidence:** Queries accepted 2026-11-10. Conditions not created.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*up == 0  becomes: query latest(up) per instance, threshold 'below 1 for 5 minutes'* (condition query)

```nrql
SELECT latest(up) FROM Metric WHERE job IS NOT NULL FACET job, instance
```

*memory_used > memory_limit  becomes a ratio with the threshold at 1* (condition query)

```nrql
SELECT latest(jvm_memory_used_bytes) / latest(jvm_memory_max_bytes) FROM Metric WHERE area = 'heap' FACET application, instance
```

### Joins with on() / ignoring() / group_left()

**Measured:** 100 widgets on 14 dashboards; 5 of 55 alert rules (2026-11-10 test estate). Typical joins: kube_pod_info (pod to node), node_uname_info (instance to host name), kube_*_labels.

PromQL joins two series by matching labels and can copy a label from one side to the other
(`kube_pod_status_phase * on(namespace, pod) group_left(node) kube_pod_info`). Almost every real use is an
'info metric' join: attach a friendly name or an owner label to a numeric series. New Relic has three answers.
NRQL JOIN lets a dashboard query use an attribute of another query as a facet (measured below). Ingest-time
enrichment puts the label on the series once and is the only way for alerts. Everything else is a table next
to a chart.

**Decision tree**

- Dashboard widget, join key is ONE label that is unique on the info side (instance, pod)? NRQL JOIN (method 1).
- You only need to FILTER by the joined label (a 'node' variable)? Semi-join with IN (method 2).
- The series is used in alerts, or the join key is two labels, or the dashboard is slow? Join once at the source (method 3) or in the collector (method 4).
- It is an alert and you only need the friendly name in the message? Alert on the raw label and enrich the notification (method 5).

**What to ask the customer**

- Which two metrics are joined and which label is copied across (group_left(node))?
- Is the join key unique on the info side? (One node name per instance? pod names reused across clusters?)
- Is the joined label needed in an alert condition, or only on dashboards?
- Who owns the Prometheus or collector configuration, and can a new recording rule be deployed?

**What New Relic cannot do:** A join cannot be used inside an alert condition, and a JOIN subquery cannot be a time series; the join key must be one attribute and may match at most 100 rows. There is no `group_right`, no `ignoring()` and no multi-label match.

**Ranked methods**

#### Method 1: NRQL JOIN with the info metric as the subquery

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 20 minutes.

- **When to choose it:** Choose this first for dashboard widgets: the info metric is read once as a subquery and its labels (node, nodename) become facets of the outer query. No collector access, no new data.
- **Prerequisites:** One join key that is unique on the info side. Not allowed in alert conditions. The subquery cannot be a time series (it gives one result for the whole range).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Works with TIMESERIES on the outer query. The info labels are fixed for the whole time range (a pod that moved nodes is attributed to the node in the subquery's result). Subquery LIMIT defaults to 10: always write LIMIT MAX (maximum 5,000). One key may map to at most 100 rows.
- **Risk:** Key collisions: pod names repeated in two clusters join to both. Silent truncation if LIMIT is left at 10. Slower than a plain query on large estates.
- **How to verify:** Run the outer query without the join and facet by the join key; sum of the per-node rows must equal the total. Compare the per-node count with `SELECT uniqueCount(pod) FROM Metric WHERE metricName = 'kube_pod_info' FACET node`.
- **Evidence:** Run 2026-11-10: pods per node through the JOIN (5 for each of 6 nodes) equal the direct per-node count on kube_pod_info; the node_uname_info join returned one series per host name.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Running pods per node: kube_pod_status_phase * on(pod) group_left(node) kube_pod_info*

```nrql
FROM Metric JOIN (FROM Metric SELECT latest(kube_pod_info) AS info WHERE metricName = 'kube_pod_info' FACET pod, node LIMIT MAX) ON pod SELECT uniqueCount(pod) WHERE metricName = 'kube_pod_status_phase' AND phase = 'Running' AND kube_pod_status_phase >= 1 FACET node LIMIT MAX
```

*CPU busy rate per host name: node_cpu_seconds_total * on(instance) group_left(nodename) node_uname_info*

```nrql
FROM Metric JOIN (FROM Metric SELECT latest(node_uname_info) WHERE metricName = 'node_uname_info' FACET instance, nodename LIMIT MAX) ON instance SELECT rate(sum(node_cpu_seconds_total), 1 second) WHERE mode != 'idle' FACET nodename LIMIT MAX TIMESERIES 5 minutes
```

#### Method 2: Semi-join: filter by a label that lives on the info metric

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this when the joined label is only used to choose which series to show (a 'node' or 'owner' variable), not to group by.
- **Prerequisites:** A dashboard variable or a literal value for the joined label.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** One query, no JOIN limits. You cannot group by the joined label with it; use method 1 for that. At most 1,000 values come back from the sub-select.
- **Risk:** Sub-select over a long range includes pods that have since moved.
- **How to verify:** Count the pods returned and compare with `SELECT uniqueCount(pod) FROM Metric WHERE metricName = 'kube_pod_info' AND node = 'node-a1'`.
- **Evidence:** Accepted by New Relic 2026-11-10 (variable replaced by a literal for the read-only run).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/), [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/)

*Restarts of the pods that run on the selected node(s)*

```nrql
SELECT sum(kube_pod_container_status_restarts_total) FROM Metric WHERE pod IN (SELECT uniques(pod) FROM Metric WHERE metricName = 'kube_pod_info' AND node IN ({{node}})) FACET pod LIMIT MAX
```

#### Method 3: Do the join once in Prometheus (recording rule) and ship the result

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1-2 hours.

- **When to choose it:** Choose this when the joined label is needed in alerts, when the join key is two labels (namespace, pod), or when many widgets use the same join. It moves the join to the system that is good at it.
- **Prerequisites:** Access to Prometheus rule files (or Mimir / VictoriaMetrics ruler) and a deploy window. The info metric must be scraped by the same Prometheus.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule interval.
- **What changes (numbers, delay, cost):** A new series with the extra label arrives; the original stays unless you drop it. Costs the ingest of the new series (same cardinality as the left side). History is not rewritten: old data lacks the label.
- **Risk:** Two copies of the same data if the raw metric is also sent. Many-to-many info metrics (duplicate kube_pod_info rows) make Prometheus reject the rule evaluation: fix the duplicates first.
- **How to verify:** Compare `SELECT latest(...) FROM Metric FACET node` on the new series with the NRQL JOIN of method 1 for the same time: the per-node numbers must be equal.
- **Evidence:** Rule syntax read in the Prometheus documentation; the NRQL on the recorded name was accepted but returns no rows on the sandbox (no such rule runs there).
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/), [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/)

*Prometheus rule: copy the node label onto the pod phase series*

```yaml
groups:
  - name: joins
    interval: 30s
    rules:
      - record: namespace_pod_node:kube_pod_status_phase:info
        expr: |
          kube_pod_status_phase
            * on (namespace, pod) group_left (node)
          kube_pod_info
```

*Query the joined series; node is now a normal attribute (usable in alert conditions)*

```nrql
SELECT uniqueCount(pod) FROM Metric WHERE phase = 'Running' AND `namespace_pod_node:kube_pod_status_phase:info` >= 1 FACET node
```

#### Method 4: Add the label in the collector (k8sattributes, or a fixed map for small fleets)

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1-2 hours.

- **When to choose it:** Choose this for application and host telemetry that goes through an OpenTelemetry Collector, where the label you want (node name, pod owner, namespace labels) is known to the collector or is a short fixed list.
- **Prerequisites:** An OpenTelemetry Collector in the path (Alloy or the contrib build), RBAC to read pods for k8sattributes.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None for the data; a collector restart to deploy.
- **What changes (numbers, delay, cost):** New attributes appear on new data only. k8sattributes enriches by pod IP or UID, so it works for telemetry sent by the pod itself, not for kube-state-metrics series (those describe other pods; use method 3).
- **Risk:** A fixed map goes stale. k8sattributes adds load on the Kubernetes API and needs RBAC.
- **How to verify:** Query the new attribute: `SELECT count(*) FROM Metric FACET k8s.node.name`. Compare the node list with `kubectl get nodes`.
- **Evidence:** Processor options read in the contrib READMEs (transformprocessor). k8sattributes not fetched or run.
- **Sources:** [github.com](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor), [docs.newrelic.com](https://docs.newrelic.com/docs/opentelemetry/get-started/collector-processing/opentelemetry-collector-processing-intro/)

*Collector: add the host name from a fixed map (small, stable fleets only)*

```yaml
processors:
  transform/hostname:
    metric_statements:
      - set(datapoint.attributes["nodename"], "web-1") where datapoint.attributes["instance"] == "10.0.0.11:9100"
      - set(datapoint.attributes["nodename"], "web-2") where datapoint.attributes["instance"] == "10.0.0.12:9100"
```

#### Method 5: Alerts: threshold on the raw series, add the joined label in the notification

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 30 minutes.

- **When to choose it:** Choose this when the join only provides a friendly name or owner for the message. The condition stays simple (FACET on the key); a workflow enrichment query adds the name when the notification is sent.
- **Prerequisites:** A workflow with enrichments enabled; the enrichment is an NRQL query run when the issue opens.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None for the condition; the enrichment runs when the notification is built.
- **What changes (numbers, delay, cost):** The condition cannot filter or group by the joined label. The notification carries the extra data as a query result. Routing by the joined label is not possible (see the routing blocker).
- **Risk:** Enrichment queries are single queries run once; if the join key is unknown to the enrichment the result is empty.
- **How to verify:** Use the workflow 'test' function in the UI or the NerdGraph test mutation against a past issue and read the rendered message.
- **Evidence:** Enrichment query accepted by New Relic 2026-11-10 (read-only). Enrichments themselves were not created.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/incident-workflows/), [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-workflows/)

*Condition: node load per instance (no join needed)* (condition query)

```nrql
SELECT latest(node_load1) FROM Metric FACET instance
```

*Workflow enrichment: host name for the instances (JOIN is allowed here, it is not a condition)*

```nrql
FROM Metric JOIN (FROM Metric SELECT latest(node_uname_info) WHERE metricName = 'node_uname_info' FACET instance, nodename LIMIT MAX) ON instance SELECT latest(node_load1) AS load1 WHERE metricName = 'node_load1' FACET instance, nodename LIMIT 20
```

### Dashboard variables that stay manual

**Measured:** 153 widgets on 9 dashboards wait for a variable that stayed manual; 21 variables use query_result() or another unreadable form (2026-11-10 test estate).

Most Grafana variables convert (label_values, custom, textbox, chained). The ones that do not are
`query_result()` of an expression, ad hoc filters, regexes with capture groups, `${var:csv}` formats, variables
inside a label name, and the interval and data source variables. A New Relic dashboard variable runs one NRQL
query (type nrql), shows a fixed list (enum) or takes free text (string); it is substituted whole into the
WHERE, FACET, SELECT or FROM of a widget, never into TIMESERIES, LIMIT, SINCE or COMPARE WITH. Every panel that
uses a manual variable is stuck until the variable exists, so this is where to start a dashboard.

**Decision tree**

- Is the Grafana variable a list of label values? Write it as a query variable with uniques() (method 1).
- Is it an ad hoc filter, or a group-by chooser? Use a text variable with LIKE, or an identifier variable after FACET (method 2).
- Does the list come from an expression (query_result) or a regex capture? Replace it with a static list generated once (method 3).
- Is the label missing from the New Relic data (cluster, environment, tenant)? Add it at ingest so the variable has something to list (method 4).
- Is it an interval, data source or `allValue` variable? Accept the difference (method 5).

**What to ask the customer**

- Open the Grafana variable: is it label_values, an expression, a regex, or free text? What does a user pick from it?
- Do users really use 'All' and multi-select, or always one value?
- Is the label present on the metric in New Relic? (`SELECT keyset() FROM Metric WHERE metricName = '<metric>'`)
- Is the New Relic plan one that supports nested variables (Pro, Enterprise, Core Compute)?

**What New Relic cannot do:** New Relic has no ad hoc filter variable, no variable in TIMESERIES, LIMIT, SINCE, COMPARE WITH or SLIDE BY (so no $__interval as a bucket), no text mixed with a variable (`"x-$job"`), and no 'allValue' regex.

**Ranked methods**

#### Method 1: A query variable with uniques(), used as IN ({{name}})

**Family:** C: Restructure. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for every label_values() style variable the converter could not read: write the list query by hand and tick multi-select. It is the direct counterpart and keeps the dashboard fully interactive.
- **Prerequisites:** The label exists on the metric. Nested variables (a child that uses {{parent}}) need Pro, Enterprise or Core Compute and a parent with a selection.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None; the list is refreshed when the dashboard loads (query variables ignore the time picker by default and read one hour).
- **What changes (numbers, delay, cost):** uniques() returns up to 5,000 values by default (10,000 at most), over the variable's window, not the 'latest sample'. Widgets must say `IN ({{name}})`, never `=`, when multi-select is on. Selecting 'All' is `["*"]`.
- **Risk:** A widget that uses `=` breaks when two values are picked. A child variable shows nothing until the parent has a value.
- **How to verify:** Run the list query alone and compare with Grafana's dropdown. Pick two values and check that both appear in the widget result.
- **Evidence:** Both queries accepted by New Relic 2026-11-10 with the variable replaced by a literal; the variable block follows the repository's own emitter and the Terraform provider schema.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/), [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/nested-variables/), [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-dashboards/)

*Variable query: label_values(kube_pod_info{cluster=~"$cluster"}, namespace)*

```nrql
SELECT uniques(namespace) FROM Metric WHERE metricName = 'kube_pod_info' AND cluster IN ({{cluster}})
```

*Widget that uses the multi-select variable*

```nrql
SELECT uniqueCount(pod) FROM Metric WHERE metricName = 'kube_pod_info' AND namespace IN ({{namespace}}) FACET node
```

*The same variable in Terraform (inside a newrelic_one_dashboard resource)*

```hcl
variable {
  name                 = "namespace"
  title                = "Namespace"
  type                 = "nrql"
  replacement_strategy = "default"
  default_values       = ["*"]
  is_multi_selection   = true

  nrql_query {
    account_ids = [var.account_id]
    query       = "SELECT uniques(namespace) FROM Metric WHERE metricName = 'kube_pod_info' AND cluster IN ({{cluster}})"
  }
}
```

#### Method 2: Ad hoc filters and group-by choosers: a text variable with LIKE, an identifier variable after FACET

**Family:** C: Restructure. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this when users typed a free filter ('pod name contains ...') or picked the label to group by. The two New Relic variable kinds that cover most of an ad hoc filter bar.
- **Prerequisites:** Text variables cannot be multi-select. An identifier variable lists attribute names (a list variable or a keyset() query).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** One free-text filter and one group-by instead of an open-ended filter bar. A text variable can hold a LIKE pattern such as %web%; an identifier variable is placed into FACET or SELECT as a name, not a value.
- **Risk:** A text variable with an unescaped quote breaks the query for that user only. Identifier variables must be restricted to a list of safe attribute names.
- **How to verify:** Type a pattern that matches nothing and one that matches everything; pick each group-by value in turn.
- **Evidence:** Both queries accepted 2026-11-10 after replacing the variables by literals; the replacement strategies are described on the variables page.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/)

*Text variable 'podfilter' (value %web%)*

```nrql
SELECT latest(kube_pod_info) FROM Metric WHERE metricName = 'kube_pod_info' AND pod LIKE {{podfilter}} FACET pod, node LIMIT 50
```

*Identifier variable 'groupby' (list: node, namespace, cluster) chosen after FACET*

```nrql
SELECT uniqueCount(pod) FROM Metric WHERE metricName = 'kube_pod_info' FACET {{groupby}} LIMIT 50
```

#### Method 3: Replace an unreadable variable (query_result, regex capture) by a generated static list

**Family:** C: Restructure. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this when the Grafana variable is computed by an expression (`query_result(topk(5, ...))`) or by a regex that New Relic cannot run, and the list changes rarely. The list is generated once from the data and pasted in as an enum.
- **Prerequisites:** A one-time NRQL query to list the values; a person to refresh the list when it goes stale.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The list is frozen at generation time (a new namespace does not appear until someone adds it). Computed lists (top 5 by CPU) become fixed lists; if the ranking matters, put the ranking in a table widget instead.
- **Risk:** Stale list. Document the refresh in the dashboard description and put a calendar reminder on it.
- **How to verify:** Compare the generated list with the Grafana dropdown at the same moment; open the dashboard as a user who has never seen it.
- **Evidence:** Generator query accepted 2026-11-10; the Terraform enum block follows the repository emitter.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/), [registry.terraform.io](https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/one_dashboard)

*Generate the values once (copy the output into the enum)*

```nrql
SELECT uniques(namespace, 200) FROM Metric WHERE metricName = 'kube_pod_info'
```

*Enum variable in Terraform*

```hcl
variable {
  name                 = "namespace"
  title                = "Namespace"
  type                 = "enum"
  replacement_strategy = "default"
  default_values       = ["web"]
  is_multi_selection   = true

  item { value = "web" }
  item { value = "payments" }
  item { value = "monitoring" }
}
```

#### Method 4: Add the missing label at ingest (cluster, environment) so the variable has something to list

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 hour.

- **When to choose it:** Choose this when the Grafana variable filters on a label that Prometheus adds at query time or through external_labels (cluster, region, tenant) and the label is not on the series in New Relic. Without it no variable query can list the values.
- **Prerequisites:** Access to the Prometheus remote_write block, or the Alloy / collector pipeline.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None; applies from the next scrape.
- **What changes (numbers, delay, cost):** New data carries the label; old data does not (the variable lists only periods after the change). One more attribute per point has no per-point price in New Relic (cost follows bytes ingested), but raises cardinality only if the value varies.
- **Risk:** Label name collisions with attributes New Relic uses (metricName, entity.*). A relabel rule that matches too widely rewrites every metric.
- **How to verify:** After 5 minutes: `SELECT uniques(cluster) FROM Metric WHERE metricName = 'up'` lists the new value. Then refresh the dashboard variable.
- **Evidence:** write_relabel_configs read on the New Relic remote write page; the Alloy block follows the prometheus.relabel reference. Not run.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/), [grafana.com](https://grafana.com/docs/alloy/latest/reference/components/prometheus/prometheus.relabel/)

*Prometheus remote_write: stamp every series with the cluster name*

```yaml
remote_write:
  - url: https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=prod-a
    authorization:
      credentials_file: /etc/prometheus/nr-license-key
    write_relabel_configs:
      - target_label: cluster
        replacement: prod-a
```

*Grafana Alloy: the same with prometheus.relabel*

```text
prometheus.relabel "stamp" {
  forward_to = [prometheus.remote_write.newrelic.receiver]
  rule {
    target_label = "cluster"
    replacement  = "prod-a"
  }
}
```

#### Method 5: Accept: interval, data source and allValue variables disappear

**Family:** F: Accept and document the difference. **Status:** VERIFIED-VALID. **Effort:** 5 minutes.

- **When to choose it:** Choose this for $__interval, $__rate_interval, data source pickers and Grafana's allValue regex. There is nothing to convert; the dashboard behaves differently on purpose.
- **Prerequisites:** Tell the dashboard owners before cut-over.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** TIMESERIES AUTO picks the bucket from the time picker (no user control, and it ignores the scrape interval). One account is queried. 'All' means ["*"]; the allValue regex is ignored.
- **Risk:** Rate charts can swing if the automatic bucket is not a multiple of the scrape interval; fix the bucket in the widget (TIMESERIES 1 minute) when it matters.
- **How to verify:** Open the migrated chart at two time ranges and check that the rate stays level (not alternating).
- **Evidence:** Fixed-bucket query accepted 2026-11-10. The swing was measured earlier (docs/research/live-grafana-counter-roundtrip.md: 5 s buckets on 10 s data alternate 0 and 2.4 around 1.2).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/)

*Fix the bucket to a multiple of the scrape interval when the automatic one swings*

```nrql
SELECT rate(sum(nginx_ingress_controller_requests), 1 second) FROM Metric FACET ingress TIMESERIES 1 minute
```

### without() grouping

**Measured:** 67 widgets on 3 dashboards (Vault, Node Exporter variants, kube-state); 1 of 55 alert rules (2026-11-10 test estate).

`sum without (cpu) (rate(node_cpu_seconds_total[5m]))` means 'group by every label except cpu'. NRQL can
only say which labels to keep (FACET a, b, c, up to 5), never which to drop, so the converter needs the full label
list of the series. `SELECT keyset()` gives that list; the rest is a decision about which labels matter.
Dropping a label in the pipeline looks like a shortcut but is wrong unless the pipeline also adds the values up.

**Decision tree**

- Dashboard and 5 or fewer labels remain after removing the dropped ones? Write them in FACET (method 1).
- More than 5 remain, or users want to choose the grouping? Keep the 2-3 labels that matter, or use an identifier variable (method 2).
- The panel is hot or the label set is huge? Aggregate at the source (method 3) with a recording rule or aggregate_on_attributes, NOT a plain labeldrop.
- Alert? FACET on the identifying labels; each facet is its own signal (method 4).

**What to ask the customer**

- Which labels does the metric have and which of them does anyone use to read the chart? (keyset)
- Is the dropped label high-cardinality (cpu, pod, container id) and the remaining set small?
- Do you own the scrape and remote write configuration?

**What New Relic cannot do:** NRQL cannot express 'all labels except'. FACET takes at most 5 attributes, so a metric whose useful identity needs 6 or more labels cannot be grouped exactly in one query.

**Ranked methods**

#### Method 1: List the remaining labels in FACET (found with keyset())

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 15 minutes.

- **When to choose it:** Choose this when the remaining labels are 5 or fewer. Step 1 finds the labels; step 2 writes them into FACET. This is the faithful translation.
- **Prerequisites:** keyset() on the metric to see the label list. Constant labels (job, prometheus_server) can be left out because they do not split anything.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Numbers equal Prometheus when every label that varies is in FACET; labels left out are merged (summed), which is what without() would have done only for the dropped label.
- **Risk:** Leaving out a label that varies merges series that Prometheus kept apart (under-reading each group). More than 5 labels cannot be written.
- **How to verify:** Add up the FACET rows: the total must equal the same query without FACET. Compare one row with the Grafana legend value.
- **Evidence:** Run 2026-11-10: sum of the per-(instance, mode) rates equals the unfaceted total rate on node_cpu_seconds_total.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Step 1: which labels does the metric have?*

```nrql
SELECT keyset() FROM Metric WHERE metricName = 'node_cpu_seconds_total'
```

*Step 2: sum without (cpu) (rate(node_cpu_seconds_total[5m]))  keeps instance and mode*

```nrql
SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric FACET instance, mode LIMIT MAX TIMESERIES 5 minutes
```

#### Method 2: Let the user choose the grouping (identifier variable) or keep the two labels that matter

**Family:** C: Restructure. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this when more than 5 labels remain, or when nobody reads more than two of them. Grafana's without() was usually a convenience, not a requirement.
- **Prerequisites:** A short list of attribute names for the variable.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The chart shows the grouping the user picked, not 'everything except cpu'. Series that differ only in labels outside the grouping are summed.
- **Risk:** Hidden merges: say in the widget title which labels are summed together.
- **How to verify:** Switch the variable through every value; the grand total must not change.
- **Evidence:** Accepted 2026-11-10 with the variable replaced by a literal.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/)

*Identifier variable 'by' (list: mode, cpu, instance)*

```nrql
SELECT rate(sum(node_cpu_seconds_total), 1 second) FROM Metric FACET {{by}} LIMIT MAX TIMESERIES 5 minutes
```

#### Method 3: Drop the label at the source by aggregating (not by labeldrop)

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1-2 hours.

- **When to choose it:** Choose this when the label is useless downstream (per-cpu, per-pod-uid) and the volume matters. The aggregation must add the values; a plain labeldrop creates duplicate series with the same labels and the backend keeps one sample, losing data.
- **Prerequisites:** Prometheus rule files or an OpenTelemetry Collector; a decision whether to keep the raw series.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule interval.
- **What changes (numbers, delay, cost):** Cuts the series count and the ingest bill by the dropped label's cardinality. History keeps the old shape. The aggregate is a rate or a sum computed outside New Relic.
- **Risk:** labeldrop without aggregation collapses series silently: duplicates are rejected or one value wins. Test on one metric first.
- **How to verify:** Compare `SELECT rate(sum(raw), 1 second)` on the raw series with `SELECT latest(recorded)` on the new series: equal within the rule interval.
- **Evidence:** Syntax read in the Prometheus rules page and the transformprocessor README (aggregate_on_attributes). Not run. The NRQL form was accepted but the recorded series does not exist in the sandbox.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/), [github.com](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/transformprocessor)

*Prometheus recording rule: the exact sum without (cpu)(rate(...))*

```yaml
groups:
  - name: cpu
    rules:
      - record: instance_mode:node_cpu_seconds:rate5m
        expr: sum without (cpu) (rate(node_cpu_seconds_total[5m]))
```

*OpenTelemetry Collector: keep instance and mode, add the rest up*

```yaml
processors:
  transform/no_cpu:
    metric_statements:
      - aggregate_on_attributes("sum", ["instance", "mode"]) where metric.name == "node_cpu_seconds_total"
```

*Chart the recorded series*

```nrql
SELECT latest(`instance_mode:node_cpu_seconds:rate5m`) FROM Metric FACET instance, mode TIMESERIES 5 minutes
```

#### Method 4: Alerts: FACET on the identifying labels; every facet is its own signal

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for an alert rule with without(). A condition cannot use 'all except', but a faceted condition evaluates each combination separately, which is what the Grafana rule did per series.
- **Prerequisites:** At most 5 identifying labels; at most 20,000 facet values; no LIMIT in a condition.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay.
- **What changes (numbers, delay, cost):** Each facet is a separate signal and opens its own alert event. Labels you leave out are summed or averaged by the function you use.
- **Risk:** Alert storm if the FACET has many values; set a threshold duration and group in the workflow.
- **How to verify:** Preview the condition: the number of lines equals the number of series Grafana evaluated.
- **Evidence:** Accepted 2026-11-10 (read-only). Not created as a condition.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Per-instance load, without caring about the other labels* (condition query)

```nrql
SELECT average(node_load1) FROM Metric FACET instance
```

### Counters read without rate()

**Measured:** 66 widgets on 16 dashboards; 4 alert rules with rate() but no sum() (2026-11-10 test estate).

Prometheus counters only ever grow; Grafana panels show them through rate()/increase(), and a few show the raw
running total. New Relic stores a Prometheus counter as a delta: each point is the increase since the previous
scrape. Remote write decides this from the name: `_total`, `_count` and `_bucket` become 'count' metrics, `_sum` a
summary, everything else a gauge. So `sum(x)` per bucket already IS the increase, `rate(sum(x), 1 second)` is the
per-second rate, and the raw running total cannot be drawn. A counter without one of those suffixes is stored as a
gauge, and then rate(sum()) is wrong.

**Decision tree**

- Panel wants a per-second rate? rate(sum(x), 1 second) (method 1).
- Panel wants 'how many in this period' (stat or bar)? sum(x) over the picker window (method 2).
- The metric name has no _total/_count/_bucket suffix? Tell remote write it is a counter (method 3).
- Data arrives over OTLP with cumulative temporality? Convert to delta in the collector (method 4).
- Alert on a counter? Use sum() per window or a ratio, not rate() (method 5).
- Someone truly needs the raw running total? There is no way to draw it from deltas (method 6).

**What to ask the customer**

- Is the panel a rate, an increase or the raw value?
- How does the data reach New Relic: Prometheus remote write, OTLP, or a New Relic integration?
- Does the metric name end in _total, _count or _bucket? If not, is it still a counter?

**What New Relic cannot do:** A raw, ever-growing counter line cannot be drawn from delta data: there is no cumulative-sum function in NRQL (none in the NRQL function reference).

**Ranked methods**

#### Method 1: rate(sum(x), 1 second) for the per-second rate

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 10 minutes.

- **When to choose it:** Choose this for every `rate()`, `irate()` and `increase()/seconds` panel on a counter that remote write recognised. It is the baseline translation New Relic itself uses and was measured exact against the sent data.
- **Prerequisites:** The metric is stored as a count (name ends in _total, _count or _bucket, or was tagged by newrelic_metric_type).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Per bucket, exact when the bucket is a multiple of the scrape interval; otherwise the chart swings around the right mean (5 s buckets on 10 s data alternate 0 and 2.4 around 1.2). `irate` becomes the same average rate, not the last two samples. A stat divides by the window, so a window longer than the data lowers the value.
- **Risk:** A counter stored as a gauge (no suffix) gives a rate that is too high by the number of scrapes. A bucket shorter than the scrape interval swings.
- **How to verify:** Over a window with data: sum of the per-bucket sums must equal sum() for the whole window (method 2). Compare with Grafana at the same step. See docs/research/live-grafana-counter-roundtrip.md for the measured match.
- **Evidence:** Run 2026-11-10: sum over 1 day equals the sum of the 1-hour bucket sums for nginx_ingress_controller_requests. Earlier live round trip (2026-11-10, counter-roundtrip note) matched rate and increase to the arithmetic.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/), [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*sum by (ingress) (rate(nginx_ingress_controller_requests[5m]))*

```nrql
SELECT rate(sum(nginx_ingress_controller_requests), 1 second) FROM Metric FACET ingress LIMIT MAX TIMESERIES 5 minutes
```

*Error ratio from two counters*

```nrql
SELECT filter(sum(nginx_ingress_controller_requests), WHERE status LIKE '5%') / sum(nginx_ingress_controller_requests) * 100 AS 'error percent' FROM Metric FACET ingress TIMESERIES 5 minutes
```

#### Method 2: sum(x) for 'how many in the period' (increase, stat of a counter)

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALUE. **Effort:** 5 minutes.

- **When to choose it:** Choose this for stat, bar and table widgets that show the increase over the dashboard time range: `increase(x[$__range])` and 'total requests today'.
- **Prerequisites:** The metric is a delta count in New Relic.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Exactly the increase seen in the range (Prometheus extrapolates at the edges; New Relic does not). `rate(sum(x), 1 hour)` is NOT the same: it scales the window's rate to an hour (measured 4,386 for 720 events seen).
- **Risk:** Choosing rate(sum(), 1 hour) by mistake overstates a short window.
- **How to verify:** `SELECT sum(x)` for the range equals the sum of its TIMESERIES buckets.
- **Evidence:** Run 2026-11-10: the total over 1 day equals the sum of the hourly buckets (nginx_ingress_controller_requests).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*increase(http_server_requests_seconds_count[$__range]) by (status)*

```nrql
SELECT sum(http_server_requests_seconds_count) FROM Metric FACET status LIMIT MAX
```

#### Method 3: Tell remote write that an oddly named metric is a counter

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this when the counter has no _total, _count or _bucket suffix (exporters with legacy names) and so was stored as a gauge. The rate or increase of such a metric is wrong until it is tagged.
- **Prerequisites:** Access to the Prometheus remote_write block. New data only.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None; applies from the next remote write batch.
- **What changes (numbers, delay, cost):** New points are stored as count (delta). Old points stay gauge: charts that span the change are wrong around it; start comparison after the change. The label is removed by New Relic on receipt.
- **Risk:** A regex that also matches real gauges turns them into counters and their values into deltas.
- **How to verify:** After the change, `SELECT min(x), max(x) FROM Metric WHERE metricName = '<name>'`: a delta counter has small values and many zeros; a gauge shows the running total.
- **Evidence:** The newrelic_metric_type override and the suffix rule are quoted from the New Relic remote write page (read 2026-11-10). Not run.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/)

*Prometheus remote_write: declare process_cpu_seconds as a counter (example of a name without a recognised suffix)*

```yaml
remote_write:
  - url: https://metric-api.newrelic.com/prometheus/v1/write?prometheus_server=prod-a
    authorization:
      credentials_file: /etc/prometheus/nr-license-key
    write_relabel_configs:
      - source_labels: [__name__]
        regex: "^(legacy_events_processed|node_network_receive_bytes)$"
        target_label: newrelic_metric_type
        replacement: counter
        action: replace
```

#### Method 4: OTLP sources: convert cumulative counters to delta in the collector

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 hour.

- **When to choose it:** Choose this when the counters come in over OpenTelemetry (not Prometheus remote write) with cumulative temporality. New Relic prefers delta; cumulative works but costs more memory and ingest.
- **Prerequisites:** An OpenTelemetry Collector (contrib build) in the path; SDKs can instead set OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None; the first point after a restart is dropped or kept depending on initial_value.
- **What changes (numbers, delay, cost):** Stateful: the processor remembers the last value per series, so a restart loses one interval (initial_value controls it). Monotonic sums only; gauges and non-monotonic sums are left alone.
- **Risk:** The collector must see every point of a series (do not load-balance one series across replicas). State grows with cardinality; max_staleness trims it.
- **How to verify:** Compare `SELECT sum(x)` over 10 minutes with the increase of the source counter over the same 10 minutes.
- **Evidence:** Options read in the cumulativetodeltaprocessor README and the New Relic OTLP page (read 2026-11-10). Not run.
- **Sources:** [github.com](https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/processor/cumulativetodeltaprocessor), [docs.newrelic.com](https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/)

*Collector: convert every monotonic sum to delta before export*

```yaml
processors:
  cumulativetodelta:
    include:
      metric_types: [sum]
    initial_value: auto
    max_staleness: 1h
service:
  pipelines:
    metrics:
      receivers: [otlp]
      processors: [cumulativetodelta, batch]
      exporters: [otlphttp/newrelic]
```

#### Method 5: Alerts on counters: sum() per window, or a ratio of counters

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for every counter-based alert. `rate()` is not in the documented list of functions allowed in a condition, so avoid it; a sum per aggregation window is the same signal with a threshold in 'events per window'.
- **Prerequisites:** A window that matches the Grafana rate window (aggregation_window 60 to 300 seconds).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay.
- **What changes (numbers, delay, cost):** The threshold is in events per window, not per second: multiply the per-second threshold by the window length. Per-series tests (each pod alone) use FACET; a total hides one bad series.
- **Risk:** Using sum() across series when Prometheus tested each series on its own (the converter refuses these on purpose).
- **How to verify:** Preview the condition and compare its line with `rate(...)*window` in Grafana for the same hour.
- **Evidence:** Accepted 2026-11-10. Whether rate() is accepted inside a condition is UNVERIFIED (not in the condition page's function list).
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Requests per window for each ingress (1 minute window; threshold 'above 600 for 5 minutes' = 10 per second)* (condition query)

```nrql
SELECT sum(nginx_ingress_controller_requests) FROM Metric FACET ingress
```

*5xx percentage per ingress* (condition query)

```nrql
SELECT filter(sum(nginx_ingress_controller_requests), WHERE status LIKE '5%') / sum(nginx_ingress_controller_requests) * 100 FROM Metric FACET ingress
```

#### Method 6: Accept: the raw running total cannot be drawn

**Family:** F: Accept and document the difference. **Status:** VERIFIED-VALID. **Effort:** 5 minutes.

- **When to choose it:** Choose this when someone insists on the ever-growing line (a counter plotted directly). There is no cumulative-sum function in NRQL; the delta storage has thrown the running total away.
- **Prerequisites:** Agree with the dashboard owner that rate or increase is the useful view.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The line becomes a rate or a per-bucket increase. The only way to keep a running total is to store the metric as a gauge (newrelic_metric_type = gauge), which gives up rate(sum()) for that metric; use derivative(x, 1 second) instead.
- **Risk:** Mixed storage: points before the change are deltas, after it cumulative; do not chart across the change.
- **How to verify:** Show the owner the rate chart next to the old panel at one incident.
- **Evidence:** derivative() form accepted 2026-11-10; the gauge override value is listed on the remote write page. The absence of a cumulative function was checked in the NRQL reference (no hits for 'cumulativeSum').
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/), [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/)

*If the counter was stored as a gauge on purpose: rate from the cumulative value*

```nrql
SELECT derivative(process_cpu_seconds_total, 1 second) FROM Metric TIMESERIES 5 minutes
```

### _over_time functions (avg_over_time, max_over_time, ...)

**Measured:** 44 widgets on 10 dashboards; 3 alert rules (2026-11-10 test estate).

`avg_over_time(x[15m])` is a rolling aggregate: at every point, the average of the last 15 minutes. NRQL has the
same thing as a sliding window: the TIMESERIES size is the window and SLIDE BY is the step. Most _over_time
functions have a direct aggregate (average, max, min, sum, count, latest, stddev, percentile). Only changes(),
resets() and quantile-of-a-counter have no counterpart.

**Decision tree**

- Chart with a rolling window (avg_over_time[15m] drawn every minute)? Sliding window (method 1).
- Single number over the last N minutes? A stat with its own SINCE (method 2), remembering it ignores the time picker.
- Alert? Aggregation window plus slide_by in the condition (method 3).
- Heavy panel or must equal Prometheus exactly? Compute at the source (method 4).
- changes(), resets(), deriv-style functions? Check method 5.

**What to ask the customer**

- Is the window in the PromQL the same as the dashboard step, or longer (a rolling window)?
- Does the panel need to follow the time picker, or is the window fixed (always the last hour)?

**What New Relic cannot do:** changes() and resets() have no NRQL counterpart (New Relic's own translator rejects changes() with 'not currently supported').

**Ranked methods**

#### Method 1: Sliding window: TIMESERIES <window> SLIDE BY <step>

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for any rolling _over_time chart. Mapping: avg_over_time to average, max_over_time to max, min_over_time to min, sum_over_time to sum, count_over_time to count, last_over_time to latest, stddev_over_time to stddev, quantile_over_time(q) to percentile(x, 100*q).
- **Prerequisites:** A window that is a whole multiple of the step; the step must divide the window.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Each point averages the last window of samples, as Prometheus does. Points are weighted by sample count, not by time, so uneven scrapes weigh differently. SLIDE BY multiplies the number of points returned and counts against the 366-bucket limit.
- **Risk:** Too small a step on a long range exceeds the bucket limit.
- **How to verify:** A non-sliding query with the same window size must give the same value at the points where the windows coincide.
- **Evidence:** Queries accepted by New Relic 2026-11-10. Values were not compared with Prometheus.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*avg_over_time(system_cpu_usage[15m]) drawn every minute*

```nrql
SELECT average(system_cpu_usage) FROM Metric FACET application TIMESERIES 15 minutes SLIDE BY 1 minute
```

*max_over_time and quantile_over_time(0.95, ...)*

```nrql
SELECT max(system_cpu_usage), percentile(system_cpu_usage, 95) FROM Metric TIMESERIES 15 minutes SLIDE BY 5 minutes
```

#### Method 2: Stat with its own window (SINCE 15 minutes ago)

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 5 minutes.

- **When to choose it:** Choose this for billboard and table widgets that always show the last N minutes.
- **Prerequisites:** Accept that the widget ignores the dashboard time picker.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** A SINCE in the widget overrides the time picker. Matches Prometheus at the moment of the query.
- **Risk:** Users change the picker and see no change.
- **How to verify:** Compare with Grafana's stat at the same moment.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*max_over_time(jvm_memory_used_bytes[15m]) by application*

```nrql
SELECT max(jvm_memory_used_bytes) FROM Metric FACET application SINCE 15 minutes ago
```

#### Method 3: Alerts: aggregation window with slide_by

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for alerts on avg/max_over_time. The condition's aggregation window is the range; slide_by evaluates it more often.
- **Prerequisites:** slide_by must divide the window and be at least 30 s when the window is an hour or less.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay; sliding windows build a buffer first, so nothing opens until one full window has passed.
- **What changes (numbers, delay, cost):** Prometheus 'for: 10m' on top of avg_over_time[15m] is two time layers; New Relic has the window and the threshold duration. Keep the window and set the duration to the Grafana pending period.
- **Risk:** Any edit resets evaluation and the condition cannot fire until a full window has passed.
- **How to verify:** Preview, then run the firing test on a throwaway policy.
- **Evidence:** Query accepted 2026-11-10; Terraform attribute names from the provider schema recorded in the notifications reference note. Condition not created.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Condition query for avg_over_time(system_cpu_usage[15m]) > 0.8* (condition query)

```nrql
SELECT average(system_cpu_usage) FROM Metric FACET application
```

*Window settings in Terraform*

```hcl
aggregation_window = 900
slide_by           = 60
aggregation_method = "event_flow"
aggregation_delay  = 120
```

#### Method 4: Compute the range function in Prometheus and ship the result

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 hour.

- **When to choose it:** Choose this when numbers must equal Prometheus exactly, for offset comparisons, or when the panel is heavy.
- **Prerequisites:** Prometheus rule files.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule interval.
- **What changes (numbers, delay, cost):** New series with the rolled-up value; exact Prometheus numbers.
- **Risk:** More series to pay for.
- **How to verify:** Chart the new series next to the NRQL sliding window; they should agree.
- **Evidence:** Rule syntax read in the Prometheus documentation. Not run.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)

*Recording rule for the rolling average*

```yaml
groups:
  - name: rolling
    rules:
      - record: application:system_cpu_usage:avg_over_time15m
        expr: avg_over_time(system_cpu_usage[15m])
```

#### Method 5: Accept: changes() and resets() have no counterpart

**Family:** F: Accept and document the difference. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this when the panel counts value changes or counter resets.
- **Prerequisites:** Agree on a proxy with the owner.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** uniqueCount(x) approximates 'how many distinct values'; it is not a change count. For counter resets, count zero-valued delta samples if the exporter reports restarts that way.
- **Risk:** A proxy that looks right and is not.
- **How to verify:** Check against Grafana during a known restart.
- **Evidence:** Proxy query accepted 2026-11-10. The lack of changes() is from the PromQL baseline note.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/view-query-data/translate-promql-queries-nrql/)

*Distinct values seen (a proxy, not changes())*

```nrql
SELECT uniqueCount(jvm_threads_live_threads) FROM Metric FACET application
```

### Panel transformations

**Measured:** 40 widgets on 19 dashboards (2026-11-10 test estate).

Grafana transformations reshape the result in the browser. About half have an NRQL form: filter by value,
group by, sort, limit, rename, calculate a field, merge two queries by a key, rows to columns. The rest
(config from query, partition by values, time formatting) have none.

**Decision tree**

- Merge or join by field? One query with several SELECT items faceted by the key (method 1).
- Rows to columns? filter() per column (method 2).
- Filter by value or reduce? Nested query with WHERE (method 3).
- Anything else? Split the panel or accept (methods 4 and 5).

**What to ask the customer**

- Which transformations are on the panel, in order?
- Does the panel need all of them or just the final look?

**What New Relic cannot do:** Config from query results, partition by values and join of different time grains have no NRQL equivalent.

**Ranked methods**

#### Method 1: Merge by key: several metrics in one SELECT, faceted by the key

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for 'join by field' and 'merge' on metrics that share a label.
- **Prerequisites:** All metrics are in the Metric event type with the shared key.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Calculated fields are plain math; AS names the columns. Each metric is an aggregate of its own samples.
- **Risk:** A key missing on one metric yields nulls.
- **How to verify:** Run each metric alone and compare the columns.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Memory table: two metrics merged by instance, with a calculated column*

```nrql
SELECT latest(node_memory_MemTotal_bytes) AS total, latest(node_memory_MemAvailable_bytes) AS available, (1 - latest(node_memory_MemAvailable_bytes) / latest(node_memory_MemTotal_bytes)) * 100 AS 'used percent' FROM Metric FACET instance
```

#### Method 2: Rows to columns with filter()

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for pivot-like transformations such as one column per CPU mode.
- **Prerequisites:** A short, known list of values.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The column list is fixed in the query; a new value needs a new column.
- **Risk:** New label values are not shown.
- **How to verify:** Row total equals the unfiltered sum.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*CPU time per instance with a column per mode*

```nrql
SELECT filter(sum(node_cpu_seconds_total), WHERE mode = 'user') AS user, filter(sum(node_cpu_seconds_total), WHERE mode = 'system') AS system, filter(sum(node_cpu_seconds_total), WHERE mode = 'idle') AS idle FROM Metric FACET instance
```

#### Method 3: Filter by value, sort, limit: nested WHERE, ORDER BY, LIMIT

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for 'filter data by values', 'sort by' and 'limit' transformations.
- **Prerequisites:** None.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** The filter acts on the aggregated value of the nested query.
- **Risk:** Top N with LIMIT is chosen by the first aggregate.
- **How to verify:** Row count and order equal the Grafana table.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Instances with load above 1, busiest first, top 5*

```nrql
SELECT * FROM (SELECT average(node_load1) AS load FROM Metric FACET instance LIMIT MAX) WHERE load > 1 LIMIT 5
```

#### Method 4: Split the panel: one widget per query, link them with a variable

**Family:** C: Restructure. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this for transformations that combine queries with different groupings.
- **Prerequisites:** A shared variable.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Two widgets instead of one table.
- **Risk:** Layout drift.
- **How to verify:** Pick the same value in both widgets.
- **Evidence:** Design advice; no query specific to it.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/query-your-data/explore-query-data/dashboards/dashboard-template-variables/)

*Plan*

```text
Widget 1: totals per instance. Widget 2: detail for {{instance}}. Both read the same dashboard variable.
```

#### Method 5: Accept: no equivalent for config-from-query, partition and time formatting

**Family:** F: Accept and document the difference. **Status:** DOC-ONLY. **Effort:** 5 minutes.

- **When to choose it:** Choose this when the transformation only changes looks.
- **Prerequisites:** Owner agrees.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Data is the same; presentation differs.
- **Risk:** Low.
- **How to verify:** Owner signs off a screenshot.
- **Evidence:** Absence of an NRQL form per the converter notes.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Wording*

```text
This panel's browser-side transformation is not reproduced; values are unchanged.
```

### Heatmaps and histogram_quantile panels

**Measured:** 22 heatmap widgets on 5 dashboards; histogram_quantile panels (2026-11-10 test estate).

Prometheus classic histograms are counters per `le` bucket. New Relic has bucketPercentile() for quantiles, and the
bucket counters can be shown per bucket with filter() arithmetic. A heatmap chart exists for histogram() output.

**Decision tree**

- Quantile panel? bucketPercentile (method 1).
- Distribution bars? Per-bucket arithmetic (method 2).
- True heatmap? Send as distribution (method 3).

**What to ask the customer**

- Is the panel a quantile or a distribution?
- Can the app send OpenTelemetry histograms?

**What New Relic cannot do:** A heatmap from classic `le` bucket counters is not drawn natively; histogram() needs raw values or distributions.

**Ranked methods**

#### Method 1: bucketPercentile() for histogram_quantile

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for quantile panels on Prometheus _bucket data.
- **Prerequisites:** Metric ends in _bucket with le.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Reports the percentile (quantile times 100) along bucket boundaries.
- **Risk:** Interpolation may differ slightly from Prometheus.
- **How to verify:** Compare with Grafana for one ingress and hour.
- **Evidence:** Accepted 2026-11-10; the function is documented as the histogram_quantile equivalent.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*p50, p95, p99 per ingress*

```nrql
SELECT bucketPercentile(nginx_ingress_controller_request_duration_seconds_bucket, 50, 95, 99) FROM Metric FACET ingress TIMESERIES 10 minutes
```

#### Method 2: Per-bucket counts with filter() differences

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for bar charts of how many requests fell in each latency band.
- **Prerequisites:** Know the le values.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Buckets are cumulative; differences give bands.
- **Risk:** le may be written 1 and 1.0 by different exporters.
- **How to verify:** Bands sum to the +Inf total.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Requests under 100 ms, 100-500 ms, over 500 ms*

```nrql
SELECT filter(sum(http_server_requests_seconds_bucket), WHERE le = '0.1') AS 'under 100ms', filter(sum(http_server_requests_seconds_bucket), WHERE le = '0.5') - filter(sum(http_server_requests_seconds_bucket), WHERE le = '0.1') AS '100-500ms', filter(sum(http_server_requests_seconds_bucket), WHERE le = '+Inf') - filter(sum(http_server_requests_seconds_bucket), WHERE le = '0.5') AS 'over 500ms' FROM Metric TIMESERIES 10 minutes
```

#### Method 3: Send OpenTelemetry histograms so histogram() and percentile() work

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 day.

- **When to choose it:** Choose this for real heatmaps.
- **Prerequisites:** Collector or SDK access.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None.
- **What changes (numbers, delay, cost):** New metric names; history stays classic.
- **Risk:** Two parallel series during migration.
- **How to verify:** percentile on the new metric matches bucketPercentile on the old.
- **Evidence:** Read in the New Relic OTLP page. Not run.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/opentelemetry/best-practices/opentelemetry-otlp/)

*SDK setting*

```text
OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE=delta
```

### absent() and missing series

**Measured:** 1 alert rule converted, 2 more use absent-style tests (2026-11-10 test estate).

absent(x) fires when no series matches. New Relic cannot count rows that do not exist, so an empty window gives
NULL, never 0. The answer is loss of signal: a condition with an expiration duration opens an event when no data
arrives for that long. It only works for a signal that existed before (it needs a first data point), and in a
faceted query each facet is its own signal, which gives per-target absence.

**Decision tree**

- One metric must keep arriving? Loss of signal on a count(*) query (method 1).
- Prometheus targets? Alert on `up` below 1 per instance (method 2).
- Detect one missing member of a fleet? FACET plus loss of signal (method 3).
- Host or service that New Relic already monitors natively? Use its not-reporting condition (method 4).
- A target that never existed cannot be detected (method 5).

**What to ask the customer**

- How long may the series be silent before it matters?
- Is there a list of expected targets?
- Does the scrape itself report `up`?

**What New Relic cannot do:** New Relic cannot alert on something that has never reported. Loss of signal needs a first data point, and it does not work with nested aggregation or subqueries.

**Ranked methods**

#### Method 1: Loss of signal on a count(*) condition

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for a heartbeat metric.
- **Prerequisites:** The metric has reported at least once after the condition is enabled.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Expiration duration (30 s to 48 h; 3-5 minutes recommended), measured from arrival time.
- **What changes (numbers, delay, cost):** absent() looks at one evaluation; loss of signal looks at time since the last point arrived. Do not wrap the count in filter(): the NULL is the signal.
- **Risk:** Enabling the condition while the data is already gone detects nothing.
- **How to verify:** Stop the sender of a test metric (ckg_flaky_heartbeat style) and wait for the expiration.
- **Evidence:** Query accepted 2026-11-10 against ckg_flaky_heartbeat; Terraform attributes from the provider schema and the NRQL conditions page. Condition not created.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/), [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-loss-signal-gap-filling/)

*Heartbeat query* (condition query)

```nrql
SELECT count(*) FROM Metric WHERE metricName = 'ckg_flaky_heartbeat'
```

*Terraform condition*

```hcl
resource "newrelic_nrql_alert_condition" "heartbeat_absent" {
  account_id                   = var.account_id
  policy_id                    = newrelic_alert_policy.p.id
  type                         = "static"
  name                         = "heartbeat absent"
  enabled                      = false
  expiration_duration          = 300
  open_violation_on_expiration = true

  nrql {
    query = "SELECT count(*) FROM Metric WHERE metricName = 'ckg_flaky_heartbeat'"
  }

  critical {
    operator              = "below"
    threshold             = 1
    threshold_duration    = 300
    threshold_occurrences = "all"
  }
}
```

#### Method 2: Alert on up below 1 per target

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for Prometheus-scraped targets; the scrape reports its own failure.
- **Prerequisites:** `up` is remote-written.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay.
- **What changes (numbers, delay, cost):** Detects a failing scrape immediately, not only silence.
- **Risk:** If the whole Prometheus stops, up stops too: pair with method 1.
- **How to verify:** Stop an exporter and watch the value drop to 0.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*up per instance* (condition query)

```nrql
SELECT latest(up) FROM Metric FACET job, instance
```

#### Method 3: Faceted loss of signal: one signal per member

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this to catch one missing instance while others report.
- **Prerequisites:** Each member reported before.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Expiration duration.
- **What changes (numbers, delay, cost):** Every facet value is a separate signal; no LIMIT allowed.
- **Risk:** Decommissioned instances alert; use close_violations_on_expiration for ephemeral ones.
- **How to verify:** Remove one target and check one event opens.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Per-instance heartbeat* (condition query)

```nrql
SELECT count(*) FROM Metric WHERE metricName = 'up' FACET instance
```

#### Method 4: Use the native not-reporting condition for monitored hosts and services

**Family:** E: Use a New Relic native feature. **Status:** DOC-ONLY. **Effort:** 15 minutes.

- **When to choose it:** Choose this when the entity is already monitored by an agent or integration.
- **Prerequisites:** Agent installed.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Per the condition.
- **What changes (numbers, delay, cost):** Entity-based, tied to the entity's health status.
- **Risk:** Only for entities New Relic monitors natively.
- **How to verify:** Stop the agent in a test host.
- **Evidence:** Condition types listed on the alert conditions overview page. Not run.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/alert-conditions/)

*Where*

```text
Alerts > Alert conditions > New condition > Infrastructure > Host not reporting.
```

#### Method 5: Accept: a target that never reported cannot be detected

**Family:** F: Accept and document the difference. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this and add an inventory check.
- **Prerequisites:** A list of expected targets.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** A count of distinct members below the expected number covers the gap.
- **Risk:** The expected number goes stale.
- **How to verify:** Lower the expected number to see it fire.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Fewer than the expected number of instances report* (condition query)

```nrql
SELECT uniqueCount(instance) FROM Metric WHERE metricName = 'up'
```

### histogram_quantile in an alert

**Measured:** 1 alert rule (KubeAPILatencyHigh) on the test estate.

bucketPercentile() is documented for queries, but the condition page does not list it among functions a condition
accepts. Three routes avoid the question: the share of slow requests, a quantile computed at the source, or
OpenTelemetry distributions with percentile().

**Decision tree**

- Try bucketPercentile in the condition editor (method 1).
- Else alert on the share of slow requests (method 2).
- Customer owns Prometheus? Record the quantile (method 3).

**What to ask the customer**

- Which quantile and what latency bound?
- Who owns Prometheus?

**What New Relic cannot do:** Whether bucketPercentile is accepted inside a condition is not stated in the documentation read.

**Ranked methods**

#### Method 1: Alert on the percentage of requests slower than the bound

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this first: it uses only functions the condition page lists.
- **Prerequisites:** A bucket boundary at the latency bound.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay.
- **What changes (numbers, delay, cost):** A share instead of a quantile: 'more than 1 percent slower than 0.5 s' is the same promise as p99 under 0.5 s.
- **Risk:** Bound must be a bucket edge.
- **How to verify:** Compare with histogram_quantile in Grafana for the same hour.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Percent of requests slower than 0.5 s per ingress (threshold above 1)* (condition query)

```nrql
SELECT (filter(sum(nginx_ingress_controller_request_duration_seconds_bucket), WHERE le = '+Inf') - filter(sum(nginx_ingress_controller_request_duration_seconds_bucket), WHERE le = '0.5')) / filter(sum(nginx_ingress_controller_request_duration_seconds_bucket), WHERE le = '+Inf') * 100 FROM Metric FACET ingress
```

#### Method 2: bucketPercentile in the condition (try it)

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this if the editor accepts it.
- **Prerequisites:** Test in the condition editor.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Aggregation window plus delay.
- **What changes (numbers, delay, cost):** Closest to the original rule.
- **Risk:** May be refused; the docs are silent.
- **How to verify:** Save the condition disabled; read it back.
- **Evidence:** Query accepted 2026-11-10; condition acceptance UNVERIFIED.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*p99 per ingress* (condition query)

```nrql
SELECT bucketPercentile(nginx_ingress_controller_request_duration_seconds_bucket, 99) FROM Metric FACET ingress
```

#### Method 3: Record the quantile in Prometheus and alert on the gauge

**Family:** A: Change at ingest. **Status:** VERIFIED-VALID. **Effort:** 1 hour.

- **When to choose it:** Choose this for exact Prometheus numbers.
- **Prerequisites:** Prometheus rule files.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule interval.
- **What changes (numbers, delay, cost):** The condition reads a plain gauge.
- **Risk:** One more series per label set.
- **How to verify:** The sandbox already receives application:http_server_requests_seconds:p95_5m.
- **Evidence:** Query on the existing recorded series accepted 2026-11-10.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)

*Rule*

```yaml
groups:
  - name: latency
    rules:
      - record: ingress:request_duration_seconds:p99_5m
        expr: histogram_quantile(0.99, sum by (le, ingress) (rate(nginx_ingress_controller_request_duration_seconds_bucket[5m])))
```

*Condition on a recorded p95* (condition query)

```nrql
SELECT max(`application:http_server_requests_seconds:p95_5m`) FROM Metric FACET application
```

### predict_linear and deriv

**Measured:** 1 alert rule (KubePersistentVolumeFillingUpPredicted) on the test estate.

NRQL has predictLinear() and derivative() for queries, and static conditions have a "predict future behavior"
option. Whether those functions work inside a condition is not stated in the pages read.

**Decision tree**

- Native prediction option (method 1).
- Fixed threshold proxy (method 2).
- Exact numbers at the source (method 3).

**What to ask the customer**

- How far ahead must the prediction look?
- Is a low-free-space threshold acceptable instead?

**What New Relic cannot do:** predictLinear inside a condition is UNVERIFIED.

**Ranked methods**

#### Method 1: Static condition with the predict-future-behavior option

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for disks filling up.
- **Prerequisites:** A static condition; the option is beta.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Prediction horizon.
- **What changes (numbers, delay, cost):** New Relic extrapolates; not the same regression window.
- **Risk:** Beta.
- **How to verify:** Preview.
- **Evidence:** Query accepted 2026-11-10; the prediction block is from the provider schema.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Available bytes per mount* (condition query)

```nrql
SELECT latest(node_filesystem_avail_bytes) FROM Metric FACET instance, mountpoint
```

*Prediction block*

```hcl
critical {
  operator              = "below"
  threshold             = 0
  threshold_duration    = 300
  threshold_occurrences = "all"
  prediction {
    predict_by = 14400
  }
}
```

#### Method 2: Alert on percent free instead

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for a robust proxy.
- **Prerequisites:** None.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay.
- **What changes (numbers, delay, cost):** Not predictive.
- **Risk:** Late for fast fills.
- **How to verify:** Compare with df.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Percent free below 10* (condition query)

```nrql
SELECT latest(node_filesystem_avail_bytes) / latest(node_filesystem_size_bytes) * 100 FROM Metric FACET instance, mountpoint
```

#### Method 3: Record predict_linear in Prometheus

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 hour.

- **When to choose it:** Choose this for exact numbers.
- **Prerequisites:** Rule files.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** One rule interval.
- **What changes (numbers, delay, cost):** Gauge of predicted bytes.
- **Risk:** More series.
- **How to verify:** Compare with Grafana.
- **Evidence:** Rule syntax read. Not run.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)

*Rule*

```yaml
groups:
  - name: disk
    rules:
      - record: instance_mountpoint:node_filesystem_avail_bytes:predict4h
        expr: predict_linear(node_filesystem_avail_bytes[6h], 4 * 3600)
```

#### Method 4: predictLinear() in a chart for triage

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 5 minutes.

- **When to choose it:** Choose this to look at the forecast.
- **Prerequisites:** None.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Chart only.
- **Risk:** Not an alert.
- **How to verify:** Eyeball.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Forecast 4 hours ahead*

```nrql
SELECT predictLinear(node_filesystem_avail_bytes, 4 hours) FROM Metric FACET instance, mountpoint TIMESERIES 10 minutes
```

*deriv() as derivative*

```nrql
SELECT derivative(node_filesystem_avail_bytes, 1 hour) FROM Metric FACET instance, mountpoint
```

### Multi-query math in alerts

**Measured:** 3 alert rules on the test estate.

A condition reads one query. Math between metrics in the Metric event type is one query. Math across data sources
cannot be a condition.

**Decision tree**

- Same event type: one query (method 1).
- AND of two tests: if() indicator (method 2).
- Minimum traffic guard: if() (method 3).
- Different sources: compute at the source (method 4).

**What to ask the customer**

- Which queries and which data sources?
- Is the math a ratio, a sum or an AND?

**What New Relic cannot do:** No cross-data-source math, and no subquery or JOIN in a condition.

**Ranked methods**

#### Method 1: One query with math on aggregates

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this for ratios.
- **Prerequisites:** Both sides in Metric.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay.
- **What changes (numbers, delay, cost):** One signal.
- **Risk:** Labels must match.
- **How to verify:** Each side alone.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*5xx share* (condition query)

```nrql
SELECT filter(sum(nginx_ingress_controller_requests), WHERE status LIKE '5%') / sum(nginx_ingress_controller_requests) * 100 FROM Metric FACET ingress
```

#### Method 2: AND of two tests with if()

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this for errors high AND traffic high.
- **Prerequisites:** if() documented.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay.
- **What changes (numbers, delay, cost):** Indicator 1 or 0.
- **Risk:** Hard to read.
- **How to verify:** Force each side.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*1 when 5xx share above 5 percent and requests above 100* (condition query)

```nrql
SELECT if(filter(sum(nginx_ingress_controller_requests), WHERE status LIKE '5%') / sum(nginx_ingress_controller_requests) * 100 > 5 AND sum(nginx_ingress_controller_requests) > 100, 1, 0) FROM Metric FACET ingress
```

#### Method 3: Minimum traffic guard

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this to avoid low-traffic noise.
- **Prerequisites:** None.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay.
- **What changes (numbers, delay, cost):** NULL when traffic is low.
- **Risk:** NULL resets duration.
- **How to verify:** Low traffic window.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*Ratio only when there are 100 requests* (condition query)

```nrql
SELECT if(sum(nginx_ingress_controller_requests) > 100, filter(sum(nginx_ingress_controller_requests), WHERE status LIKE '5%') / sum(nginx_ingress_controller_requests) * 100) FROM Metric FACET ingress
```

#### Method 4: Compute cross-source math where both sides live

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 1 day.

- **When to choose it:** Choose this for different data sources.
- **Prerequisites:** A system that sees both.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** Rule interval.
- **What changes (numbers, delay, cost):** One series.
- **Risk:** Moves logic.
- **How to verify:** Compare.
- **Evidence:** Design advice.
- **Sources:** [prometheus.io](https://prometheus.io/docs/prometheus/latest/configuration/recording_rules/)

*Idea*

```text
Record the combined value in the system that holds both inputs and remote-write it.
```

### References to recording-rule series

**Measured:** 15 widgets and 2 alert rules on the test estate.

Recording-rule series have colons in the name. They exist in New Relic only if the Prometheus that evaluates the
rule remote-writes the output. Colon names work in NRQL inside backticks (measured).

**Decision tree**

- Remote-write the output (method 1).
- Recompute from raw (method 2).
- Alert with backticks (method 3).
- Rename at ingest (method 4).

**What to ask the customer**

- Does the Prometheus that runs the rules remote-write?
- Is the rule expression simple?

**What New Relic cannot do:** New Relic does not evaluate recording rules.

**Ranked methods**

#### Method 1: Remote-write the rule output and query with backticks

**Family:** A: Change at ingest. **Status:** VERIFIED-VALID. **Effort:** 30 minutes.

- **When to choose it:** Choose this first.
- **Prerequisites:** Rule evaluator with remote write.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** Rule interval.
- **What changes (numbers, delay, cost):** Exact Prometheus values.
- **Risk:** Dropped by write_relabel.
- **How to verify:** Query.
- **Evidence:** Recorded series exist and queries accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/)

*Read a recorded series*

```nrql
SELECT average(`ingress:nginx_requests:rate2m`) FROM Metric FACET ingress TIMESERIES 10 minutes
```

#### Method 2: Recompute the rule from raw metrics

**Family:** B: Rewrite in NRQL. **Status:** VERIFIED-VALID. **Effort:** 20 minutes.

- **When to choose it:** Choose this when rules are not shipped.
- **Prerequisites:** Raw metrics present.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Slightly different window.
- **Risk:** Cost per query.
- **How to verify:** Compare both.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/nrql/nrql-syntax-clauses-functions/)

*ingress:nginx_requests:rate2m recomputed*

```nrql
SELECT rate(sum(nginx_ingress_controller_requests), 1 second) FROM Metric FACET ingress TIMESERIES 10 minutes
```

#### Method 3: Alert on the recorded series

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 10 minutes.

- **When to choose it:** Choose this for alert rules.
- **Prerequisites:** Series arrives.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Window plus delay.
- **What changes (numbers, delay, cost):** Backticks required.
- **Risk:** Silent if rule stops: add loss of signal.
- **How to verify:** Preview.
- **Evidence:** Accepted 2026-11-10.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Recorded CPU utilisation* (condition query)

```nrql
SELECT latest(`instance:node_cpu_utilisation:rate5m`) FROM Metric FACET instance
```

#### Method 4: Rename the metric at ingest

**Family:** A: Change at ingest. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this to avoid backticks.
- **Prerequisites:** Remote write relabel.
- **Needs collector access:** yes. **Needs data re-ingested or sent differently:** yes. **Latency:** None.
- **What changes (numbers, delay, cost):** New name.
- **Risk:** Names diverge.
- **How to verify:** Query new name.
- **Evidence:** Relabel syntax per Prometheus.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/infrastructure/prometheus-integrations/install-configure-remote-write/set-your-prometheus-remote-write-integration/)

*Replace colons with underscores*

```yaml
write_relabel_configs:
  - source_labels: [__name__]
    regex: "([^:]+):([^:]+):([^:]+)"
    target_label: __name__
    replacement: "${1}_${2}_${3}"
```

### for / pending duration, noDataState and execErrState

**Measured:** Every alert rule has a for duration and a no-data state; 53 of 55 rules use NoData/Error defaults (2026-11-10 test estate).

Grafana's `for` is a pending period. New Relic's threshold duration ("for at least N minutes" or "at least once in N
minutes") sits on top of an aggregation window. No-data handling maps to loss of signal and gap filling. Query
errors have no New Relic counterpart.

**Decision tree**

- Map `for` to threshold duration and occurrences (method 1).
- Map NoData to loss of signal or gap filling (method 2).
- Sparse data? Pick the streaming method (method 3).
- KeepFiring and recovery thresholds are not available (method 4).

**What to ask the customer**

- What should happen when data stops: alert, stay quiet or keep the last state?
- How often does the data arrive?

**What New Relic cannot do:** No keep_firing_for, no recovery threshold, no execErrState: a failing query is not alertable.

**Ranked methods**

#### Method 1: Map for to threshold duration and occurrences

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 10 minutes.

- **When to choose it:** Choose this for every rule.
- **Prerequisites:** Duration is a multiple of the aggregation window (60 to 86400 s).
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Duration plus window plus delay; the live test measured about 5 minutes more than Grafana.
- **What changes (numbers, delay, cost):** "for 5m" becomes threshold_duration 300 with occurrences ALL. "At least once in" is AT_LEAST_ONCE. Any edit resets evaluation.
- **Risk:** A duration over 24 hours is refused.
- **How to verify:** Firing test on a throwaway policy.
- **Evidence:** Terms read in the NRQL condition page and provider schema. Not created.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*for: 5m*

```hcl
critical {
  operator              = "above"
  threshold             = 0
  threshold_duration    = 300
  threshold_occurrences = "all"
}
```

#### Method 2: Map NoData to loss of signal and gap filling

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 15 minutes.

- **When to choose it:** Choose this to reproduce NoData=Alerting or OK.
- **Prerequisites:** A first data point.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Expiration duration.
- **What changes (numbers, delay, cost):** NoData=Alerting: open_violation_on_expiration. NoData=OK: close_violations_on_expiration. KeepLast: fill_option last_value.
- **Risk:** Gap filling happens only after a later point arrives.
- **How to verify:** Stop a test sender.
- **Evidence:** Query accepted 2026-11-10; settings from the loss-of-signal page.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-loss-signal-gap-filling/)

*Query that keeps NULL for empty windows* (condition query)

```nrql
SELECT count(*) FROM Metric WHERE metricName = 'ckg_flaky_heartbeat'
```

*NoData=Alerting*

```hcl
expiration_duration          = 300
open_violation_on_expiration = true
fill_option                  = "none"
```

#### Method 3: Choose event_flow, event_timer or cadence

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 10 minutes.

- **When to choose it:** Choose event_timer for sparse data.
- **Prerequisites:** Know the arrival pattern.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Delay default 120 s; timer default 60 s.
- **What changes (numbers, delay, cost):** Changes when windows close.
- **Risk:** Wrong method delays or drops evaluation.
- **How to verify:** Compare time to fire.
- **Evidence:** Read in the streaming concepts page.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Sparse data*

```hcl
aggregation_method = "event_timer"
aggregation_timer  = 120
```

#### Method 4: Accept: no keep-firing or recovery threshold

**Family:** F: Accept and document the difference. **Status:** DOC-ONLY. **Effort:** 5 minutes.

- **When to choose it:** Choose this and use longer durations.
- **Prerequisites:** Owner agrees.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Flapping is damped only by duration.
- **Risk:** More flaps.
- **How to verify:** Review a week of events.
- **Evidence:** Absence in the condition settings.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/create-alert/create-alert-condition/create-nrql-alert-conditions/)

*Wording*

```text
No recovery threshold exists; the event closes when the signal returns inside the threshold.
```

### Label-based notification routing

**Measured:** Routes with regex matchers or ordering on the test estate.

Grafana routes by alert labels in a policy tree. New Relic workflows filter issues by attributes such as
accumulations.tag.team, policy ids or condition names.

**Decision tree**

- Tag conditions and filter on the tag (method 1).
- One policy per route (method 2).
- Route by a metric label value: one condition per value (method 3).
- Accept ordering differences (method 4).

**What to ask the customer**

- Which labels route and how many routes?
- Is the value a metric label or a rule label?

**What New Relic cannot do:** Workflows cannot route on a facet value; no continue/ordering semantics.

**Ranked methods**

#### Method 1: Workflow filter on accumulations.tag.<key>

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this for team-style labels.
- **Prerequisites:** Entity tags on the condition or entity; channel ids.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Tags appear on the issue only after one has carried them.
- **Risk:** Tag spelling differs between pages.
- **How to verify:** Use the workflow test.
- **Evidence:** Example read in the NerdGraph workflows page. Not run.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-workflows/)

*Workflow for team payments*

```graphql
mutation {
  aiWorkflowsCreateWorkflow(accountId: 1234567, createWorkflowData: {
    name: "route team payments"
    workflowEnabled: false
    destinationsEnabled: true
    mutingRulesHandling: DONT_NOTIFY_FULLY_MUTED_ISSUES
    issuesFilter: { name: "team", type: FILTER,
      predicates: [{ attribute: "accumulations.tag.team", operator: EXACTLY_MATCHES, values: ["payments"] }] }
    destinationConfigurations: { channelId: "CHANNEL_ID", notificationTriggers: [ACTIVATED, CLOSED] }
  }) { workflow { id } errors { description type } }
}
```

#### Method 2: One policy per route

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this for few routes.
- **Prerequisites:** Policies.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Filter on labels.policyIds.
- **Risk:** Policy sprawl.
- **How to verify:** Fire one test.
- **Evidence:** Attribute listed in the notifications reference note.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/incident-workflows/)

*Predicate*

```json
{"attribute": "labels.policyIds", "operator": "EXACTLY_MATCHES", "values": ["POLICY_ID"]}
```

#### Method 3: One condition per label value

**Family:** D: Redesign the alert. **Status:** VERIFIED-VALID. **Effort:** 1 hour.

- **When to choose it:** Choose this to route by a metric label.
- **Prerequisites:** Short value list; Terraform.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Conditions multiply.
- **Risk:** New values missed.
- **How to verify:** Count conditions.
- **Evidence:** Query accepted 2026-11-10.
- **Sources:** [registry.terraform.io](https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/nrql_alert_condition)

*One condition for namespace payments* (condition query)

```nrql
SELECT latest(kube_deployment_status_replicas_unavailable) FROM Metric WHERE namespace = 'payments' FACET deployment
```

*Fan out*

```hcl
resource "newrelic_nrql_alert_condition" "per_namespace" {
  for_each  = toset(["payments", "web"])
  policy_id = newrelic_alert_policy.by_ns[each.key].id
  # name, nrql and critical blocks use each.key
}
```

#### Method 4: Accept: no continue, ordering or regex semantics

**Family:** F: Accept and document the difference. **Status:** DOC-ONLY. **Effort:** 15 minutes.

- **When to choose it:** Choose this and list values.
- **Prerequisites:** Owner agrees.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Regex becomes value lists.
- **Risk:** Double or missed notification.
- **How to verify:** Send test issues.
- **Evidence:** Operator list read.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/incident-workflows/)

*Wording*

```text
Regex matchers are replaced by explicit lists of values.
```

### Mute timings with active intervals

**Measured:** 2 of 3 mute timings did not convert on the test estate.

A muting rule has one window with a daily, weekly or monthly repeat. Grafana intervals combine weekdays, days of the
month, months and times.

**Decision tree**

- Weekly pattern: one weekly rule (method 1).
- Months or days: several rules (method 2).
- Scope to a team (method 3).
- Make workflows respect muting (method 4).

**What to ask the customer**

- Which alerts must be muted and when?
- Time zone?

**What New Relic cannot do:** No months-of-year or days-of-month lists in one rule.

**Ranked methods**

#### Method 1: One weekly muting rule

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 20 minutes.

- **When to choose it:** Choose this for weekday windows.
- **Prerequisites:** Time zone.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Window may cross midnight.
- **Risk:** DST.
- **How to verify:** Read back.
- **Evidence:** Fields read in the muting rules page.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/muting-rules-suppress-notifications/)

*Weeknights 22:00 to 06:00*

```graphql
mutation {
  alertsMutingRuleCreate(accountId: 1234567, rule: {
    name: "weeknights", enabled: true,
    description: "migrated mute timing",
    schedule: { startTime: "2026-11-16T22:00:00", endTime: "2026-11-17T06:00:00",
      timeZone: "Europe/Amsterdam", repeat: WEEKLY,
      weeklyRepeatDays: [MONDAY, TUESDAY, WEDNESDAY, THURSDAY, FRIDAY] }
    condition: { operator: AND, conditions: [{ attribute: "policyId", operator: EQUALS, values: ["POLICY_ID"] }] }
  }) { id }
}
```

#### Method 2: Unroll months and days into several rules

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 1 hour.

- **When to choose it:** Choose this for monthly or seasonal windows.
- **Prerequisites:** Terraform.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** More rules.
- **Risk:** Maintenance.
- **How to verify:** List rules.
- **Evidence:** Schedule fields read.
- **Sources:** [registry.terraform.io](https://registry.terraform.io/providers/newrelic/newrelic/latest/docs/resources/alert_muting_rule)

*One rule per date*

```hcl
resource "newrelic_alert_muting_rule" "window" {
  for_each = toset(["2026-12-24", "2026-12-31"])
  name     = "mute ${each.key}"
  enabled  = true
  schedule {
    start_time = "${each.key}T00:00:00"
    end_time   = "${each.key}T23:59:59"
    time_zone  = "Europe/Amsterdam"
  }
}
```

#### Method 3: Scope the rule to a policy or tag

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 15 minutes.

- **When to choose it:** Choose this to mute only some alerts.
- **Prerequisites:** Policy ids.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Mute only matching events.
- **Risk:** Too broad.
- **How to verify:** Read back.
- **Evidence:** Condition shape read.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/muting-rules-suppress-notifications/)

*Condition*

```json
{"operator": "AND", "conditions": [{"attribute": "tags.team", "operator": "EQUALS", "values": ["payments"]}]}
```

#### Method 4: Set mutingRulesHandling on workflows

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 5 minutes.

- **When to choose it:** Choose this always.
- **Prerequisites:** Workflows.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** NOTIFY_ALL_ISSUES makes muting useless.
- **Risk:** Missed alerts.
- **How to verify:** Fire during a window.
- **Evidence:** Read in the workflows page.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/apis/nerdgraph/examples/nerdgraph-api-workflows/)

*Value*

```text
mutingRulesHandling: DONT_NOTIFY_FULLY_MUTED_ISSUES
```

### Slack and Teams contact points

**Measured:** 2 of 5 contact points on the test estate.

Slack needs an OAuth click once; Teams needs the New Relic app and a security code. After that channels are API calls.

**Decision tree**

- Native destination (method 1).
- Webhook (method 2).
- Via an incident tool (method 3).

**What to ask the customer**

- May an admin authorise the New Relic app?

**What New Relic cannot do:** Slack and Teams destinations cannot be created from a script.

**Ranked methods**

#### Method 1: Native Slack or Teams destination

**Family:** E: Use a New Relic native feature. **Status:** DOC-ONLY. **Effort:** 20 minutes.

- **When to choose it:** Choose this by default.
- **Prerequisites:** Admin click.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Native messages.
- **Risk:** Admin approval.
- **How to verify:** Send a test.
- **Evidence:** Read in notifications reference.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/microsoft-teams-integrations/)

*Steps*

```text
Alerts > Destinations > Slack or Microsoft Teams > authorise; then create a channel.
```

#### Method 2: Webhook destination with a payload template

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this when OAuth is refused.
- **Prerequisites:** Incoming webhook URL.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Plain text message.
- **Risk:** Secret URL.
- **How to verify:** Test.
- **Evidence:** Template variables read.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/message-templates/)

*Payload*

```json
{"text": "{{annotations.title.[0]}} - {{priority}}"}
```

#### Method 3: Route through PagerDuty or Opsgenie

**Family:** D: Redesign the alert. **Status:** DOC-ONLY. **Effort:** 30 minutes.

- **When to choose it:** Choose this if chat is already wired there.
- **Prerequisites:** Existing integration.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** Extra hop.
- **What changes (numbers, delay, cost):** Chat comes from the tool.
- **Risk:** Dependency.
- **How to verify:** Test.
- **Evidence:** Integrations listed.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/notification-integrations/)

*Route*

```text
Workflow to PagerDuty service integration; its chat integration posts.
```

#### Method 4: Accept: message look and buttons differ

**Family:** F: Accept and document the difference. **Status:** DOC-ONLY. **Effort:** 5 minutes.

- **When to choose it:** Choose this.
- **Prerequisites:** None.
- **Needs collector access:** no. **Needs data re-ingested or sent differently:** no. **Latency:** None.
- **What changes (numbers, delay, cost):** Look changes.
- **Risk:** Low.
- **How to verify:** Review.
- **Evidence:** Design.
- **Sources:** [docs.newrelic.com](https://docs.newrelic.com/docs/alerts/get-notified/message-templates/)

*Wording*

```text
Grafana message templates are not carried over.
```
