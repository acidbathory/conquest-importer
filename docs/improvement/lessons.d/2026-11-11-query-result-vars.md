# 2026-11-11 query_result() variables

- The plan classifier and the converter each read `query_result(...)` on their own. Both now call
  `query_result_selector` in `sources/grafana/dashboards/variables.py`, so they cannot disagree
  on which shapes convert (a plan that says "converts" while the converter refuses).
- A regex that captures a label other than the grouped one (for example `cluster` out of
  `source_cluster`) is not equivalent: it has its own reason (`V_QUERY_RESULT_LABEL`) and stays manual.
- No Istio dashboard is in the fixtures; the tests use the public shape with synthetic queries.
  Check to add when one is fetched: plan it and expect no `query_result` variable manual.
