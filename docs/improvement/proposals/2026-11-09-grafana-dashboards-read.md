# Proposal: Grafana dashboards, read side and coverage plan

Branch `improve/grafana-dashboards-read` (from `integration/all-threads`). Status: waiting for the
owner. Nothing here was run against a live Grafana or New Relic account. No New Relic dashboard
is built or emitted by this branch.

## What it does

- Reads the three dashboard JSON shapes (classic, the v1 resource, the v2 resource with Grid,
  Rows, Tabs and AutoGrid layouts) from a file, a list, an API response or a folder. A dashboard
  that cannot be read becomes an item with a fixed reason; the rest load. Caps: 10,000 panels,
  200 nested levels, 5,000 dashboards, 300 characters of a title, 10,001 of an expression.
- `extract-grafana-dashboards --out`: GET only, on `/api/search` (paged), `/api/dashboards/uid/{uid}`,
  `/api/folders`, `/api/library-elements`. Host through the existing `validate_host`; token from
  `GRAFANA_SA_TOKEN`. The safeguards of `core/safe_http.py` (no redirects, status of 300 or more is
  an error, token scrubbed, response and total byte caps, deadline) plus a request cap and page
  caps. The only files written are `dashboards/<uid>.json`, `folders.json`, `library-panels.json`;
  the `meta` block is cut to folder and provisioned flag.
- `plan-grafana-dashboards --in --out`: `report.md` and `plan.json`. Every panel is placed at
  T1, T2 or T3 or stays manual with one reason; panel types follow the 24-row table of the note;
  variables, annotations, links, rows and repeats are inventoried.
- A remediation rule and a taxonomy rule for each of the 46 reasons (all anchored on
  `dashboard `), and tests that each rule wins over every registered rule.

## Tier numbers against the research

The 69 public dashboards of the note (1,404 panels) are not in the repository and no network was
used, so the note's measured percentages (21.8%, 38.9%, 47.2%) could not be re-run. What was
checked is the set of five synthetic fixtures of the note's section 9 and the query shapes of its
section 5.2.

| Fixture | Panels | T1 | T2 | T3 | Note says | Difference |
|---|---|---|---|---|---|---|
| easy-node-overview | 5 | 4 | 5 | 5 | 5 of 5 convert (T2) | none |
| hard-service-mixed | 10 | 0 | 2 | 2 | converts request-rate query A, latency p95 and p99, retries | the note counts query A of "Request rate"; a panel converts only if every visible query does, and query B (`label_replace`) fails, so the panel is manual. At query level 5 of 7 queries convert. Panel level: 2 of 10 |
| legacy-rows-v12 | 3 | 1 | 2 | 2 | singlestat and graph convert (T2); the table with `styles` and `transform` fails or degrades | none |
| v2-small | 3 | 2 | 2 | 2 | 2 widgets convert; library element reported | none |
| hostile | 5 | 1 | 1 | 1 | (no expectation) | |
| all five | 26 | 8 | 12 | 12 | | |

Each shape in the note's table 5.2 is a test row (about 80 expressions in
`tests/test_grafana_dashboards_queries.py`): the tier or the reason it stays manual.

Decisions where the note was not exact, so the numbers can differ from a re-run of its script:

- A regex matcher counts as convertible when it is RE2 (no lookahead, lookbehind or
  backreference), because the note's numbers assume RLIKE works. That is UNVERIFIED in New Relic.
- A target with a data source reference that cannot be resolved (`${DS_X}` with no `__inputs`
  entry) is unresolved and manual; the key-based guess is used only when there is no reference,
  as in the note's method.
- A panel with any transformation is manual, including a pure `organize` (the note says the
  converter may treat `organize` as renames; the plan does not yet).
- `count(...)` is T3 (the note says "T3 or manual").
- A reused result (`-- Dashboard --`) takes the tier of its source panel, one level deep.

## Risks

- The classifier reuses the alert translator's PromQL and LogQL handlers through their private
  context and failure types (`_Ctx`, `_Fail`), after rewriting rate calls, regex matchers and
  variables. A change to those private types breaks it; tests would show it. The real conversion
  in the next wave replaces this module.
- The extractor paths come from the documentation and the note, not from a live read. The
  token of `extract-grafana` (alerting roles) may not be enough: it needs dashboard, folder and
  library panel read access (note, section 3.2, UNVERIFIED).
- Nested folders: `/api/folders` lists top-level folders only, as far as the note says. A nested
  folder's title is not resolved (the dashboard keeps the folder uid).
- Tiers measure whether a conversion could be written, not whether the metric exists in New
  Relic or whether the chart looks the same.
