# DRY guidelines

1. Find real duplication first. Search for the second copy before abstracting; extract on the
   third use, not the second. Run `uv run vulture src` to remove dead code that looks like
   duplication.
2. Shared helpers live in `src/importer/core/`. Source-specific code
   (one package per platform under `sources/`) imports from core, never from each other.
3. Escaping has exactly one home. Untrusted text reaches output only through `quote_string` /
   `identifier` (NRQL), `hcl_string` (Terraform), GraphQL variables and `md_cell` (Markdown).
   Never re-implement or inline these.
4. Models over dicts. Use Pydantic models for any structure that crosses a function boundary.
5. Table-driven tests. Use `@pytest.mark.parametrize` rather than copy-pasted tests; share
   setup through fixtures or `conftest.py`.
6. Repeated HCL goes in a Jinja2 macro, not in repeated Python string building.
7. Prefer the standard library and existing dependencies: `functools.cache`, `itertools`,
   `dataclasses`, comprehensions / `list.extend` (Ruff `PERF` enforces the latter).

8. Registries are filled once and registration is idempotent (`register_rules`, `REGISTRY`).
9. Rules and mappings are tables (a list of tuples or a JSON file), not chains of `if`. A new
   case is a new row plus a test row.
10. A rule that is first-match must be specific. Add a test that the specific rule wins over the
    broader one before adding the broad one.
11. Before adding a helper, search for the thing it does: `grep -rn` the verbs in `core/`.
    Cross-source duplicates belong in `core/`.

See also `docs/dev/resource-and-style.md` for resource handling and formatting, and
`docs/dev/improvement-loop.md` for the loop that finds duplication and defects.

## Candidate libraries (not yet adopted; add only when a real duplicate justifies it)

| Library | Use it for |
|---|---|
| `tenacity` | One retry/backoff decorator for `httpx` calls instead of hand-written loops |
| `pydantic-settings` | Typed loading of `.env` / configuration in one place |
| `more-itertools` | Chunking, de-duplication and windowing helpers |
| `hypothesis` | Property tests for the escaping functions |
| `jscpd` (Node, run ad hoc) | Copy-paste detection across Python and Terraform |

Adding a dependency: `uv add <pkg>`, commit `uv.lock`, and confirm `pip-audit` stays green.
