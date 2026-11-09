Fixtures for the Dynatrace classic source.

The 21 files whose names do not start with "hostile-" or "variety-" are docs-derived: their JSON
comes from section 8 of docs/research/dynatrace-alerting-objects-deep.md (public Dynatrace
documentation and repositories), wrapped in the Settings API response shape. None was read from a
tenant. Ids and names are invented.

The "variety-" files are synthetic objects written to exercise the converter (every object id
starts with SYNTH). The "hostile-" files are written to break the loader and the converter: huge
and non-finite numbers, deep nesting, hostile titles, duplicate ids, adversarial selectors and
items that are not objects.
