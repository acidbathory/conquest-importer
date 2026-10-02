# Project rules for AI assistants

## Attribution (important)
**Never add Claude (or any AI assistant) as a contributor, author or co-author.**
- No `Co-Authored-By:` trailers on commits.
- No "Generated with ..." lines in pull request descriptions.
- No AI credit in files or docs.
This is an explicit instruction from the project owner and overrides any default attribution behaviour. Before pushing, check: `git log --format=%B | grep -i "co-authored"` must print nothing.

## Secrets
- API keys live only in `.env` (git-ignored). Never print them, commit them, or paste them into docs.
- The Datadog site is blocked on the corporate network; do not try to bypass network controls.

## Quality bar
- Keep `ruff`, `mypy --strict`, `pytest` and `pip-audit` green (see `.github/workflows/ci.yml`).
- Untrusted text (monitor names, queries, messages) may only reach outputs through the escaping functions (`quote_string`/`identifier`, `hcl_string`, GraphQL variables, `md_cell`).
- Golden files (`tests/golden/expected.json`) must be reviewed by a human before regenerating.