- Mutation checks were run by hand on the extractor (allow-list, redirects) and the loader
  (depth and panel caps); the named tests failed. The model and extractor test files passed on
  their first full run after the code was written: only the import failure came before. That is a
  slip against test-first for those two files. The query, plan, report and rules tests found
  real defects on their first runs (listed in `docs/improvement/lessons.d/2026-11-09-grafana-dashboards-read.md`).

## Requests for frozen or other files

1. `tests/test_extension_points.py` (not mine): two tests assumed `commands/` has no real module and
   failed as soon as `commands/grafana_dashboards.py` existed. I changed them to look only at the
   throwaway modules they create (6 lines, in its own commit `91124de`). Every branch that adds a
   command will need the same change; the integrator should make it once on the base and drop
   this hunk.
2. `sources/grafana/translate.py` (not mine): please add a public function, for example
   `parse_promql(expr) -> Parsed | reason` and `parse_logql(expr)`, that returns the selector,
   aggregation and group-by without needing a rule context (`_Ctx`) or raising a private `_Fail`.
   Until then `dashboards/query_tier.alert_reach` is the only place that imports `_Ctx` and
   `_Fail`.
3. `core/dashboard_ir.py` (frozen, another agent): nothing needed for the read side. The next wave
   needs nullable `TIMESERIES AUTO`, `LIMIT MAX`, `variables[]` and a heading widget, as the note's
   build item 3 says.
4. `docs/improvement/baseline.json` and `FIXTURES` in `core/improve.py`: not changed. Dashboards
   are not measured by `improve-measure` because that measure converts alert exports through a
   `Source`; the dashboard fixtures are therefore not registered with `register_fixtures`.
   Decide how dashboard coverage should enter the measure when the converter exists.

## Decisions for the owner

- Should `/apis/dashboard.grafana.app/...` be read too? The note documents it but says which
  version a stack serves is unknown (its questions 1 and 4). The extractor uses only the legacy
  paths; the owner's stack decides.
- Is a pure `organize` transformation to be converted (renames and hidden columns) or stay manual?
- Should a panel with a convertible query but an unsupported widget type (gauge, bar gauge, heatmap,
  logs) count toward "converted"? The plan reports it as a separate column.
- The report and plan name panel titles and dashboard titles. Is that acceptable in a shared
  folder? Titles are escaped, not removed.

## UNVERIFIED

- That the four legacy read paths and their parameters work as documented on the owner's stack;
  the `/api/search` fields (`folderUid`, `folderTitle`) and the library-elements list shape
  (`result.elements[].model`).
- How a live v2 export stores a Prometheus query (`spec.expr`, `group: prometheus`), the grid width
  of a v2 item (24 is assumed), and the shape of `AutoGridLayout` items without positions (a
  3-column default is assumed for the plan only).
- That RLIKE is RE2 and anchored in dashboard widgets; that Prometheus counters arrive as deltas
  (which `rate()` and `increase()` T2 rely on); that `bucketPercentile` accepts the names
  converted from `_bucket`.
- That a v1 envelope's `metadata.annotations["grafana.app/folder"]` holds the folder uid.
- The gauge widget id (`gauge` in `panels.TABLE` is a placeholder), the stacked-bar id.
- That every read path applies schema migrations; the loader reads old schemas as stored.

## Gate

See the end of this file for the last gate run.

`rm -rf .mypy_cache; sh scripts/improve/gate.sh` on commit `64fc340` plus the docs of this proposal:
ruff check, ruff format, `mypy --strict src tests`, vulture, pytest (2173 passed, 24 skipped), the
measure against the unchanged baseline, and the golden check: GATE PASSED. The measure is
unchanged for every source because dashboards are not part of it. `git log --format=%B | grep -i
co-authored` prints nothing.

## Round 2 (review fixes)

Owner decisions applied: exports hold no secrets, the plan never overstates, and a regex matcher is
T1 only in the forms the alert translator accepts.

### What changed

- Secrets: `redact.py` runs on every dashboard and library panel before writing: keys such as
  `password`, `secureJsonData`, `token`, `authorization`, `headers`, `basicAuth*`, `httpHeader*` are
  blanked at any depth; URL user info, `?token=`-style values, bearer/basic values and token-shaped
  words are masked in every string. The count is printed. Folder 0700, files 0600. Planted-secret
  test over dashboards, library panels and mixed nesting.
- Export: files are `dashboard-<hash of the exact uid>.json` (the uid is recorded in `meta`), written
  with exclusive create; the whole export is built in a temporary folder and moved into place only
  when the run finished, so a stopped run leaves nothing. `RecursionError` while parsing is an
  error, not a traceback. Paging reads to an empty page (a server may cap pages), stops on a repeated
  page, and a full set of pages is an error for folders and library panels as for dashboards.
