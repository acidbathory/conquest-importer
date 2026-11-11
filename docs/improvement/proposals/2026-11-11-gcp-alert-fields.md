# Proposal: Google Cloud alert fields (pinned outputs change, needs your approval)

Branch `improve/gcp-alert-fields`, from `integration/all-threads` (0d7e4a5). Nothing was regenerated.

## What changed in plain English

The converter now keeps more of what a Google alert policy says. For the shipped fixture
`tests/fixtures/gcp/policies.json` this changes the output of exactly two conditions:

1. `ckg-log-match` (a log-match condition with a 300 s notification rate limit). Google requires that
   limit on log alerts. New Relic has no rate limit, so the condition now carries a violation time
   limit of 300 s (a violation that keeps matching is closed and opened again, which notifies again
   no more often than Google would), a line in the description, and a report note saying so.
2. `ckg-gce-cpu-high` (WARNING, `EVALUATION_MISSING_DATA_INACTIVE`). Before: the missing-data setting was
   a note only. Now: loss of signal after 600 s that closes open violations and opens none (Google
   treats missing data as not breaching). The description gains "Google severity: WARNING" and the
   documentation format, and the IR gains the tag `gcp-severity:WARNING`.

The other two fixture policies (MQL, absence) are byte for byte unchanged.

## Pinned files that fail until you approve

- `tests/fixtures/emitter-output-hashes.json`: group `convert/gcp` (IR, NerdGraph and `main.tf` for the
  two conditions above, and `report.md`).
- The saved gcp IR in the grafana byte-identity test (`test_the_saved_ir_of_the_other_sources_is_byte_identical_to_before_grafana`).
- The reason categories in `test_the_categories_of_the_other_sources_are_unchanged` (gcp now has the
  new reasons, all in "approximate conversion").

To review: run `uv run importer convert --source gcp --in tests/fixtures/gcp --out /tmp/new` and diff against
the same command on `integration/all-threads`. The full diff of both runs was read before writing this.

## Other behaviour changes (not pinned, tests updated)

- `forecastOptions` (horizon 1 to 60 hours) converts to a static condition with New Relic's predict-future
  option (`predictBy`, in beta) instead of staying manual. It is a different model from Google's forecast.
- A threshold `trigger` of several series or a percent of series stays manual with the numbers in the reason,
  except when the aggregation reduces everything to one series (then it is noted as moot).
- An absence condition now accepts any aligner or reducer in Google's enums (only "did a point arrive"
  matters) and states a `trigger` of N series or P percent: New Relic fires only when no series reports.
- ERROR severity maps to critical with a note (New Relic has critical and warning only).
- `REDUCE_PERCENTILE_50/95/99` and `REDUCE_STDDEV` convert to `percentile()`/`stddev()` only when each series
  has one point per window (alignment period of 300 s or less, no counter aligner); `ALIGN_STDDEV` needs a
  period of at least 900 s. COUNT, COUNT_TRUE/FALSE, FRACTION_TRUE, PERCENT_CHANGE, INTERPOLATE, NEXT_OLDER
  and PERCENTILE_05 stay manual, each with its own reason.
- Filters: a parenthesised group of ANDs, `one_of(...)`, `log_id("...")` and quoted label keys are read.
  Log field names with `@`, `/` or `-` are written in backticks (new `field_identifier` in `emit/nrql.py`,
  used for log and event filters only; `identifier` is unchanged).
- A file holding one policy (the layout of Google's sample repository) now loads.
- New optional `EmitOptions` fields: `description_note`, `predict_by_seconds`,
  `open_violation_on_expiration`, `close_violations_on_expiration`; new `Aggregation` values `count`, `stddev`.
  Unset, they change no existing output (the other sources' pinned outputs still pass).

## Risks and unknowns

- Creating a condition with `openViolationOnExpiration=false` and `closeViolationsOnExpiration=true`
  was not tried against New Relic (read-only rules). UNVERIFIED.
- The description is cut at 1000 characters; New Relic's real limit was not checked.
- `percentile()`, `stddev()`, `count()` and `percentage(count(*), WHERE ...)` were accepted by NerdGraph as
  NRQL (read-only, syntax only). The account holds no Google Cloud metrics, so values were not compared.
- Violation time limit as a rate limit is an approximation: it re-notifies while matches continue, but it
  also closes a violation that Google would keep open.
