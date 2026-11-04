## What and why

## Checks
- [ ] `ruff check`, `ruff format --check`, `mypy src tests`, `pytest` and `pip-audit` pass
- [ ] Untrusted text only reaches output through the escaping helpers
- [ ] If a converter changed: golden diff reviewed by a person, and docs/what-migrates.md still true
- [ ] No keys, account ids or customer names in the diff
