# Proposal: Dynatrace dashboards, synthetics and inventory (read side and plans)

Branch `improve/dynatrace-inventory`, from `integration/all-threads`. Workstream D. No network, no
tenant, no New Relic account was used. Nothing creates a New Relic object.

## What changed

New code, all in the workstream's own paths:

- `sources/dynatrace/dashboards/`: readers for Platform dashboard documents and classic
  dashboards (`models.py`), a DQL scanner (`dql_scan.py`), the query rules (`dql_rules.py`), tile,
  variable, markdown and classic planners, the plan models and report (`plan.py`), the fixed reason
  texts (`reasons.py`) and one remediation rule per reason.
- `sources/dynatrace/inventory/`: readers for synthetic monitors, locations, entities and SLO lists
  (`models.py`), the read-only extractor (`extract.py`), the re-instrumentation plan
  (`reinstrument.py`), the synthetic monitor plan (`synthetics.py`), three data tables with a
  DOCS or UNVERIFIED status on every row, and the reasons and rules.
- `commands/dynatrace_inventory.py`: `extract-dynatrace-inventory`, `plan-dynatrace-dashboards`,
  `plan-dynatrace-reinstrumentation`, `plan-dynatrace-synthetics`.
- Tests (357, nine files `tests/test_dynatrace_dashboards_*.py` and
  `tests/test_dynatrace_inventory_*.py`), fixtures in `tests/fixtures/dynatrace-dashboards/`
  (four scrubbed real dashboards, one classic dashboard, monitors, locations, entities, SLOs, a
  `hostile/` folder), a section in `docs/what-migrates.md`, lessons in
  `docs/improvement/lessons.d/2026-11-09-dynatrace-inventory.md`.

Gate: `rm -rf .mypy_cache; sh scripts/improve/gate.sh` prints GATE PASSED (ruff, ruff format,
`mypy --strict` on src and tests, vulture, 2106 passed and 24 skipped, measure compared with the
baseline, golden files untouched). The conversion measure is unchanged because the plans are not
registered with `register_fixtures`: their inputs are dashboards, not the workflows and SLOs that
`convert --source dynatrace` reads.

## Coverage against the research

The planner reproduces the research counts on the four real dashboards (fixtures are copies with
the tenant id and UUIDs replaced).

| File | Data tiles | Exact | Draft | Manual | Markdown | Variables e/d/m |
|---|---|---|---|---|---|---|
| AWS | 8 | 1 | 5 | 2 | 4 | 0/3/1 |
| Azure | 19 | 0 | 14 | 5 | 8 | 0/0/7 |
| Host | 22 | 5 | 2 | 15 | 15 | 0/0/5 |
| K8s | 10 | 4 | 1 | 5 | 7 | 1/1/0 |
| Total | 59 | 10 | 22 | 27 | 34 | 1/4/13 |

The tile ids of each class are the ones the research lists (`test_the_named_tiles_have_the_researched_verdicts`).
No difference to explain. Further results of the same run:

- 40 of the 59 data tiles read Dynatrace-only data and are flagged "no New Relic data source":
  AWS 3, Azure 5 (Smartscape and Davis tiles), Host 22, K8s 10. All 10 exact tiles are flagged,
  as the research says. The 11 Azure and AWS cloud-metric tiles that do have a documented
  New Relic counterpart are drafts and are not flagged; the planner has no metric name map and
  does not create one (another workstream).
- Markdown: 34 convert; 4 spacers are marked "drop"; 2 images from `dt-cdn.net`; 8 tiles with a
  Dynatrace link (3 Azure, 1 Host, 4 K8s), which is the research's 4 `/ui/` plus 4 `dt-url.net`.
- Classic dashboards (not in the research counts): markdown and header convert, metric charts are
  manual until the name map exists, other tile types and management zone filters are manual.

## How "exact" is decided

Exact means every clause maps to NRQL unchanged by the rules in section 2.9 of the research. It does
not mean the numbers match. Known gaps that the plan does not flag: Dynatrace `matchesValue()` and
`contains()` wildcard and case rules against `LIKE`; time bucketing; `scalar` and `rate` options.
`REVIEW` steps in the rules say to compare numbers. A live data check is the only test of that.

## Risks

- The query reader is a scanner with fixed patterns, tuned on four files. A real tenant will have
  `makeTimeseries`, `fetch logs`, `bin()`; those are manual ("source not read") by design. It
  errs towards manual: an unknown command, filter shape or aggregation is manual, never exact.
