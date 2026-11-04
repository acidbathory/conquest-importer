# DRY guidelines

1. **Find real duplication first.** Search for the second copy before abstracting; extract on the
   third use, not the second. Run `uv run vulture src` to remove dead code that looks like
   duplication.
2. **Shared helpers live in `src/importer/core/`.** Source-specific code
   (`sources/cloudwatch`, `sources/datadog`) imports from core, never from each other.
3. **Escaping has exactly one home.** Untrusted text reaches output only through `quote_string` /
   `identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables and `md_cell` (Markdown).
   Never re-implement or inline these.
4. **Models over dicts.** Use Pydantic models for any structure that crosses a function boundary.
5. **Table-driven tests.** Use `@pytest.mark.parametrize` rather than copy-pasted tests; share
   setup through fixtures or `conftest.py`.
6. **Repeated HCL goes in a Jinja2 macro**, not in repeated Python string building.
7. **Prefer the standard library and existing dependencies**: `functools.cache`, `itertools`,
   `dataclasses`, comprehensions / `list.extend` (Ruff `PERF` enforces the latter).

## Candidate libraries (not yet adopted; add only when a real duplicate justifies it)

| Library | Use it for |
|---|---|
| `tenacity` | One retry/backoff decorator for `httpx` calls instead of hand-written loops |
| `pydantic-settings` | Typed loading of `.env` / configuration in one place |
| `more-itertools` | Chunking, de-duplication and windowing helpers |
| `hypothesis` | Property tests for the escaping functions |
| `jscpd` (Node, run ad hoc) | Copy-paste detection across Python and Terraform |

Adding a dependency: `uv add <pkg>`, commit `uv.lock`, and confirm `pip-audit` stays green.
