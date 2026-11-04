# Linting and checks

Single reference for every check. CI (`.github/workflows/ci.yml`) and `.pre-commit-config.yaml`
run the same commands; keep all three in step.

## Python: Ruff, always

Ruff is the only Python linter and formatter. Don't add flake8, black, isort or pylint.

```sh
uv run ruff check .          # lint (add --fix for safe autofixes)
uv run ruff format .         # format (CI runs `ruff format --check .`)
uv run mypy src tests        # types, strict; Ruff does not replace this
uv run pytest
uv run pip-audit
uv run vulture src --min-confidence 80   # dead code
```

Rule set is in `pyproject.toml` (`[tool.ruff.lint]`): `E F I B S UP SIM C4 PIE PERF RET RUF PT
DTZ T20 ARG C90`, max complexity 15.

- Fix the code rather than silencing the rule. If a `# noqa` is unavoidable, name the rule and
  give a reason: `# noqa: C901 - single-pass parser`.
- Per-file ignores belong in `pyproject.toml` with a comment saying why.
- Never ignore `S` (security) findings on code that handles untrusted text.

## Terraform

```sh
terraform fmt -check -recursive scripts
tflint --chdir scripts/live-test/aws        # likewise .../services
```

`out/` is generated and git-ignored, so don't lint or commit it.

## YAML and GitHub workflows

```sh
uv run yamllint -d relaxed .github .pre-commit-config.yaml
uv run actionlint
```

## JavaScript / TypeScript

None exists today. If any is added, use ESLint with a flat config (`eslint.config.js`) and add it
to CI and pre-commit in the same change.

## Shell

No shell scripts are tracked today. If one is added, run `shellcheck` on it and add it to CI.
