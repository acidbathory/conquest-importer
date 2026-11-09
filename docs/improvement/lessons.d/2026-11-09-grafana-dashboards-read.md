## 2026-11-09: cycle `grafana-dashboards-read`

| Defect | How it hid | Check that catches the class |
|---|---|---|
| A rule pattern `\S+` for the data source label did not match "azure monitor" or "google cloud monitoring" | The first examples all used one-word labels | The rules test loops over every label the code can produce (`OTHER_DATASOURCES` values and every panel type name), not over hand-picked examples |
| A panel placed after a collapsed row took the row's title as its section | The nesting test ended in `nested == [] or all(...)`, which cannot fail | Assertions state exact values; no `or` that is true when the data is empty. The hostile fixture pins the section of the panels after a collapsed row |
| A report grew with the number of dashboards (25 manual rows each, titles of 300 characters) | Bounds were set per dashboard, never for the whole report | A test builds 400 dashboards and checks the report size and the "more in plan.json" line. Bound every table in a report, not only every loop |
| Generic code called `str.format` on every reason template and failed on `${name:csv}` | The test ran only over the reasons the fixtures produced | Templates are filled with `replace("{label}", ...)` in tests; a reason text may contain braces |
| A shared test assumed `commands/` holds no real command | It passed until the first command module was added | `test_extension_points.py` now looks only at the throwaway modules it creates. The edit is outside this workstream's paths and is listed in the proposal |
| A plan written next to the export would have been read back as dashboards | `plan.json` has `panels` and looks like a classic dashboard | The command prints a sibling folder (`<out>-plan`) as the next step, and an end-to-end test runs the printed command as printed |
| Tests written after the code, first run all green | Test files were written first, but they failed only on import | Mutation check: break the allow-list, the redirect rule, the depth cap and the panel cap and confirm the named tests fail (done for the extractor and the loader) |

## Open

- Tier numbers come from fixtures and from the query-shape table in the research note. The 69
  public dashboards of that note are not in the repository and nothing was downloaded, so the
  note's measured percentages were not reproduced.
- The classifier reuses the alert translator through its private context and failure types. A
  public function there would remove that coupling (listed in the proposal).

## Round 2 (review)

| Defect | How it hid | Check that catches the class |
|---|---|---|
| Whole dashboard bodies, with data source passwords, headers and tokens, written to disk at mode 0644 | The extractor was tested for the Grafana token, never for secrets inside the data it saved | A test plants a secret in every shape (URL user info, key names, query strings, bearer text, nested lists, library panels) and asserts it is in no file; files 0600, folder 0700 |
| File names from the uid lost data on a case-insensitive file system | Tests used lowercase uids | Names come from a hash of the exact uid; a test asserts the names differ case-insensitively |
| A stopped run left a partial export that the planner planned | Errors were tested, the leftover folder was not | Build in a temporary folder and rename at the end; a test asserts nothing is left after a fatal error |
| The plan placed panels above what they depend on (manual variable, time shift, mappings) | Each rule was tested alone, never "what does this panel depend on" | A table of dependencies with a test per row; counts reconcile (`queries = tiers + manual`) on generated plans |
| A shortcut (regex softening) made the plan more optimistic than the translator | The note's numbers assumed it | Regex forms are exactly those of the translator's own table (`WORD`, `PREFIX`, `SUFFIX`, `BOTH`) |
| Substring tests for `_bucket` and `count` | Fixtures had no label value containing them | Token-based detection and tests with the word inside a label value and as a metric name |
| Quadratic regex while the docstring said "linear" | Timing tests used inputs the patterns handle well | Hostile inputs per pattern (`[` x 9000, `x_over_time` x 800); the claim in the docstring is the bound that was tested |
| Mutants survived: exclusive create, reuse depth, undefined variables, per-dashboard manual cap | Coverage looked complete | A hand-made mutation list run before the gate; each survivor gets a test (29 mutants, 0 survivors now) |
