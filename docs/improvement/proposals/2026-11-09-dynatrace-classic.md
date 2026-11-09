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
