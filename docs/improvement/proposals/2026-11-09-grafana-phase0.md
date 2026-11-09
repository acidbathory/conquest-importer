# Proposal: Grafana source, phase 0

Branch `improve/grafana-phase0` (from `integration/all-threads`). Status: waiting for the owner.
Nothing here was checked against a live New Relic or Grafana account by the builder; the only
Grafana evidence is the two scrubbed read-backs of 17 paused sample rules from the sandbox stack.

## What it does

- Loads Grafana alert rules from any of four read shapes (provisioning list, export, ruler,
  `/apis`) with one normaliser per shape, into one internal rule model.
- Converts a Grafana-managed rule whose pipeline is query, optional reduce, one threshold, when the
  query is a plain Prometheus selector with at most one `avg`/`sum`/`min`/`max` (and `by`), or a
  plain Loki `count_over_time`. Everything else is manual with a specific reason and steps.
- `extract-grafana`: GET only on four fixed paths, host must be `https://<stack>.grafana.net` (or
  another https host named with `--allow-host`), contact points are only counted and never
  requested with `decrypt`.

## Measure

`uv run importer improve-measure`, before and after:

| | Before | After |
|---|---|---|
| grafana | not measured (no source) | 6 of 21 (29%) on `tests/fixtures/grafana`: 4 of the 17 real sample rules, plus 2 hand-made |
| cloudwatch | 27 of 32 (84%) | 27 of 32 (84%) |
| gcp | 3 of 4 (75%) | 3 of 4 (75%) |
| dynatrace | 2 of 8 (25%) | 2 of 8 (25%) |

The baseline file was not edited. The gate compares against the old baseline and passes because
the old sources are unchanged; adding grafana to the baseline needs your approval.

The 17 real sample rules:

| Rule | Result | Why |
|---|---|---|
| ckg-prom-cpu-gauge | converts | `avg by (instance) (...)` with `=` and `!=` matchers |
| ckg-prom-regex-unagg | converts | selector with `=~"orders\|billing"` becomes IN |
| ckg-loki-errors | converts | `sum by (app) (count_over_time(... \|= ... != ... [5m]))`, `app=~"api.*"` becomes LIKE |
| ckg-expr-keeplast-nodata | converts | `avg(...)`, with notes for `KeepLast` |
| ckg-expr-flatten | manual | math expression across two queries |
| ckg-expr-multi-ds-manual | manual | two data sources |
| ckg-expr-within-range | manual | `within_range` threshold |
| ckg-loki-latency | manual | parser stage and `unwrap` |
| ckg-prom-5xx-rate | manual | `rate()` (counter semantics differ in New Relic) |
| ckg-prom-absent | manual | `absent()` |
| ckg-prom-disk-fill | manual | `predict_linear` |
| ckg-prom-join-manual | manual | `group_left` |
| ckg-prom-mem-ratio | manual | `_over_time` functions and a ratio |
| ckg-prom-offset-manual | manual | `offset` |
| ckg-prom-p95 | manual | `histogram_quantile` |
| ckg-prom-topk-manual | manual | `topk` |
| ckg-prom-up-zero | manual | comparison operator in the query (`== 0`) |

## Changes to shared files

- `src/importer/core/ir.py` and `core/emit/nrql.py`: `TagFilter` gains `negate`, `values` and
  `wildcard` (same meaning as on `CountFilter`), so a metric scope can say `!=`, IN, NOT IN, LIKE.
  Defaults keep every existing output identical (all earlier tests unchanged).
- `core/taxonomy.py`: anchored phrases for the new wording. A test checks that every reason from
  the AWS, Google and Dynatrace fixtures keeps its category.
- `core/improve.py` (fixture folder), `core/report_html.py` (small profile), `sources/__init__.py`,
  `cli.py` (command `extract-grafana`, `--source` help), `tests/test_sources.py` and
  `tests/test_improve.py` (source lists).

## Risks and unverified

- No converted condition was run in New Relic. Metric names (Prometheus names as they appear after
  remote write or OTLP) and the attribute names for Prometheus labels, Loki labels and the log
  line (`message`) are assumptions, and each converted rule says so.
- The provisioning and export rules carry no data source type; the type is inferred from the data
  source uid or the query shape and the rule says so. A wrong guess would normally fail the parse
  and stay manual, but this is untested on other stacks. The extractor could read
  `/api/datasources` to settle it; that path is not on the allow-list yet.
- The ruler and `/apis` fixtures are hand-made from the research note, not read from a stack.
  Grafana 11 and 12 behaviour is unverified; only 13.x was seen.
- The provisioning list has no evaluation interval, so a rule read that way gets a 60 second window
  with a note. Reading the export (which has it) would be better; the extractor does not call it.
- Prometheus `!=` and `!~` also match series without the label; NRQL may treat missing attributes
  differently. Noted on each rule that uses them.
- A bare selector with reducer `last` becomes an `average` over the window (noted). A count over a
  window with no logs is no row, not zero, so below-thresholds on logs may differ (noted).
- Counter-looking names (`_total`, `_count`, `_sum`, `_bucket`) are manual on purpose: the
  threshold would change meaning.
- `ckg-prom-absent` could become a loss-of-signal condition; left manual in phase 0.
- Remediation text was written without New Relic documentation open; check NRQL snippets
  (`rate()`, `RLIKE`) before trusting them.

## Defect class found while building

A new source's rules can be shadowed by loose patterns that earlier sources registered (here: the
Google rules on `reducer`, `over New Relic's limit` and `valid New Relic attribute name`), and a
new rule can claim another source's reason. The tests now check both directions for Grafana
(`test_a_specific_rule_is_not_shadowed_by_a_broader_one`,
`test_the_grafana_rules_do_not_claim_reasons_of_other_sources`). A generic version for all sources
is still open.

## Needs the owner

1. Merge decision, and approval to add grafana to `docs/improvement/baseline.json`.
2. Whether `TagFilter` may grow these fields (shared IR).
3. A live check: send one sample metric to New Relic and run the converted NRQL; read the ruler
   and `/apis` shapes from the stack and replace the hand-made fixtures.
4. Whether the extractor should also read `/api/datasources` (type lookup) and the export (interval).
