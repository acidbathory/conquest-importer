# Testing conventions

- Run everything with `uv run pytest`. Tests live in `tests/`; `assert` is allowed there.
- Prefer `@pytest.mark.parametrize` over repeated tests.
- `pytest.raises` must carry a `match=` that checks the actual message.
- Mock HTTP at the transport level; no test may call a real API or need real credentials.
- Golden files (`tests/golden/expected.json`) are reviewed by a person before they are regenerated. Review
  the diff in the pull request.
- Test callbacks may ignore arguments they must accept (`ARG` is relaxed under `tests/`).
