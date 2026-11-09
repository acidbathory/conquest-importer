# count(*) over an empty window: NULL, not 0

Status: research only. No emitter behaviour was changed, and no golden file was touched.

Every claim carries one of three labels:

- REPO: read in this repository, at the file and line given (line numbers are for the commit this
  branch started from).
- DOCS: stated in vendor documentation. In this note every DOCS label is second-hand: it comes
  from `docs/research/grafana-native-coverage.md` (untracked in the main checkout, read there),
  which cites the New Relic alert documentation ("null vs. zero"). I did not re-read the New
  Relic documentation, and this machine has no network access.
- UNVERIFIED: my inference, or something that needs a test in a New Relic account.

## 1. The claim

- A query `SELECT count(*) FROM X WHERE <filter>` returns no row (NULL), not 0, in a window where
  no event matches, because `WHERE` runs before `SELECT`. DOCS (via the Grafana note, section 2
  and Part C).
- The documented workaround is `SELECT filter(count(*), WHERE <filter>) FROM X WHERE <entity
  scope>`. It returns 0 in any window where the entity reports something, even if nothing matches
  the filter. If no event at all arrives, the result is still NULL and only loss of signal
  detects it. DOCS (via the Grafana note).
- What an empty (NULL) window does to a condition: with the default gap handling, an empty window
  resets the threshold-duration timer. DOCS (via the Grafana note, citing the gap-filling
  documentation). The importer does not set a fill option. REPO `core/emit/nerdgraph.py` 43-55
  builds `signal` with only `aggregationWindow`, `aggregationMethod` (`EVENT_FLOW`) and
  `aggregationDelay`.

## 2. Where the importer emits count(*)

| File:line | Builds | Signal kind | Who produces that kind |
|---|---|---|---|
| `src/importer/core/emit/nrql.py:145` (`_count_nrql`) | `SELECT count(*) FROM Log` or `Transaction` with a `WHERE` of the filters, plus `FACET` | LOG, APM (`COUNT_EVENTS`, line 33) | REPO: CloudWatch log metric filters (`sources/cloudwatch/translate.py:598`), GCP log-match conditions (`sources/gcp/translate.py:546`), Dynatrace scheduled DQL checks (`sources/dynatrace/translate.py:312`). No source sets APM today (REPO: grep for `SignalKind.APM` finds only the emitter). |
| `src/importer/core/emit/nrql.py:153` (`_heartbeat_nrql`) | `SELECT count(*) FROM <event> WHERE metricName = '...'` | HEARTBEAT | REPO: GCP metric-absence conditions (`sources/gcp/translate.py`, `_absence_condition`). |

Callers of the text: `build_nrql` (`nrql.py:163-172`), used by the NerdGraph and Terraform
emitters. Dashboards are not involved: they use `_metric_nrql` only (REPO). `sources/gcp/data_check.py:76-79` runs the LOG query with `SINCE n hours ago` and reads the
number with `or 0`, so it already treats NULL and 0 alike (REPO).

## 3. Which of these the change would affect

The heartbeat query must not change. It is a loss-of-signal detector: the condition fires on
`count(*) < 1` and on expiration when no data arrives, and an empty window being NULL is the
behaviour it relies on (REPO: the translator sets `Operator.BELOW, 1` and `NoData(notify=True)`).
Wrapping it in `filter()` would return 0 whenever the scope reports and change what it detects.
UNVERIFIED, from the REPO shape; confirm with the live test below.

The LOG/APM query is where NULL matters, by threshold direction:

- Threshold `above N` (all GCP log-match conditions, Dynatrace `>`/`>=` checks): an empty window
  cannot breach either way, so a NULL window is harmless for opening a violation. It may matter
  for closing one and for the threshold-duration timer when `occurrences` is `ALL`. UNVERIFIED.
- Threshold `below N` or `below_or_equals` (Dynatrace checks of that form; REPO
  `sources/dynatrace/translate.py:304` already adds `BELOW_NOTE` for them): a NULL window is not
  evaluated, so a condition meant to fire when the count drops to 0 may never fire. This is the
  case where the change fixes a real defect. DOCS (via the Grafana note) for the NULL behaviour;
  UNVERIFIED for the effect on a condition.

