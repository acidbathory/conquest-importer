# Proposal: replace instead of translate (quickstart map)

New files only; no frozen file changed. `sources/grafana/dashboards/quickstarts.json` (22 entries,
read from the public newrelic-quickstarts repository on 2026-11-10), `quickstart_map.py` (matching
and the metric-name overlap check) and `commands/grafana_quickstarts.py` (`plan-grafana-quickstarts`).

Run over the 19 real dashboards in live-grafana-dash `extract-all`: 5 of 19 get a native replacement
verdict. 2 on the same Prometheus data path with at least half of the dashboard's metric names in the
quickstart (NGINX Ingress 9614, cAdvisor 14282 to Kubernetes (Prometheus), 3 of 6 names), 3 only
after changing collection (kube-state-metrics v2, JVM Micrometer, PostgreSQL). Node Exporter Full is
only "related" (18 of 228 names), as the research measured.

Limits: the Grafana id to quickstart pairing is a judgement (docs/research/grafana-complex-dashboards-repos.md
5.3); only the same-data-path entries are built on the same metric names (`mapping_verified`). Install
route and licence of the quickstarts were not checked. `tests/test_emitter_byte_identity.py` fails on
the grafana alert hash in this worktree (hash test, not touched by this change).