- Untrusted text: reasons are constants (a test asserts every reason in a plan belongs to
  `reasons.ALL`); titles, ids and names go to JSON as data and through `md_cell` in reports.
  `md_cell` does not escape Markdown emphasis or headings, only table characters and brackets.
- Hostile input: sizes (`MAX_TEXT` 100,000 characters, 500 tiles, 100 variables, depth 64), no
  recursion on nesting, and time-bound tests for long chains. One recursion bug was found by the
  test and fixed (lessons file).
- The extractor stores scripts of synthetic monitors reduced to structure; free text (a validation
  pattern, a URL path) is kept. Review exports before sharing.
- `test_extension_points.py` needed a change (below).

## Requests for the integrator (not made by me)

1. `tests/test_extension_points.py` (not my file, not frozen): two assertions assumed that no real
   command module exists (`load_all(...) == [...]` and `== []`). Any new command module breaks
   them. I changed them to look only at the modules the test creates (two lines). Other workstreams
   will hit the same; take one version.
2. `core/dashboard_ir.py` (frozen, being changed by another agent): the plan already carries, per
   tile, the New Relic visualization id (`viz.line`, `viz.billboard`, `viz.table`, `viz.pie`,
   `viz.bar`, `viz.area`, `viz.bullet`, `viz.markdown`) and the 12-column layout (`column`, `width`,
   `row`, `height`), and per variable the verdict. The emit step needs from the IR: raw-NRQL
   series, a table widget, billboard and line thresholds, variables, and an event type per widget
   (research section 2.10).
3. `core/safe_http.py`: `PageBudget` now exists in the Google and the inventory extractors. A third
   use would be the time to move it. Not requested yet.
4. Dashboards as a measured source: if the owner wants the plan counts in `improve-measure`, that
   needs a decision on the baseline (frozen) and on whether a plan is an `IRMonitor`.

## Decisions for the owner

- Smartscape counts need `POST /platform/storage/query/v1/query:execute`. The extractor is GET only
  and does not read them. Allow one read-only POST, with a short allow-list of DQL strings?
- Exact copies are counted once even under different names (the name is not part of the identity).
  Keep that, or count every copy?
- Dashboard and monitor names are kept in exports unless they look like an address or a token
  (`safe_name`). Entities lose names, ids and addresses. Is that enough?
- Classic metric charts are manual until the name map exists. Should they be drafts with the
  Dynatrace metric key listed, so the map can fill them later?
- Check whether the operating-system list of the New Relic infrastructure agent should be added to
  the data files (the plan notes the question for AIX and similar, it does not answer it).

## UNVERIFIED

From the code docstrings and the data files, none confirmed on a tenant or on a New Relic account:

- Document content path `.../documents/<id>/content` and its response shape; the list response keys
  `documents` and `nextPageKey`; the filter text `type in ('dashboard')`.
- Whether a platform token can call the classic Configuration API, the synthetic API and the
  Entities API on the `apps` host; the scopes each needs; the entity page size of 500.
- Where the technology of a service is reported on a Smartscape node (the plan uses the classic
  `serviceTechnologyTypes`).
- Row-height factor 1/2 and the rounding of column edges (research section 2.8); whether New Relic
  enforces a widget height of 12.
- `viz.bullet` as a gauge, `viz.area` for time bars.
- Every row of `technology_agents.json` and `entity_types.json` with status UNVERIFIED (frameworks
  and servers, IIS, C++/Rust/Erlang/Elixir, on-host integrations, browser, mobile, Lambda, GCP,
  process groups, cloud applications) and every city in `synthetic_locations.json` except
  Ashburn, Washington and Mumbai.
- The step monitor locator format; the direction of `followRedirects` against
  `treat_redirect_as_failure`; the alert condition resource for availability and loading time; a
  scripted API monitor as a stand-in for network availability monitors.
- A technology-to-agent choice for any Dynatrace technology name not in the table is reported as
  "not in the technology table" and never guessed.

## Honest notes on method

- Test first was kept only in part. The models and the extractor were written with their tests and
  passed at once; I then mutated the code to check that the tests can fail (one mutation, the id
  check in `_url`, did not fail any test because a second layer repeats it; I added a direct test).
  For the planner the tests with the research numbers existed before the module did, but the first
  failure was an import error. Details are in the lessons file.
- Fixtures: the four dashboards come from the owner's tenant. The tenant id appeared in two
  Markdown links and every "select all" token is a UUID; both are replaced. `grep` for the tenant
  id, UUID shapes and `@` finds nothing else.
