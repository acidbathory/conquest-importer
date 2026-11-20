# New Relic capability live check (sandbox, 2026-11-16)

What this is: 22 probes run against a New Relic sandbox account with `scripts/live-test/capability-check/run.py`
to settle questions the documentation left open (see [nr-capability-audit](nr-capability-audit.md)). All
objects were named `ckg-cap-...`, alert conditions were created **disabled**, the dashboard was **private**, no
metric data was sent, and everything was deleted afterwards (a search found no leftovers).

What it proves and what it does not: New Relic **accepted or rejected the query or payload when it was created**.
That settles what is valid. It does not show that a condition evaluates correctly or that a widget renders
as intended, because no data existed and nothing was enabled or viewed.

## Alert conditions (static NRQL conditions, created disabled)

| Probe | Result | Reason given when rejected |
|---|---|---|
| control `SELECT average(x) FROM Metric` | accepted | |
| nested sum of the latest value per series: `SELECT sum(v) FROM (SELECT latest(x) AS v FROM Metric FACET host)` | **accepted** | |
| `bucketPercentile(x_bucket, 95)` | **accepted** | |
| `clamp_min(average(x), 1)` | **accepted** | |
| `if(average(x) > 5, 1, 0)` | **accepted** | |
| `derivative(sum(x), 1 minute)` | rejected | "Unknown function sum()" (a parse error for this shape; not conclusive for `derivative` itself) |
| `WHERE attr RLIKE 'web-.*'` and `NOT RLIKE` | **accepted** | |
| operators `EQUALS`, `NOT_EQUALS`, `ABOVE_OR_EQUALS`, `BELOW_OR_EQUALS` | **accepted** (all four) | |
| `average(x) * cardinality(x)` | rejected | `cardinality` is on a list of banned functions in conditions: bytecountestimate, nativesizeestimate, cardinality, attributecardinality, dimensioncardinality, distribution, earliestn, latestn, keysetlike, clwyhistogram, hdrhistogram |
| `COMPARE WITH 1 week ago` | rejected | validation error (the docs say it is incompatible with alerting) |
| malformed query (negative control) | rejected | validation error |

## Read-only NRQL syntax checks (dashboard-style queries over an empty 5 minute window)

`clamp_min`, nested aggregation, `average(x) * cardinality(x)` with `FACET`, `bucketPercentile`, `RLIKE`, `if()` and
`LIKE '%web%'`: all accepted.

## Dashboard payload (one private dashboard, 5 widgets, 1 variable; read back after creation)

- The dashboard was accepted with the shapes our emitter writes: a `STRING` variable with `replacementStrategy`
  `STRING` and a string default, `viz.billboard` widgets, and `rawConfiguration.nrqlQueries`.
- `rawConfiguration.platformOptions.ignoreTimeRange: true` was **stored** on the widget. A top-level
  `rawConfiguration.ignoreTimeRange: true` was also stored, so storage alone does not show which key the product
  honours. The documented per-widget setting is "Ignore time picker"; `platformOptions.ignoreTimeRange` is the
  likely key. Confirm by opening a dashboard created with it.
- A widget query with a variable inside quotes (`LIKE '%{{v}}%'`) and one with a whole variable were accepted at
  creation. Creation does not show how they render.

## What this changes

- **Histogram quantile alerts become possible to build** (`bucketPercentile` is accepted in a condition). Needs a new
  aggregation in our frozen condition model, so it needs a proposal. It still returns nothing on data sent over
  OTLP, where a Prometheus histogram is stored as one distribution (see [ingestion-paths](ingestion-paths.md)).
- **Regex matchers are valid** in conditions (`RLIKE`, `NOT RLIKE`). The limit is our frozen model, not New Relic.
- **`clamp_min` and `if()` are accepted**, so denominators guarded with `clamp_min(x, 1)` are no longer impossible.
- **A nested sum of the latest value per series is accepted** in a condition, which can make gauge sums exact in
  alerts when the series dimensions are known (it needs those dimensions; it does not remove that problem).
- **`cardinality()` is confirmed banned in conditions**, so `average(x) * cardinality(x)` stays dashboards-only.
- **Single-value widgets** can carry `SINCE 5 minutes ago` together with the widget's ignore-time-picker setting
  once someone confirms the setting works when rendered.
- Still unknown: whether `derivative` works in a condition (retry as `derivative(x, 1 minute)`), and everything that
  needs data to evaluate.

## Data round (sandbox, 2026-11-16): the forms evaluated on real data

`scripts/live-test/capability-check/data_round.py` sent 91 synthetic gauge points (two `ckg_cap_*` metrics) and ran
read-only NRQL over them, comparing each result with the value computed from the same series (the PromQL answer).
Over Prometheus remote write the rig had already shown that `_bucket` keeps `le`, `_sum` is a `summary`, and
`bucketPercentile` and `rate(sum(x_sum), 1 second)` match Prometheus ([rig results](telemetry-rig-results.md));
these probes cover what the rig did not.

