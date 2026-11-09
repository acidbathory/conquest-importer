# Grafana dashboard fixtures

All files are synthetic. None was exported from a Grafana stack, and none holds a real host name,
address, token or person.

- The five files in this folder are the fixtures of section 9 of
  `docs/research/grafana-dashboards.md`, written from the public dashboard schema (the v2 file from
  the v2beta1 CUE file). `easy-node-overview`, `hard-service-mixed`, `legacy-rows-v12` and
  `v2-small` are well formed; `hostile.json` has wrong types and injection attempts on purpose.
  How a live v2 export stores a Prometheus query is not confirmed.
- `hostile/` holds more files that are only meant to be read by the loader and planner without a
  crash and without any of their text reaching an output unescaped: wrong value types
  everywhere, a v2 resource with injection in names and an auto grid, wrappers and lists that
  are not dashboards, and terminal escape sequences.
- Large cases (10,000 panels, 200 nested levels, 1 MB strings, 500 variables) are built in
  memory by the tests, not stored here.