## 4. What the change would be

In `_count_nrql`, emit the count as `filter(count(*), WHERE <filters>)` and move the entity scope
to the outer `WHERE`:

```
before: SELECT count(*) FROM Log WHERE severity IN ('ERROR') AND textPayload LIKE '%x%'
after:  SELECT filter(count(*), WHERE severity IN ('ERROR') AND textPayload LIKE '%x%')
        FROM Log WHERE <entity scope>
```

Open decisions the change needs (all UNVERIFIED):

1. What the outer scope is. `CountSignal` has `filters` and `group_by` and no separate scope. An
   unscoped outer query returns 0 whenever any log line arrives in the account, which hides a
   truly silent source; a scope needs an attribute that identifies the source's logs. For GCP the
   attribute names of forwarded logs are not confirmed (REPO: `LOG_NOTE` in
   `sources/gcp/translate.py` says so), so the scope cannot be generated yet. For Dynatrace the
   same applies to `dt.process.name` and similar.
2. Whether to apply it to every LOG condition or only to `below` thresholds. Restricting it to
   `below` and `below_or_equals` changes the least and fixes the clear defect; this is the
   smallest option.
3. `FACET`: with a facet, an absent facet value produces no row, with or without `filter()`.
   UNVERIFIED.
4. A condition that carries `filter()` should keep the loss-of-signal expiration so true silence
   is still caught. REPO: `NoData` is only set from `treatMissingData` style fields today.

## 5. What would change in the repository

Golden files (REPO, checked by `grep -c "count(" ` on the files):

- `tests/golden/cloudwatch_expected.json`: no change. It is built from `describe-alarms` alarms
  without log filters, and contains no `count(` text.
- `docs/improvement/baseline.json`: no change. It holds counts and rates, no query text.

Tests that pin the current query text and would need new expected values, each a human review
(REPO, from grep):

- `tests/test_dynatrace.py:143`, `:162`, `:223`
- `tests/test_gcp.py:85`, `:99` (the log queries). Lines `:376`, `:398` and `:550` are heartbeat
  queries and must stay as they are.
- `tests/test_log_filters_and_inventory.py:98`

Documentation that quotes the query: `docs/what-migrates.md` (log-match wording), the proposal
`docs/improvement/proposals/2026-11-09-gcp-absence.md`, and `docs/research/dynatrace-native-coverage.md`.

## 6. Test plan

Offline, before any code change:

1. A table-driven test over `_count_nrql` with filters, no filters, a facet, a negated filter and
   a wildcard, asserting the exact new text. It must fail on the current emitter.
2. A test that `_heartbeat_nrql` output is byte-identical before and after the change.
3. A test that the change applies only to the chosen threshold directions (decision 2), using one
   monitor per operator.
4. The existing escaping tests (`quote_string`, `identifier`) must still cover every value that
   reaches the `filter()` clause.
5. `improve-measure` rates and the baseline must not move (the gate checks this).

Live, in the sandbox, only with the owner's approval and the cost named first (UNVERIFIED until
run):

1. Send a few log events to a test account, then run both queries for a window with no matching
   event but other events in scope, and for a window with no events. Record NULL, 0 or no row for
   each of `count(*)` and `filter(count(*), WHERE ...)`.
2. Create two disabled-then-enabled test conditions, one per form, with a `below 1` threshold, and
   observe whether each opens a violation after a silent window.
3. Repeat with an `above 0` threshold and observe whether the violation closes when the matching
   events stop, and how long that takes.
4. Check whether the loss-of-signal expiration alone gives the same result as the `filter()` form
   for the `below` case. If it does, the smaller fix is to set the expiration on those
   conditions and leave the query alone.

## 7. Recommendation

Do not change the emitter yet. First run the live test 1 and 4, because loss of signal on the
existing query might already cover the `below` case, and because the outer scope (decision 1)
cannot be written for GCP logs until the forwarded attribute names are confirmed. If the live
test shows the defect, apply the change to `below` and `below_or_equals` thresholds only, update
the six pinned test values and the affected documents, and have a person review the diff.
