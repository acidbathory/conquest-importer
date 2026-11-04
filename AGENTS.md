# AGENTS.md

Rules for AI assistants working in this repository. See also `CLAUDE.md` and `HANDOFF.md`.

- Claude should not be added as a contributor on GitHub (no `Co-Authored-By:` trailers, no
  "Generated with" lines in commits or pull requests).
- Python code is checked with ruff (lint) and `mypy --strict`, plus `pytest` and `pip-audit`, as in
  `.github/workflows/ci.yml`. Any JavaScript/TypeScript code is checked with ESLint.
  User-facing docs are in `README.md` and `docs/` (keep `docs/what-migrates.md` true when converters change).
  Details: `docs/dev/linting.md`, `docs/dev/dry-guidelines.md`, `docs/dev/testing.md`.
- Any infrastructure that carries cost must be clearly highlighted. Costs must be reported daily,
  with a dedicated budget alarm that does not exceed $10.
- Folder layout: the parent folder is `Documents/*`.
  - `Documents/Projects` is the key folder for client interactions.
  - `Documents/Courses` is for MOOCs the owner is doing (Udemy etc.).
  - `Documents/Projects/Career` is for career planning material (promotion to Principal SC etc.).
  - `Documents/Projects/schneider-electric` holds the key reference slides for any slides made here.
- All research tasks, web crawling and searching should be done by agents. Low-risk tasks should
  NOT prompt the owner further for curl access.
