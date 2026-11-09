# Adding a source platform

The Google Cloud package (`src/importer/sources/gcp/`) is the smallest complete example. A source
is a package that provides:

| File | Job |
|---|---|
| `models.py` | Pydantic models for the export, and `load_*` with a size cap |
| `translate.py` | One IR monitor per condition; anything not understood is `Confidence.MANUAL` with a reason |
| `remediation_rules.py` | Manual steps for every manual reason, registered with `register_rules` |
| `extract.py` | Read-only export with an allow-list of calls; writes no secrets |
| `__init__.py` | `Source` registered in `sources/base.py` |
| a verified-names file | Only names seen arriving in New Relic; everything else carries a "not confirmed" note |

Steps, in order:

1. Write a research note in `docs/research/<platform>-native-coverage.md` with evidence levels.
2. Add a scrubbed fixture under `tests/fixtures/<platform>/`.
3. Write models, translator, rules and tests. A test fails when a reason has no remediation.
4. Add the fixture folder to `FIXTURES` in `src/importer/core/improve.py` and, with the owner's
   approval, regenerate `docs/improvement/baseline.json`.
5. Add the report profile (`core/report_html.py`) and the CLI `--source` choice.
6. Verify metric names on a live sandbox. Name the cost first; the owner approves anything that
   creates cloud resources or grants IAM.
7. Update `docs/what-migrates.md` and the README roadmap.
