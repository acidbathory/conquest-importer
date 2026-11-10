# Brief: researcher

A sourced answer from public vendor documentation. No live access.

## Task (MANDATORY)
Question: `<one precise question>`. The decision it feeds: `<what the answer changes>`. Already
known / ruled out: `<list with file paths>`. Output file: `docs/research/<slug>.md` (new file).

## Worktree (MANDATORY)
```
cd /Users/abal/Documents/Projects/killer-app && git fetch -q origin && \
  git worktree add .claude/worktrees/<slug> -b research/<slug> origin/main
```
Work ONLY there. Do not push. Private temp folder `/tmp/<slug>/`.

## Scope (MANDATORY)
Public documentation and the repository. Read `docs/research/` for what is already established;
do not repeat it. Mark every claim DOCS (URL and date read), REPO (file), MEASURED (a recorded live
result, with its note) or UNVERIFIED. Nothing UNVERIFIED may be written as fact. Where documents
disagree, say so and give both.

## Authorisations (MANDATORY)
WebSearch, WebFetch, reading the repository, writing the one research file, one commit on your
branch (no attribution trailers).

## Forbidden (MANDATORY)
Any call to a customer or sandbox tenant; credentials; bypassing a network block; a browser;
editing code or tests; inventing function names or endpoints. Do not paste long vendor text:
summarise and link.

## Gate (MANDATORY)
`bash scripts/improve/gate.sh` still passes (documentation links and line lengths are checked).

## Final message (MANDATORY, at most 10 lines)
The answer in two sentences; the three facts that matter with their evidence tag; what remains
UNVERIFIED and the cheapest live check that would settle it; the file path.