- `__elements` entries are unwrapped from `{uid, name, kind, model}` (shape unverified).
- Loader: classic with stray `elements`/`layout` is read as classic with a note; v2 `repeat` goes to
  the item's own element; elements placed twice are noted; the unplaced note counts only panels;
  duplicate uids are kept once and noted; BOM accepted, bad encoding named; `schemaVersion` with
  non-ASCII digits is ignored; plan files are skipped and `--out` inside `--in` is refused;
  more than 5,000 dashboards or 10,000 files keep the first ones and warn.
- Planner (each change can only lower a number): a panel using a variable that stays manual is
  manual; variable regexes beyond plain words (capture groups included) are manual; `timeShift` and
  `timeFrom` make a panel manual; a stat or table with mappings, overrides, a scaling unit or a
  non-latest reducer goes one tier lower (manual from T3); instant/table format, query interval and
  legend text are noted; reuse of a text panel is not T1; binary operations need identical grouping;
  a group-by variable is manual; regex matchers outside the translator's forms are manual
  ("needs RLIKE verification"); `_bucket` and `count` are found as tokens; `up * .5`, `1e3`, `1h30m`
  are read; rate calls inside quoted strings are not rewritten. Query counts reconcile:
  `queries = T1 + T2 + T3 + manual` (tested on generated plans).
- ReDoS: the two quadratic patterns are rewritten, a run of 200+ word characters is refused before
  the translator's patterns, and hostile inputs (including `[` x 9000 and `x_over_time` x 800)
  finish under a second. plan.json lists at most 1,000 panels per dashboard and says how many were
  left out; counts and inventories cover all.
- DRY: the token comes from `api_token()` of the alert extractor; limits are defined once in
  `models.py`; one `note()` helper; `Target.instant`, `fmt`, `legend` now feed panel notes.

### Tier numbers on the research fixtures, after round 2

| Fixture | Panels | T1 | T2 | T3 | Before | Why it changed |
|---|---|---|---|---|---|---|
| easy-node-overview | 5 | 4 | 5 | 5 | 4/5/5 | unchanged |
| hard-service-mixed | 10 | 0 | 0 | 0 | 0/2/2 | `service` has a lookahead regex and stays manual, so every panel that uses `$service` is manual. Single queries: 9 looked at, 5 reach T2, 4 manual |
| legacy-rows-v12 | 3 | 0 | 2 | 2 | 1/2/2 | the singlestat has value maps: one tier lower |
| v2-small | 3 | 2 | 2 | 2 | 2/2/2 | unchanged |
| hostile | 5 | 1 | 1 | 1 | 1/1/1 | unchanged (the text panel) |
| all five | 26 | 7 | 10 | 10 | 8/12/12 | the two above |

### Mutation check

29 hand-made mutants (caps, write-size, fatal handling, host check, symlink, raw library shape, v2
cap/transformations/hidden/repeat, reuse depth, undefined variables, T2 boundary, report caps,
four redaction patterns, exponent numbers, label sets, plain regex, quoted rate, display
lowering). Survivors on the first round-2 run: 4 (exclusive create, reuse depth, undefined
variables, per-dashboard manual cap). Tests were added for each; survivors now: 0 of 29. The
mutation script is not committed. The new query rows were written after the code, not before: a
slip against test-first for that part.

### Requests (files I do not own)

- `sources/grafana/translate.py`: a public parse function (selector, aggregation, group-by, no
  `_Ctx`, no `_Fail`), and a public `PROM_FEATURES` table that `query_tier.PROM_CHECKS` can reuse
  instead of restating it.
- `core/safe_http.py`: a `want` parameter on `decode_json` (list or object); `extract._decode` is a
  copy that accepts lists and treats `RecursionError` as bad JSON.
- `sources/grafana/extract.py`: `http_getter` takes a path only and has a fixed allow-list; a
  version taking query parameters and a path predicate would remove `dashboards/extract.http_getter`.
- `tests/test_extension_points.py`: unchanged request from round 1.

### Decisions for the owner

- Redaction is key and pattern based. Is it enough, or should the export also drop `datasource`
  blocks and `links` entirely?
- Value mappings, overrides and units lower a stat or table by one tier. A stricter rule would make
  them manual.
- `/api/folders` nested folders are still not read.

### UNVERIFIED (new)

The `__elements` wrapper shape; that `RLIKE` is RE2 (still the reason regexes stay manual); the
"scaling unit" list (everything except none, short, string, locale, percent); that a `timeShift`
cannot be expressed as `COMPARE WITH` in a widget.