| Probe | Expected | Got | Result |
|---|---|---|---|
| `ckg_host RLIKE 'web-.'` over `web-1`, `web-10`, `xweb-1`, `web-` | `web-1` only (full-string match) | `web-1` | **match: RLIKE is a full-string match** |
| `ckg_host NOT RLIKE 'web-.'` | the other three hosts | the other three hosts | match |
| rows with the attribute missing (`IS NULL`) | 3 | 3 | match |
| `NOT RLIKE 'zzz'` over 15 rows (12 with the attribute, 3 without) | 15 if rows without the attribute are kept | 15 | **NOT RLIKE keeps rows with a missing attribute** |
| `RLIKE '.*'` over the same rows | 12 if rows without the attribute are excluded | 12 | **RLIKE excludes rows with a missing attribute** |
| `average(x) * cardinality(x)`, 5 minute window, series at 15 s, 60 s and one that stopped | 210 (true sum) | 180 | 14% low (within the 15% bound) |
| the same form, 1 minute window | 210 | 100 | **52% low** (the slow and the stopped series are missing from the window) |
| `SELECT sum(v) FROM (SELECT latest(x) AS v ... FACET host)` | 210 | 210 | **exact** |
| `clamp_min(average(x), 100)` | 100 | 100 | match |
| `if(average(x) > 25, 1, 0)` | 1 | 1 | match |

(The first run labelled the two NULL rows "differs" because the script expected 9 and 12 where 12 and 15 were right;
the script is corrected. The data is as in the table.)

What this changes:
- **Regex matchers are exact for patterns that cannot match the empty string.** Prometheus treats a missing label
  as an empty string: `=~` with such a pattern does not select the series, `!~` does. New Relic does the same
  (RLIKE excludes the missing attribute, NOT RLIKE keeps it). Our regex whitelist already refuses patterns that can
  match the empty string. The note on converted regex rules ("a missing label is NULL ...") is therefore more
  cautious than needed for the shapes we convert.
- **The nested sum of the latest value per series is exact**, including a series that stopped inside the window. It
  needs the dimensions that name one series; nothing supplies them today.
- **`average(x) * cardinality(x)` is only a rough sum**: 14% low with mixed scrape intervals over 5 minutes and 52% low
  over 1 minute when series are slow or stopped. This confirms the harness and the note on dashboard widgets; it must
  stay dashboards-only and approximate.
- `clamp_min` and `if()` evaluate correctly, so proposal 3 (clamp functions) is valid as a form.

## Compound alert conditions (sandbox, 2026-11-20)

Script: `scripts/live-test/capability-check/compound_check.py` (plan by default; `--apply` and `--cleanup`
behind the account guard). 2 policies, 10 standard NRQL component conditions that can never reach their
threshold (an event type nothing writes, threshold 1e12, no workflow), 13 compound probes. Everything was
deleted afterwards and a search found no leftovers.

API (read from the live NerdGraph schema): `alertsCompoundConditionCreate(accountId, policyId,
compoundAlertCondition: {name, enabled, triggerExpression, facetMatchingBehavior, thresholdDuration,
componentConditions: [{id, alias}], runbookUrl, ...})`, plus `...Update` and `...Delete` (the delete
returns `{ id }`). Read back: `actor.account.alerts.compoundConditions { items { ... } }`.
`facetMatchingBehavior` is `FACETS_IGNORED` or `FACETS_MATCH`.

| Question | Answer (seen live) |
|---|---|
| Aliases | Only the capital letters `A` to `E`. Lower-case `a AND b` is rejected ("restrict aliases to A, B, C, D, E"), which is where the five-component limit comes from. |
| `A AND B`, `A OR B`, `A AND NOT B`, `(A AND B) OR C`, five components | Accepted and stored as written. |
| Six components | Rejected. |
| A disabled component | Rejected: "All component conditions must be enabled for a compound alert". |
| `FACETS_MATCH` with two components faceted the same way | Accepted. |
| `FACETS_MATCH` with one faceted and one unfaceted component | Rejected: "No matching facets found across component conditions". |
| No `thresholdDuration` | Accepted; stored as 60 seconds. |
| A component from another policy | Accepted. |
| The same component in two compound conditions | Accepted. |
| Compound condition itself enabled | Accepted. |
| Deleting a policy | Deletes its compound conditions too (the deletes that followed answered "not found"). |

Not checked: how a compound condition evaluates or fires, what an incident from it looks like, and the
Terraform resource. Consequence for a converter: the components must be enabled, so each one evaluates
and can open its own incidents. Put the components in a policy without a workflow and route only the
compound condition, or every leg notifies on its own.
