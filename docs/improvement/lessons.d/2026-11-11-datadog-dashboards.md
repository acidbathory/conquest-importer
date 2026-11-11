## 2026-11-11: cycle `dd-dashboards` (steps 1 and 2)

| Defect class | How it hid | Check that catches the class |
|---|---|---|
| A converter built for alerts reused for charts: the monitor translator rejects `top`, `rollup`, `timeshift` only because a condition cannot hold them | Reusing it blindly would have turned a dashboard-only construct into a "condition" reason that sends a person to the wrong steps | Dashboard reasons are their own (`dashboard query function ...`), anchored in `dashboards/rules.py`; `tests/test_datadog_dashboards.py` checks each placeholder has steps and a playbook |
| A chart that is plausible and wrong: `sum` over a gauge adds every point in a bucket, `last` of a combined series is not `latest()` | The query runs and draws a line | `sum` carries a note on the widget and `last` is a placeholder; both are parametrized cases in the test file |
| A template variable with no tag prefix (`$scope`) looks like a filter | It stands for a whole scope text; mapping it to `IN ({{scope}})` would filter on nothing | It is applied as its default with a note; a test pins the query without a filter and one with a default scope |
| An untrusted variable name or value reaching NRQL | A name with a hyphen or quote, a default of `*' OR 1=1` | Names are checked against New Relic's pattern; values go only through the TagFilter emitters and a mark that is replaced by `IN ({{name}})` and refused if not found; `test_hostile_text_does_not_reach_a_query` |
| Numbers that move without notice | The measure is not in the improvement baseline (no fixture registered) | `test_the_real_dashboards_measure_is_pinned` pins 30 / 454 / 123 / 331 / 62 notes; a change must update it with the reason |
