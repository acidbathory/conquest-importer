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
