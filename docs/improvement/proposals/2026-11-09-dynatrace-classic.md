# Proposal: Dynatrace classic and Davis alerting objects

Branch `improve/dynatrace-classic`, started from `integration/all-threads`. Status: waiting for
the owner. Nothing was run against a Dynatrace tenant, New Relic or any cloud. The conversion is
built from the schema tables in the Dynatrace documentation (research note
`docs/research/dynatrace-alerting-objects-deep.md`), so every claim about a real tenant is
unverified (list at the end).

## What changed

A new source `dynatrace-classic` in `src/importer/sources/dynatrace/classic/`. It registers its
source, rules, taxonomy and fixtures from its own `__init__`; no file under `sources/dynatrace/`
other than the new sub-package changed.

| Part | Files | What it does |
|---|---|---|
| Models and loader | `models.py` | Settings 2.0 objects from a file, a Settings response or a folder. One bad object becomes a manual item with a problem text made of field names only; caps on file size, file count, object count and nesting. Notification objects are cut to type, name, enabled flag and profile id at read time |
| Extractor | `extract.py`, `commands/dynatrace_classic.py` | `extract-dynatrace-classic --out`. GET only, schema ids from a constant list, host regex for `*.apps.dynatrace.com` (bearer) and `*.live.dynatrace.com` (API token), shared safeguards from `core/safe_http.py`, page, object, byte and time caps. Platform hosts read `/platform/classic/environment-api/v2/settings/objects` first and `/platform/settings/v1/objects` when it answers 404; a schema that 404s on every path is a named warning. Detector actor ids and notification secrets are dropped before writing |
| Selector parser | `selector.py`, `selector_query.py` | Hand-written, linear-time grammar with caps; produces an AST, then the meaning of each transformation (the table in section 5.1 of the note). Unknown or unsafe constructs make the whole selector manual |
| Metric map | `metric_map.json`, `metric_map.py` | Dynatrace key to New Relic signal with a status per row (documented, verified, unverified, none) and a data prerequisite per metric family. Seven rows, all unverified |
| Translator | `translate.py`, `metric_events.py`, `detectors.py`, `signals.py`, `windows.py`, `inventory.py` | Static metric events, baseline events, static Davis detectors over a simple `timeseries`; everything else a manual row |
| Rules and taxonomy | `remediation_rules.py`, `taxonomy_rules.py` | 25 anchored rules and 10 anchored taxonomy rows |
| Data check | `data_check.py`, `check-dynatrace-metrics` | Read-only count of points per term's signal; exit 2 when any converted condition has none |
| Docs | `docs/what-migrates.md` (new section), `docs/improvement/lessons.d/2026-11-09-dynatrace-classic.md` | |

Conversion choices a reviewer should check:

- `violatingSamples` n of `samples` m becomes `thresholdDuration = n * 60 s` with every window
  breaching. For n below m this is stricter than Dynatrace and the reason says so. The looser
  mapping (m minutes, at least once) was refused because one breaching minute would open an alert.
- `queryOffset` moves the aggregation delay only (`max(120 s, offset)`); more than 60 minutes is
  manual. `alertOnNoData` becomes loss of signal after `violatingSamples` minutes. `dealertingSamples`
  is noted as having no equivalent.
- Baselines use `BaselineSpec` with a fixed band of 3 standard deviations. This number is chosen,
  not derived; the reason says so and points at the `NrAiSignal` histogram.
- A metric key with a baseline model (invalid in Dynatrace) converts with a note.
- `:default(n)` and `:sort/:limit/:names` are noted and not applied (see request 2 below).
- Any filter that needs a tag, an entity id, a management zone or a regular expression is manual:
  dropping it would widen the alert.

## Result

| | Before | After |
|---|---|---|
| `dynatrace-classic` fixtures converted (`improve-measure`) | not measured | 25 of 85 (29%) |
| CloudWatch | 27 of 32 | 27 of 32 |
| GCP | 3 of 4 | 3 of 4 |
| Dynatrace (workflows and SLOs) | 2 of 8 | 2 of 8 |
| Grafana | 6 of 21 | 6 of 21 |
| Tests | 844 passed (base) | 2112 passed, 24 skipped |

