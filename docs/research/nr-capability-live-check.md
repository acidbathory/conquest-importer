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
