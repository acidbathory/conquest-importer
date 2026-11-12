# Contributing

This is a prototype, so the most useful contribution is a real failure: something that didn't
convert when you expected it to, or a conversion that returned wrong numbers.
Use the [feedback template](../../issues/new/choose).

The four contributions that help most are described in [Help us make progress](docs/user/contribute.md):
scrubbed exports, a Prometheus read, a live check in a New Relic sandbox, and an NRQL review.

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

CI runs all of these; run them locally first:

```sh
uv run ruff check . && uv run ruff format --check .
uv run mypy src tests
uv run pytest
uv run pip-audit
```

## Rules that matter

- Ruff is the only Python linter and formatter, and `mypy --strict` stays green.
- Untrusted text (alarm names, queries, messages) may reach output only through the escaping
  helpers: `quote_string` / `identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables,
  `md_cell` (Markdown) and `e()` (HTML report).
- A person reviews golden files (`tests/golden/`) before they are regenerated.
- Never put secrets in the repo. Keys come from the environment.
- Write docs and messages in plain language: say the point directly, skip decorative bold and
  filler. The [humanizer patterns](https://github.com/blader/humanizer) list what to avoid.
- Every item the converters cannot handle needs an honest reason and manual steps
  (`src/importer/core/remediation.py`); a test fails if one is missing.
- Don't add AI tools as authors or co-authors on commits or pull requests.

## Where things live

```
src/importer/
  cli.py                     commands
  core/                      shared: the IR, emitters, apply, rollback, report
  sources/<source>/          one folder per source (cloudwatch, gcp, grafana, dynatrace):
                             reader, converters, data-path files
tests/                       unit tests, fixtures, golden files
docs/                        user docs and research notes
```

To add a source platform, create one folder under `sources/` with `load()` and `translate()`, as
the existing ones do. See the [roadmap](README.md#roadmap).

## License

By contributing you agree that your contribution is licensed under the
[Apache License 2.0](LICENSE), the same as the project.
