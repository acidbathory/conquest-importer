# Contributing

Thanks for helping. This is a prototype, so the most valuable contribution is a **real failure**:
something that did not convert that you expected to, or a conversion that returned wrong numbers.
Use the [feedback template](../../issues/new/choose).

## Fork and open a pull request

1. Fork the repository on GitHub and clone your fork.
2. Create a branch, make your change and run the checks below.
3. Push the branch to your fork and open a pull request against `main`.

## Set up

```sh
git clone https://github.com/acidbathory/conquest-importer.git && cd conquest-importer
uv sync            # or: python3 -m venv .venv && . .venv/bin/activate && pip install -e . pytest ruff mypy
uv run pytest
```

## Before you open a pull request

CI runs all of these; run them locally first (details in [docs/dev/linting.md](docs/dev/linting.md)):

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv run pip-audit
```

## Rules that matter

- **Ruff is the only Python linter and formatter.** `mypy --strict` stays green.
- **Untrusted text** (alarm names, queries, messages) may reach output only through the escaping
  helpers: `quote_string` / `identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables,
  `md_cell` (Markdown) and `e()` (HTML report). See [docs/dev/dry-guidelines.md](docs/dev/dry-guidelines.md).
- **Golden files** (`tests/golden/`) are reviewed by a person before they are regenerated.
- **No secrets in the repo**, ever. Keys come from the environment.
- Every item the converters cannot handle needs an honest reason *and* manual steps
  (`src/importer/core/remediation.py`); a test fails if one is missing.
- Don't add AI tools as authors or co-authors on commits or pull requests.

## Where things live

```
src/importer/
  cli.py                     commands
  core/                      shared: the IR, emitters, apply, rollback, report
  sources/cloudwatch/        CloudWatch reader, converters, ingest Terraform
tests/                       unit tests, fixtures, golden files
docs/                        user docs; docs/dev has contributor docs
```

Adding a new source platform: one folder under `sources/` with `load()` and `translate()`, the same
as the existing two. See [ROADMAP.md](ROADMAP.md).
