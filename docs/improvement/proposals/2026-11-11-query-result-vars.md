# query_result() variables with one captured label (2026-11-11)

Branch `improve/qr-vars`. Gate: GATE PASSED.

## Change

`query_result(sum(sel) by (l))` and `query_result(sum by (l) (sel))` (also min, max, avg, count,
group) convert to `FROM Metric SELECT uniques(l) WHERE <sel>` when the regex captures exactly `l`
(`/.*l="([^"]*)".*/`). Plan and converter share `query_result_selector`. New reason
`V_QUERY_RESULT_LABEL` (regex captures a different label than the one grouped) with a remediation
rule. An `or`, a function inside, `topk` and a rate stay manual (`V_QUERY_RESULT`).

## Measure (fixtures: grafana-dashboards, grafana-dashboard-convert, grafana-dashboard-exact)

Variable reasons before and after are identical: no fixture holds a `query_result` of an
expression (grafana-dashboards 22 variables, 10 manual, 4 gated panels; convert 20, 2 manual;
exact 1, 0). The Istio dashboards (7636, 7630) and the sandbox set are not in the repository, so
the 12 variables / 41 widgets and 75 sandbox widgets are not re-measured here. Synthetic cases in
the tests go from manual to converts.

## Pinned output diff

None: no pinned fixture contains the changed shape, so no pin moves. Expected diff when the Istio
dashboards are added: the `query_result` variables change from manual (`V_QUERY_RESULT`) to
`nrql variable` with `FROM Metric SELECT uniques(destination_workload) WHERE metricName = '...'`,
and the widgets gated by `P_VARIABLE` on them become plannable.

## Not verified

The query was not run against New Relic (offline). The Istio `or` form and a regex naming a suffix
of the grouped label (`cluster=` from `source_cluster`) are refused on purpose.
