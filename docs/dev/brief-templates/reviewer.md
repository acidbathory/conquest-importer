# Brief: reviewer

Independent review of a diff. Read-only. You are given the diff, not the author's conclusion.

## Task (MANDATORY)
Review `git diff origin/main...improve/<slug>` in the worktree `.claude/worktrees/<slug>`. Claimed
effect (for context only, do not assume it is true): `<one line>`.
Check in this order: correctness (try to break it: empty, huge, hostile text); escaping of
untrusted text; safety guards; DRY and structure; resources and bounds; tests (they must fail
without the change; look for weakened or deleted assertions first, in `tests/` before `src/`);
docs (`docs/what-migrates.md`, CLI help); mutation (change one operator or constant in the new
code and confirm a test fails).

## Worktree (MANDATORY)
Do not create a new branch. `cd /Users/abal/Documents/Projects/killer-app/.claude/worktrees/<slug>`
and work from there. Private temp folder `/tmp/<slug>-review/`. Do not modify any tracked file.

## Scope (MANDATORY)
The diff, the files it touches, and their tests. Say what you did not look at.

## Authorisations (MANDATORY)
Read files; run `git diff`, `git log`, `uv run pytest`, `uv run ruff check .`,
`uv run mypy --strict src tests`, and small throwaway Python snippets that import the code.

## Forbidden (MANDATORY)
Editing or committing anything; Terraform of any kind (`init` included); any network call; any
`importer apply*`, `migrate`, `rollback`; printing secrets; touching other worktrees.

## Gate (MANDATORY)
Run `bash scripts/improve/gate.sh` and report the result as measured, not the author's.

## Final message (MANDATORY)
Findings, most serious first, each: `file:line`, the input that breaks it, the fix you would
make, severity (blocks merge / should fix / nit). Then: what you could not verify. No praise,
no summary of the change.
