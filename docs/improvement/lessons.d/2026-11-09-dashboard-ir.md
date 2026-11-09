# Lessons: dashboard IR extension (2026-11-09)

- Serialised IR files are output too. The byte-identity requirement covers `dashboard-ir/*.json`, and a pydantic model dumps every default. New optional fields on a model that already ships need a wrap serializer that drops them while they hold their default, or every existing IR file changes.
- Hash the whole output folder, not only the emitters' files. The recorded hashes in `tests/fixtures/dashboard-output-hashes.json` cover the IR, NerdGraph, Terraform and the report for both sources.
- A frozen file can force the shape of a new field. `core/emit/nrql.py` reads `Series.period_seconds` as an int, so `TIMESERIES AUTO` is a separate `auto_period` flag rather than a nullable period.
- A schema fixture of names (from `terraform providers schema -json`) lets a test check generated HCL offline, which `terraform validate` cannot do without a provider download.
- Keep unconfirmed output names out of the output: `decimals`, `stacked` and every NerdGraph widget option are modelled but not written until read back in the sandbox.
- A parametrized test that builds invalid models in its parameter list fails at collection, not at the assertion. Pass the keyword arguments and build the model inside the test.
- Slip on test-first: the new test files were written before the code, but they failed on import errors, not on assertions, because the new names did not exist. The emitter tests did fail on assertions once the IR existed.
