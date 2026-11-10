# Brief: docs

Documentation that must stay true to the code.

## Task (MANDATORY)
Write or update: `<files>`. Audience: `<practitioner / contributor / owner>`. What changed in the
code that makes it necessary: `<commits or proposal>`.

## Worktree (MANDATORY)
```
cd /Users/abal/Documents/Projects/killer-app && git fetch -q origin && \
  git worktree add .claude/worktrees/<slug> -b docs/<slug> origin/main
```
Work ONLY there. Do not push. Private folder `/tmp/<slug>/`.

## Scope (MANDATORY)
Only the named files and `tests/test_docs_user_links.py` expectations if a command or link is added.
Every command and flag you write must exist: run `uv run importer <command> --help`. Every example
output must come from a real offline run on a fixture, not from memory. Plain words, no emojis, no
claims of verification that `docs/improvement/live-evidence.json` does not carry.

## Authorisations (MANDATORY)
Offline only. Commits on your branch, small, no attribution trailers.

## Forbidden (MANDATORY)
Any live call; code changes other than doc tests; inventing flags or paths; copying text from the
vendor; secrets, tenant names or real customer data in examples; editing frozen or golden files.

## Gate (MANDATORY)
`bash scripts/improve/gate.sh` prints GATE PASSED (it runs the docs link and command tests).

## Final message (MANDATORY, at most 8 lines)
Files changed; commands you actually ran to check examples; anything in the docs you could not
verify and marked as such; gate result.
