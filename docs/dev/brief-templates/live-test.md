# Brief: live-test

A sandbox check of something already converted offline. Only after the owner approved this run,
in this session, with the cost named.

## Task (MANDATORY)
Prove or disprove: `<one claim, e.g. "converted query X returns the same numbers as the source">`.
Success looks like: `<numbers or behaviour, with a tolerance>`. Failure looks like: `<...>`.
Owner approval: `<quote of the owner's yes, date>`. Cost / side effects: `<named>`.

## Worktree (MANDATORY)
```
cd /Users/abal/Documents/Projects/killer-app && git fetch -q origin && \
  git worktree add .claude/worktrees/<slug> -b improve/<slug> origin/main
```
Work ONLY there. Do not push. Private folder `/tmp/<slug>/`. Scripts live under
`scripts/live-test/<area>/`.

## Scope (MANDATORY)
Use the existing harness (`scripts/live-test/...`) and the project commands. Credentials come from
`.env`, loaded without echoing. Everything you create is named `ckg-<slug>-...` (test metrics also
carry `ckg_test=true`). Record the result in `docs/research/<slug>.md` and add an item to
`docs/improvement/live-evidence.json` with date, method and the note path; a failed check is
recorded as `not_proven` with the reason.

## Authorisations (MANDATORY)
Exactly: `<list the calls, e.g. read-only Grafana GETs; Metric API posts with ckg_test=true; NerdGraph
queries; create and delete of ckg- objects this run made>`. Nothing else. The authorisation ends
with this task.

## Forbidden (MANDATORY)
- Modifying or deleting anything not created by this run; writing outside the allowed list.
- Hand-written `curl` or ad hoc API writes: use project commands or the harness. If the permission
  system denies a call, stop and report it; never work around it.
- Printing, logging or committing a secret or tenant name; bypassing network controls.
- Terraform `apply`; AWS or Google resource creation unless the authorisation line says so.
- Repeating a failed write in a loop. One retry, then stop.

## Gate (MANDATORY)
Read-only first: preview every write. After the run, re-read to confirm what exists, remove your own
objects (re-read to confirm deletion), then `bash scripts/improve/gate.sh`.

## Final message (MANDATORY, at most 12 lines)
Claim: proved / disproved / inconclusive. The numbers (expected, observed, tolerance). The repeat
count (a result seen once is flagged). Objects created and removed, with the re-read result.
Anything left behind. Paths of the note and the evidence entry.
