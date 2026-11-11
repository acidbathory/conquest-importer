# Proposal: parser spellings and PromQL math functions (cycle `parser-funcs`)

Branch `improve/parser-funcs` from `integrate/1111`. Gate: GATE PASSED. No pinned output changed.

## Pinned output diff

None. The fixtures under `tests/fixtures/grafana-dashboards` and `grafana-dashboard-convert`
hold no `round(x, n)`, `clamp_*`, `time()` or `delta()`, and no query in them uses single quotes,
backticks or upper-case aggregate names outside the cases that already stay refused for another
reason (the hostile ones). Census on those two folders, before and after: 64 expressions, 41 the
converter writes (43 with the engine); 58 PromQL panels, 31 end to end. Unchanged.
The corpora the numbers in the research came from (top-10 grafana.com set, sandbox extract) are
not in the repository, so the panel counts there (22 `round`, 6 parser-only) could not be
re-measured here. The unit tests cover each construct instead.

## What changed

| construct | now |
|---|---|
| `# comment` | stripped outside strings (all three quote styles) before the planner, the dashboard converter and the alert plan read the text (`sources/grafana/promql_normalize.py`) |
| `'...'` and backtick strings | rewritten as the same value in `"..."` (`\'` loses its backslash, a bare `"` gains one, a backtick body is raw so `\` and `"` are escaped) |
| `SUM(x)`, `Sum By (a)`, `BY`, `WITHOUT` | aggregation operators and `by`/`without` are lower-cased (Prometheus reads keywords in any case). Functions are case sensitive there: `RATE(` stays refused |
| `round(x, 1)`, `round(x, 0.1 ... 0.000000001)` | `round(x)`, `round(x, n)`: NRQL rounds to decimal places, PromQL to a multiple, which agree for 1 and powers of ten |
| `round(x, 5)`, `0.5`, `0`, `-1`, `1e-1`, a variable | manual, new reason `Q_ROUND_STEP`, steps in the "Dashboard query function" rule |
| `clamp_min(x, n)`, `clamp_max(x, n)` around a whole query, `n` a plain number | `clamp_min(<select>, n)`; anything else (`clamp`, a variable, an expression, inside arithmetic) stays `Q_FUNCTION` |
| `time()` | manual, own reason `Q_TIME` |
| `delta()` | manual, own reason `Q_DELTA` (last minus first sample stretched to the window is not `latest() - earliest()`; `earliest` on Metric is also unverified) |

Both new reason texts start with the old catch-all text, so the existing anchored rules still
answer them; the rule text for both gained the specific steps.

## Not done, and why

- Alerts: only the spelling fix. `round`, `clamp` in an alert expression sit under a threshold
  and the alert handlers refuse any function around the aggregate; the alert side needs its own
  analysis of what a threshold on a rounded value means.
- `clamp(x, a, b)`: needs `a <= b` (PromQL returns nothing otherwise); not in the brief.
- `round` or `clamp` inside an aggregation or arithmetic (`sum(round(x))`, `round(x) * 100`):
  unchanged, `Q_FUNCTION`.
- Other keywords in other cases (`BOOL`, `AND`, `OFFSET`): not touched.
- Metric-name extraction for hints and the quickstart (`prom_hints`, `quickstart_map`) still reads
  the raw text, so a metric named only in a comment would be listed there.

## Differential harness

`scripts/analysis/promql_diff_harness.py` was run with six extra cases (spellings, `round`,
`clamp_*`): all six were `NOT_MODELLED` (the reference parser in `importer.parity.diff_promql`
does not read quotes other than `"`, nor `round` or `clamp_*`). The cases were not added, since
a case the model cannot run is noise. A follow-up in `importer/parity/diff_promql.py` and
`diff_nrql.py` (not mine) would add `round`, `clamp_min`, `clamp_max`. The results document
`docs/research/promql-differential-results.md` is therefore unchanged.

## UNVERIFIED

- That NRQL `round(x, n)` means n decimal places and that `clamp_min`/`clamp_max` take a literal
  as second argument on Metric with FACET and TIMESERIES: from the New Relic function list in
  `docs/research/newrelic-grafana-data-and-query-analogues.md` (F46, F48), not run (no network).
- Tie rounding: PromQL rounds a half up (`floor(v + 0.5)`); NRQL does not state its rule, so a
  negative value exactly halfway may differ by one unit. The existing `round(x)` has the same gap.
- Prometheus keyword case-insensitivity is from memory of its lexer, not run here.
