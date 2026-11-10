# Brief: fixer

Fix exactly one blocker. Test first. Do not leave the worktree.

## Task (MANDATORY)
Blocker: `<exact reason pattern or census construct>`; affects `<N>` items in `<source>` (measured
by `<command>` on `<date>`). Expected effect: `<metric>` goes from `<a>` to at least `<b>`. If you
believe the fix is impossible or unsafe, stop and say why; do not substitute another change.

## Worktree (MANDATORY)
```
cd /Users/abal/Documents/Projects/killer-app && git fetch -q origin && \
  git worktree add .claude/worktrees/<slug> -b improve/<slug> origin/main
```
Work ONLY in `.claude/worktrees/<slug>`. Do not touch main or any other worktree. Do not push.
Private temp folder: `/tmp/<slug>/` (never plain `/tmp`).

## Scope (MANDATORY)
Read first: `docs/dev/parallel-work.md`, `docs/dev/dry-guidelines.md`,
`docs/dev/resource-and-style.md`, `docs/dev/improvement-loop.md`, `<files>`.
You may edit: `<owned paths>` and new files. New commands go in `src/importer/commands/`, taxonomy
via `register_taxonomy`, fixtures via `register_fixtures`. Lessons go in
`docs/improvement/lessons.d/<date>-<slug>.md`; a proposal in
`docs/improvement/proposals/<date>-<slug>.md` with before and after numbers.
Frozen (write a request in the proposal instead of editing): `core/ir.py`, `core/emit/*.py`,
`core/notification_ir.py`, `core/dashboard_ir.py`, `core/report_html.py`, `sources/base.py`,
`sources/__init__.py`, `docs/improvement/baseline.json`, `tests/golden/*`, `tests/fixtures/*hashes.json`.

## Authorisations (MANDATORY)
Offline code, tests and docs only. Fakes for HTTP (`httpx.MockTransport`). Commits on your branch,
small, failing test first, no attribution trailers.

## Forbidden (MANDATORY)
- Any call to Grafana, New Relic, Dynatrace, AWS, Google or the web; the browser; Terraform
  (`init`, `plan`, `apply`); `importer apply*`, `migrate`, `rollback`.
- Editing, skipping or loosening an existing test or check; raising a threshold; editing an
  expectation to fit new output. If an existing test fails because the behaviour is meant to
  change, say so in the proposal and stop.
- Regenerating golden or hash files, editing the baseline. If pinned output changes, write the
  exact diff in the proposal and leave the files.
- Printing or storing a secret. Untrusted text reaches output only via `quote_string`,
  `identifier`, `hcl_string`, GraphQL variables, `md_cell`.
- `Co-Authored-By` or any AI credit in commits, files or docs.

## Gate (MANDATORY)
`bash scripts/improve/gate.sh` must print GATE PASSED, run from the worktree root. The only
accepted exception is a pinned-output test you listed in the proposal. Run it last; paste the final
lines.

## Final message (MANDATORY, at most 12 lines)
1. Blocker and verdict: fixed / partly / not possible.
2. Branch and commit list.
3. Gate: last lines of its output.
4. Scoreboard or measure numbers before and after.
5. Pinned output or frozen file requests, with the proposal path.
6. What you could not verify, and anything you doubt.
