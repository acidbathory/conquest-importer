Fixtures for the Dynatrace classic source.

The 21 files whose names do not start with "hostile-" are docs-derived: their JSON comes from
section 8 of docs/research/dynatrace-alerting-objects-deep.md (public Dynatrace documentation
and repositories), wrapped in the Settings API response shape. None was read from a tenant. Ids
and names are invented. The "hostile-" files are written to break the loader and the converter.
