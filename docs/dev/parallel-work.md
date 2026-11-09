# Working in parallel without clashing

Several branches can build at once if each owns its files. Merge conflicts in this repository came
from the same few shared lines every time, so those lines now have extension points.

## Rules

1. Own your directory. A workstream writes only inside its own paths (the table below) and in
   new files. Never edit another workstream's files; ask the integrator.
2. New CLI commands go in `src/importer/commands/<name>.py` with a `register(app)` function. Do
   not edit `cli.py`.
3. New taxonomy rules use `register_taxonomy(category, "^anchored pattern")` from your source
   package's `__init__`. Do not edit the lists in `core/taxonomy.py`.
4. New fixture folders use `register_fixtures(source, "tests/fixtures/<folder>")` from
   `importer.sources.base`. Do not edit `FIXTURES` in `core/improve.py`.
5. Lessons go in their own file: `docs/improvement/lessons.d/<date>-<cycle>.md`. Do not edit
   `docs/improvement/lessons.md`. Proposals already have one file per cycle.
6. Frozen shared files: `core/ir.py`, `core/emit/*.py`, `core/notification_ir.py`,
   `core/dashboard_ir.py`, `core/report_html.py`, `sources/base.py`, `sources/__init__.py`,
   `docs/improvement/baseline.json`, `tests/golden/*`. If you need a change there, write it as a
   small request (the field, why, the proposed diff) in your proposal file and stop. The
   integrator makes the change on the base branch and tells everyone to rebase. Exception: a new
   source adds one line to `sources/__init__.py`.
7. Docs: edit only your own section of `docs/what-migrates.md`, and your own research files.
8. Before asking for a merge run `scripts/improve/gate.sh`, and
   `scripts/improve/overlap.sh <base> <branch> <branch> ...` to see which files two branches both
   changed. Any overlap outside your own directory is a rule violation to fix first.
9. Rebase on the base branch at least once a day and after every integrator change to a frozen
   file. A branch more than a day behind the base gets its gate re-run before merging.

## Ownership

| Workstream | Owns |
|---|---|
| Dynatrace alerting objects | `sources/dynatrace/classic/`, `tests/*dynatrace_classic*`, `tests/fixtures/dynatrace-classic/` |
| Dynatrace dashboards, SLOs, synthetics, inventory | `sources/dynatrace/dashboards/`, `sources/dynatrace/inventory/`, `tests/*dynatrace_dashboards*`, `tests/fixtures/dynatrace-dashboards/` |
| Grafana dashboards | `sources/grafana/dashboards/`, `tests/*grafana_dashboards*`, `tests/fixtures/grafana-dashboards/` |
| Grafana notifications and data path | `sources/grafana/notifications/`, `sources/grafana/datapath/`, `tests/*grafana_notifications*`, `tests/fixtures/grafana-notifications/` |
| New Relic destination and workflow model | integrator only (frozen files above) |

A workstream that finds it needs a file another one owns uses rule 6.