The 85 objects: 25 in the 21 docs-derived files, 27 in the 2 synthetic "variety" files and 33 in
the 6 hostile files. Of the 60 manual rows, 19 are inventory (6 alerting profiles, 6 notifications, 4
maintenance windows, 2 management zones, 1 built-in setting) and 17 are objects the loader could
not read (hostile fixtures on purpose); the rest are metric events (19) and detectors (5) that
stay manual with a reason. Inventory rows depress the rate by design: the conversion of profiles,
notifications and maintenance windows is the next wave.

Gate: `rm -rf .mypy_cache; sh scripts/improve/gate.sh` printed GATE PASSED (ruff, ruff format,
`mypy --strict`, vulture, pytest, the measure against the baseline, golden files untouched). No
source's numbers moved, and no golden file or `baseline.json` was touched.

## Test order, honestly

The loader (`models.py`) was written before its tests; I then broke the notification reduction on
purpose to see the tests fail. For the rest the tests were written first, but they failed at
collection (the module did not exist), not as assertions. Assertion failures that did appear: the
metric map file refused its own comment key, a test event lacked required fields, a paging test
lost the schema on follow-up pages, three older remediation rules shadowed my reasons, and a
hostile schema id reached a reason through `repr`. Several failures were wrong expectations in my
tests, not code.

## Edits outside my own paths

- `tests/test_extension_points.py`: `load_all` now also loads `commands/dynatrace_classic.py`, so
  the test could no longer expect exactly its own two modules. It now checks order and that
  private modules are skipped. Any other branch that adds a command module needs the same change;
  expect a conflict and take either version.
- `tests/test_sources.py` and `tests/test_improve.py`: each lists every source by hand; one line
  and the sample input for `dynatrace-classic` were added. A registry-driven version would end
  this edit for the next source.
- `docs/what-migrates.md`: a new section before the closing line; no other section changed.

## Requests for frozen files (for the integrator)

1. `core/report_html.py`: a `PROFILES["dynatrace-classic"]` entry. Without it `importer report` and
   `migrate` refuse the source; the markdown report from `convert` works.
2. `core/ir.py`: a `fill` option on `Signal` (none, static value, last value) for `:default(n)`;
   a `slide_by_seconds` for `:rollup`. Without them `:default` is noted and not applied and
   `:rollup` is manual.
3. `core/emit/common.py`: `SOURCE_LABELS` and `RESOURCE_PREFIXES` entries for `dynatrace-classic`
   (otherwise the description reads "Imported from dynatrace-classic <id>" and resources are named
   `src_<id>`).
4. `cli.py` `_run_data_check` has three copies now (Google, Dynatrace, here as `check_command`);
   move it to `core/`.
5. `core/doctor.py`: a `dynatrace-classic` source choice (the token and URL checks are the same as
   for `dynatrace`, but the classic host pattern is new).

## Decisions for the owner

1. Is support for classic SaaS hosts (`*.live.dynatrace.com`, `Api-Token`, `/api/v2/settings`)
   wanted? It is a second host pattern and header scheme, added because classic and hybrid
   tenants hold these objects. It is easy to remove.
2. The baseline band of 3 standard deviations: keep, or calibrate on real data (note question Q8).
3. Count inventory rows in the conversion rate, or report them separately? Today they count as
   manual items.
4. Verification of the metric map needs a New Relic account that receives the agent data and a
   Dynatrace tenant with the same hosts; name the cost before approving.
5. Next wave: pair alerting profiles with problem notifications into workflows, and maintenance
   windows into muting rules. Both are inventory rows today.

## UNVERIFIED

- Every row of `metric_map.json` (attribute names, units, the `error` attribute, the `appName`
  and `hostname` dimensions, the `dt.*` aliases of the DQL metric names).
- The Platform settings fallback: query parameters (`schema-id`, `scope`, `add-fields`,
  `page-size`, `page-key`) and the response keys (`items`, `nextPageKey`). The classic host path,
  header scheme and `fields` parameter.
- How several `entityFilter.conditions` combine (read as AND), whether `violatingSamples` can
  exceed 60 for metric events, and which fields a real export omits or sends as null.
- That the `OUTSIDE` condition is valid only for the baseline models (taken from the detector
  docs).
- Detector input key names (`query` or `query.expression`, `alertOnMissingData`, `slidingWindow`)
  and that numbers arrive as strings (JSON numbers are also accepted).
