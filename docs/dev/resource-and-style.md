# Resources, memory and style

Rules for code that has to run unattended and keep running. Each rule names how it is checked.

## Formatting

- `ruff format` decides layout; do not align by hand or fight it. Line length is 100.
- `ruff check` must stay clean. Complexity is capped at 15 (rule C901): when a function reaches
  it, extract a named helper that does one thing, as `_reducer` and `_whole_windows` were
  extracted from `_metric_condition`.
- Every function has type hints and `mypy --strict` passes. Use `Any` only where JSON enters the
  program, and narrow it at once into a model or a plain type.
- Use `pathlib`, f-strings and comprehensions. No bare `except:`; catch the narrowest exception.
- A `# noqa` needs the rule code and a reason on the same line.
- Comments say why, not what. Docstrings are short and describe the contract.

## Resources and memory

- Anything that must be closed is opened in a `with` block: files, `httpx.Client`, boto3 sessions
  where the library supports it, temporary directories, subprocess pipes. Where the caller needs
  a long-lived object, offer a context manager (see `http_getter` in
  `sources/gcp/extract.py`). `pyproject.toml` turns `ResourceWarning` into a test failure, so a
  leak shows up in the test that caused it.
- Every loop over remote data has a bound: a page cap (`MAX_PAGES`), an item cap, a size cap
  (`MAX_INPUT_BYTES`), a timeout on every network call and subprocess.
- Module-level state must not grow. A registry (`register_rules`, `REGISTRY`) is filled once and
  is idempotent: registering the same item again adds nothing. Never append to a module-level
  list or dict inside a function that runs per request or per asset.
- Caches are bounded: `functools.lru_cache(maxsize=N)`, never `cache` or an unbounded dict, and
  only for pure functions of hashable input.
- Do not hold a large payload longer than needed. Parse, convert to the smaller model, drop the
  raw object. Read a file once. Prefer generators and `itertools` for pipelines over building
  intermediate lists of the whole estate.
- No `__del__`, no global singletons that hold clients, no threads or processes left running.
- Temporary files go in `tempfile` helpers or `tmp_path` in tests and are removed.
- Checking: `tracemalloc` snapshot tests for anything that loops over many assets (see
  `docs/dev/testing.md`), and run `uv run pytest -W error` before a release.

## Errors and secrets

- Errors that reach the user are safe to print: no keys, tokens, URLs with credentials or raw
  response bodies. Pass untrusted text through `printable` before echoing it.
- Fail early and clearly on bad input (`ValueError` with the fix in the message); do not return
  partial results silently.
- Secrets are read from the environment at the moment of use and never stored in a model, a log
  line, a manifest or a report.

## Structure

- One job per module; sources import from `core`, never from each other.
- Prefer a pydantic model to a dict that crosses a function boundary.
- Prefer early returns to nesting. Keep a function short enough to read without scrolling.
- Constants have one home. A magic number that appears twice becomes a named constant next to
  the rule it belongs to.
- New behaviour comes with a test that fails without it. Prefer `parametrize` over copies.

## Before you commit

`scripts/improve/gate.sh` runs lint, format check, `mypy --strict`, vulture, the tests, the
conversion measure against the baseline and the golden-file check. It must print GATE PASSED.
