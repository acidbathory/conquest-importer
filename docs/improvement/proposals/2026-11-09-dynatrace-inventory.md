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
- Tests (618, in `tests/test_dynatrace_dashboards_*.py` and
  `tests/test_dynatrace_inventory_*.py`; round two added `..._strict`, `..._injection`, `..._copies`,
  `..._allowlist` and `..._limits`), fixtures in `tests/fixtures/dynatrace-dashboards/`
  (four scrubbed real dashboards, one classic dashboard, monitors, locations, entities, SLOs, a
  `hostile/` folder), a section in `docs/what-migrates.md`, lessons in
  `docs/improvement/lessons.d/2026-11-09-dynatrace-inventory.md`.

Gate: `rm -rf .mypy_cache; sh scripts/improve/gate.sh` prints GATE PASSED (ruff, ruff format,
`mypy --strict` on src and tests, vulture, 4604 passed and 24 skipped (after merging integration/all-threads again), measure compared with the
baseline, golden files untouched). The conversion measure is unchanged because the plans are not
registered with `register_fixtures`: their inputs are dashboards, not the workflows and SLOs that
`convert --source dynatrace` reads.

## Coverage, round two (the numbers changed on purpose)

Owner decision: the planner must not overstate. Exact now means every clause maps to NRQL with the
same meaning and nothing is dropped. The research counts (10 exact, 22 draft, 27 manual) came from
a more optimistic reading and are not a target. A reviewer showed that tiles counted as exact
dropped options (`rate`, `scalar`), ignored timeseries parameters, accepted computed fields and
sort expressions, and read `matchesValue` as `LIKE`. After the fixes, on the four real dashboards:

| File | Data tiles | Exact | Draft | Manual | Markdown | Variables e/d/m |
|---|---|---|---|---|---|---|
| AWS | 8 | 0 (was 1) | 5 | 3 (was 2) | 4 | 0/3/1 |
| Azure | 19 | 0 | 14 | 5 | 8 | 0/0/7 |
| Host | 22 | 5 | 2 | 15 | 15 | 0/0/5 |
| K8s | 10 | 0 (was 4) | 0 (was 1) | 10 (was 5) | 7 | 1/1/0 |
| Total | 59 | 5 (was 10) | 21 (was 22) | 33 (was 27) | 34 | 1/4/13 |

Every tile that moved class:

- AWS 14, K8s 1, 4, 5, 6: exact to manual. Each has `value.A = avg(m, scalar: true)`, an
  aggregation that returns one number per group instead of a series. That changes the shape of the
  result, so the reason is "the query asks for a single value (the scalar option)". K8s 1, 4, 5
  and 6 also carry `rate: 1m`, which is now a draft note (`rate()` over the same interval, result
  unverified), and all of them use `matchesValue`, which is a draft note (case and wildcard rules).
- K8s 8: draft to manual, for the same `scalar: true` (its link columns are still a draft note).
- Unchanged: the 5 Host exact tiles (`in()`, `==`, null checks, `countDistinctExact`, one plain
  sort key; no options), the 2 Host drafts (second sort key), the 14 Azure drafts (entity filters
  dropped, a variable in the limit, and a sort by the average of a series that is now stated as a
  draft note), the 5 Azure manual and the 15 Host manual tiles, AWS 1, 3, 4, 5 and 6 (drafts),
  AWS 10 and 12, K8s 10, 15, 18, 19, 20 (manual).
- Variables: 1 exact, 4 draft, 13 manual, unchanged.

If the owner wants `scalar: true` read as a helper used only to rank (the K8s tiles rank by
`value.A`), those 5 tiles become drafts and the totals are 5 exact, 27 draft, 27 manual. I did not
do it: the plan would then describe a ranking that NRQL cannot reproduce as if it could.

Other results of the same run:

- 40 of the 59 data tiles read Dynatrace-only data and are flagged "no New Relic data source"
  (AWS 3, Azure 5, Host 22, K8s 10). All 5 exact tiles are flagged.
- Markdown: 34 convert; 4 spacers marked "drop"; 2 images from `dt-cdn.net`; 8 tiles with a
  Dynatrace link. No tile in the fixtures has an unsafe-link or html flag.
- Classic dashboards (not in the research counts): markdown and header convert, metric charts are
  manual until the name map exists, other tile types and management zone filters are manual.
- Copies: identical dashboards (same raw JSON apart from name, owner and ids) are read once and
  listed by name in the report. Dashboards that differ in any field, including filters the models
  do not read, are all kept.

## How "exact" is decided