- NRQL: `percentile`, `latest` and `count(attribute)` as alert aggregations, `filter(count(*),
  WHERE ...)` and `percentage(...)` as a condition select, whether `LIKE` ignores case, the effect
  of loss of signal after `violatingSamples` minutes, and what `:rate(1m)` means against New
  Relic's per-interval counts.
- That a selector's `~` escape covers only `"` and `~`, and that metric key namespaces are
  `builtin`, `ext`, `calc` and `func`.
- Nothing about masked secrets is relied on: notification objects are cut to four fields before
  any other code sees them.
- Fixtures cannot show that a converted query returns data or fires; `check-dynatrace-metrics`
  against a sandbox can, and needs an approved live step.


# Round 2: review fixes

An independent reviewer proved eleven defects with snippets. Merged `integration/all-threads`
first (the base already fixes `tests/test_extension_points.py`; my edit to it is dropped).

## What changed

| # | Defect | Fix | Test |
|---|---|---|---|
| 1 | `or(and(..), eq)` flattened to an IN that meant something else | `or` accepts only direct `eq` calls on one dimension; `and()`, `or()`, `not()` with no tests are manual | `test_converted_filters_mean_what_the_selector_says_for_many_small_trees` (over 300 generated trees against a small evaluator), `test_the_reviewers_or_of_and_is_manual`, `test_an_empty_filter_call_is_manual_not_no_filter` |
| 2 | Threshold scaled by the unit factor under `+`, `-`, `k / metric` | Only `metric * k`, `k * metric`, `metric / k` keep the factor, `k / metric` inverts it; anything else with a unit conversion is manual | `test_multiplying_or_dividing_...`, `test_a_constant_divided_by_the_metric_inverts_the_factor`, `test_adding_to_a_scaled_metric_is_manual`, `test_a_detector_threshold_is_scaled_too` |
| 3 | `queryOffset` 21 to 60 minutes gave a delay over New Relic's 1200 s | Cap at 20 minutes; reason, rule text and docs say 20 | `test_offsets_up_to_20_minutes_...`, `test_offsets_over_20_minutes_are_manual_...` |
| 4 | An entity id compared with a name attribute never matched | A value shaped like an entity id on a `dt.entity.*` dimension is manual; a split by an entity dimension notes that labels change from ids to names | `test_an_entity_id_filter_is_manual`, `test_splitting_by_an_entity_dimension_says_the_labels_change`, `test_an_id_shaped_value_on_a_dimension_that_is_not_an_entity_converts` |
| 5 | `OverflowError` on a 400-digit integer | Integers above 10^15 (thresholds) or 10^9 (counts) and numbers in text over 40 characters are refused before `float()`; `OverflowError` caught | `test_a_huge_detector_number_is_manual_not_a_crash`, `test_a_huge_number_in_a_metric_event_does_not_crash` (through `importer convert`) |
| 6 | Generated key `A#2` could repeat a real id | `Keys` skips every key in use | `test_generated_suffixes_never_collide_with_real_ids`, `test_generated_keys_skip_every_taken_suffix` |
| 7 | `//` accepted; scheme and path chosen by two separate checks | One trailing slash stripped once, one `fullmatch`, one result drives the scheme and the paths. A bearer token goes only to an apps host, an API token only to a live host | `test_host_patterns_refuse_every_bypass` (32 hostile addresses), `test_a_platform_token_goes_only_to_...`, `test_an_api_token_goes_only_to_...` |
| 8 | Not-found decided by the message text | `ClassicError.status`; only status 404 on the first page falls back; a 404 on a later page is an error; no schema read at all fails the run | `test_an_error_whose_text_says_404_is_still_an_error`, `test_a_404_on_a_later_page_...`, `test_when_no_schema_can_be_read_the_run_fails`, `test_the_command_exits_1_when_nothing_could_be_read` |
| 9 | 100,000-character page key and a `[` x 100000 body gave tracebacks | Page key capped at 2000; `InvalidURL` and `RecursionError` caught in my getter | `test_a_huge_page_key_is_an_error`, `test_a_deeply_nested_body_is_an_error_not_a_traceback`, `test_the_cli_never_shows_a_traceback_for_hostile_bodies` |
| 10 | Informational event types became paging conditions | `INFO`, `CUSTOM_ANNOTATION`, `CUSTOM_DEPLOYMENT`, `CUSTOM_CONFIGURATION` are manual with a rule; `davisMerge` is noted | `test_informational_event_types_are_not_paging_conditions`, `test_davis_merge_is_noted_when_present` |
| 11 | The data check ignored the condition's filters | The count uses the condition's own scope (the query is the condition's, with the select replaced by a count) | `test_a_filtered_condition_is_blind_when_only_other_rows_exist` |
| 12 | Smaller points | Gauge note on `:rate`; `/ 0` manual; repeated `splitBy` and `by:{}` names de-duplicated in order; default `dealertingSamples` (5) not noted; empty value placeholder; booleans refused as counts; a text `enabled` is not read as enabled; bidi, zero-width and control characters removed from names and keys; PARTIAL notes for `:count`, `:median`, `:value`; remediation text no longer points inside the package | `tests/test_dynatrace_classic_round2.py` |
| 12b | A user metric map | `IMPORTER_DYNATRACE_METRIC_MAP` names a file (same strict validation, read at each conversion) and `importer validate-dynatrace-map FILE` checks one. The built-in `convert` is in a frozen file, so an environment variable stands in for a `--metric-map` option | `test_a_user_map_is_used_...`, `test_the_validate_command_...` |
| owner | Inventory rows must not be misread in the rate | `importer summarize-dynatrace-classic` prints the rate and, on its own line, the listed-only rows and the rate without them (19 of 85; 25 of 66) | `test_the_summary_shows_listed_only_rows_on_their_own_line` |

Owner decisions applied: the classic SaaS host stays (with the tightened check above), the
3-deviation baseline band stays and is labelled as chosen in every baseline reason, and inventory
rows stay in the rate.

## Mutation checks

37 mutants of the code the review listed (host regexes loosened three ways, the slash strip, the
header scheme, every cap on both sides, the notification allow-list, the schema allow-list, the
length caps, the map `select` limit, the no-data timeout, the case-insensitive guard, name
capping, the offset cap, the entity-id pattern, the `or` rule, the factor inversion, key
uniqueness, the event-type set, the dealerting default, boolean refusal, nesting depth).

| | Survived |
|---|---|
| After the first round-2 tests | 9 of 37 |
| After the boundary tests added for them | 0 of 37 |

The nine were: a page cap and a total page cap that allowed one extra, the schema allow-list with
an extra entry, name capping, a key loop that stopped after one try, the detector number caps at
their exact value, a data-check count that kept the FACET, and the entity-id guard applied to
dimensions that are not entities. Mutants were run by a throwaway script outside the repository
that edits one source line, runs the eight Dynatrace classic test files and restores the file.
The first-round tests were not measured separately.

## Requests for the integrator (new)

- Shared `api_token`, `environment_url`, `is_plain_token` and the host patterns are now copied in
  `sources/dynatrace/extract.py` and here; move them to `core/`.
- `core/report.py` / `md_cell`: strip bidirectional and zero-width characters. This source removes
  them from its own names and keys, but `md_cell` is shared.
- `core/emit/nrql.py`: `Num` is emitted with `:g`, which loses precision above six digits, and
  `identifier()` uses `match` with `$`, which accepts a trailing newline; use `fullmatch`.
- `core/safe_http.py`: `decode_json` lets `RecursionError` escape for a body of nested brackets.
  This source catches it in its own getter; fix it at the source for the Google and Grafana
  extractors too.
- `cli.py`: a `--metric-map PATH` option on `convert` would replace the environment variable.
- Report: show listed-only rows on their own line (this source prints it with
  `summarize-dynatrace-classic`).

## Result

Gate: `rm -rf .mypy_cache; sh scripts/improve/gate.sh` printed GATE PASSED, 2476 passed and 24
skipped. Measure unchanged: `dynatrace-classic` 25 of 85, the others as before.

## UNVERIFIED (added)

- The New Relic aggregation delay limit of 1200 s is from the reviewer; it is not in the pages I
  read.
- The alert duration limit: New Relic pages give 2 hours for an alert event; the emitter allows 24
  hours. A Dynatrace window of up to 60 minutes fits either.
- The Dynatrace default of 5 for `dealertingSamples` and the default `:rate` interval of one
  minute.
- That an entity id always looks like `TYPE-` plus 16 hex digits.