`dashboards/dql_rules.py` has the rules in its docstring. In short: a parameter or option that is
not mapped is a blocker or a named draft note; a clause under `filterOut` that would be dropped is
manual; entity clauses are found in the masked text, so a string that mentions
`dt.smartscape_source.id` is not one; commands after `limit` that NRQL would run before it are
manual; a select-all test (`$v == "*" or f == $v`) is replaced by the variable default `*` and
stated as a draft. Exact still does not prove the numbers match: that needs a live check.

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

1. `tests/test_extension_points.py`: I had changed two assertions that broke with any real command
   module; after the second merge of `integration/all-threads` the file is identical to the base, so
   there is no longer any difference to resolve.
2. `core/dashboard_ir.py` (frozen, being changed by another agent): the plan carries, per tile, the
   New Relic visualization id and the 12-column layout, and per variable the verdict, `multiple`
   and `select_all`. The emit step needs from the IR: raw-NRQL series, a table widget, billboard and
   line thresholds, variables, and an event type per widget (research section 2.10).
3. `core/safe_http.py` and `core/` (reviewer asked me to list, not touch): these are copied in the
   inventory extractor and should move to core: `inventory_token`, `environment_url`,
   `is_platform_host` and `ENV_URL` (also in `sources/dynatrace/extract.py`), `PageBudget` (also in
   the Google extractor), and an `InvalidURL` catch in `get_json` (my getter wraps it for now).
   `core/report.md_cell` does not remove control characters or escape Markdown emphasis; my code
   applies `printable` at parse time for that.
4. Dashboards as a measured source: if the owner wants the plan counts in `improve-measure`, that
   needs a decision on the baseline (frozen) and on whether a plan is an `IRMonitor`.

## Decisions for the owner

- Smartscape counts need `POST /platform/storage/query/v1/query:execute`. The extractor is GET only
  and does not read them. Allow one read-only POST, with a short allow-list of DQL strings?
- Exact copies are counted once even under different names (the name is not part of the identity).
  Keep that, or count every copy?
- Dashboard titles, queries and markdown are stored as written because the plan needs them; they
  can name internal systems. Monitor and dashboard names are replaced when they look like an
  address or a token. Entities keep only type, product-like technology names and OS. Is that enough?
- Read `scalar: true` as a ranking helper (draft) or as a different result shape (manual, as now)?
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

## Round two: what the reviewer proved and what was done

- Exact verdicts were wrong (timeseries parameters, aggregation options, summarize `by`, computed
  fields, sort expressions, order after `limit`, `matchesValue`, `filterOut`, entity markers inside
  strings, tile timeframe and segments). Fixed in `dql_rules.py`; every item has a parametrized
  test in `tests/test_dynatrace_dashboards_strict.py`.
- `analyse()` and the variable reader raised IndexError on a query with nothing in it (`|`, a
  comment, `append [ ]`). Fixed; every command now turns an unexpected error into a clean exit
  that names only its type.
- Report columns (tile kind, variable type, monitor type) are now cut and made printable when read
  and passed through `md_cell` when written.
- Page keys over 2000 characters are refused, `InvalidURL` is caught in the getter, a JSON `true`
  is not a count, and a repeated id in a listing is read once.
- The saved export is built from an allow-list (`inventory/sanitize.py`) and written 0600 in
  0700 folders. The `else body` fallback is gone.
- Copies are matched on the raw dashboard without name, owner and ids; skipped copies are listed.
- Synthetics: authentication, client certificates and regex rules need a script; the default
  status rule no longer blocks a simple monitor; a frequency that is not a positive whole number
  is missing; country codes compare in any case and a guessed location carries a reason.
- Re-instrumentation: a target's status is the worst of the rows naming it; names that match after
  changing underscores or hyphens are mapped and marked; a service counts a technology once;
  Azure is documented only for App Service plans; the table rows the extractor can never produce
  are removed and `CLOUD_APPLICATION` is requested; the location marks follow the research note
  (only Frankfurt is documented).
- Markdown flags cover raw HTML, reference definitions, autolinks, entity-encoded and split
  schemes, and the other Dynatrace hosts.
- The data-source flag covers `dt.host.*`, `dt.service.*`, `dt.process.*`, `dt.cloud.*`,
  `dt.entity` fetches; the select-all test is implemented; `Variable.multiple` and the default are
  read; the unread `subtype` field was removed from the tile model (it only marked Explore
  tiles, and the compiled `query` is what the plan reads).

Mutation check: 38 mutants of limits, flags and mappings were run against the Dynatrace tests
(`/tmp` script, not committed). 14 survived the first time (MAX_LIST_ITEMS read from the module,
layout field ranges, MAX_TILES of the extractor, MAX_DEPTH in three places, MAX_VARIABLES,
MAX_TEXT, MAX_PAGE_KEY, MAX_TOTAL_PAGES, `md_cell` on the summary name). Each now has a test with
the number written out, and a second run killed all 38.
