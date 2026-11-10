# Live round trip of community Grafana dashboards: Grafana Cloud, extract, convert, New Relic

Run on 2026-11-10 against the owner's Grafana Cloud stack (read and create only). Status: **steps 1 to 3 done; the New Relic apply and verification (steps 4 and 5) were blocked by the permission system and are not done.** Everything below marked MEASURED came from the live stack and the offline converter; nothing is claimed about New Relic acceptance.

## What was blocked

`importer apply-dashboards` (even without `--apply`, a dry run that reads `.env`) was denied by the Claude Code auto mode classifier with reason "Modify Shared Resources", three times, so no New Relic dashboard was created and no NRQL was run. Nothing was worked around. To finish: run (from the worktree `.claude/worktrees/live-grafana-dash`, with `.env` loaded)
`importer apply-dashboards --in out/live-grafana/conv-ckg --account-id $NEW_RELIC_ACCOUNT_ID --apply --confirm-account $NEW_RELIC_ACCOUNT_ID` and the same for `conv-builtin` (its IR names already carry the `ckg-` prefix), then record the guids under `new_relic_dashboards` in `out/live-grafana/manifest.json` as `{id, name}`.

## Published to Grafana (MEASURED)

Folder `ckg-importer-test`, six dashboards, uid `ckg-<grafana.com id>`, title prefixed `ckg-`, datasource inputs pointed at `grafanacloud-prom`. Script: `scripts/live-test/grafana-sandbox/publish_dashboards.py`; uids in `out/live-grafana/manifest.json`. Sources and licence note: `out/live-grafana/sources.md` (grafana.com gives no licence field; files kept in gitignored `out/`).

| id | dashboard | panels (excl. rows) | chained variables |
|---|---|---|---|
| 1860 | Node Exporter Full | 124 | job, nodename, node (3 deep) |
| 13332 | kube-state-metrics-v2 | 42 | cluster, node, namespace; constant data source |
| 14282 | Cadvisor exporter | 6 | host, container |
| 9628 | PostgreSQL Database | 32 | namespace, release, instance (`query_result`) |
| 9614 | NGINX Ingress controller | 12 | namespace, controller_class, controller, ingress |
| 4701 | JVM (Micrometer) | 27 (legacy `rows` layout) | application, instance, pools |

`extract-grafana-dashboards` read 19 dashboards, 3 folders (6 ckg- plus the 13 built-in; 47 values redacted). The command has no folder filter, so the export was split by title into `extract-ckg` and `extract-builtin` afterwards. No built-in dashboard was modified.

## Conversion (MEASURED, after the fix below)

| set | dashboards | panels | exact | approximate | manual (placeholder) | dashboards emitted |
|---|---|---|---|---|---|---|
| ckg- (6 community) | 6 | 243 | 0 | 3 | 240 | 1 (kube-state-metrics) |
| built-in (13) | 13 | 245 | 39 | 7 | 199 | 9 |

A dashboard whose panels are all manual gets confidence manual and is not emitted (5 of 6 community ones, 4 of 13 built-in). `plan-grafana-datapath --dashboards` ran on both sets: 3 of 3 and 5 of 5 converted tiles have a known data path.

Why community dashboards barely convert (counts from the plan reports):
- 202 of 243 community panels: "uses a variable that stays manual". Chained variables (`label_values(m{job="$job"}, x)`) are manual ("depends on another variable", 12), and `query_result()` and regex variables too, so every panel using `$node`, `$instance` or `$namespace` stays manual. Node Exporter Full: 124 panels, 0 converted.
- 37 panels: "data source could not be told" (13332 refers to a constant variable `$datasource`); fixed, see below.
- Then comparison or vector matching (15), transformations (8), unknown query shape (9), functions (7), gauge/bargauge types (9).
- Built-in dashboards: variable stays manual 178, `without()` 59, transformations 57, LogQL parser/unwrap 39, vector matching 33, other data sources 26.

## Bug found and fixed

A constant variable holding a data source name (`$datasource` = `Prometheus`, the shape of the 30M-download kube-state-metrics dashboard) was not resolved, so its panels were manual with "data source could not be told". Fixed in `src/importer/sources/grafana/dashboards/datasources.py` with a failing test first (`tests/test_grafana_dashboards_plan.py`); effect: community T2/T3 panels 1 to 3. The remaining manual panels are the documented design limits above, not bugs.

## Not done (needs the blocked apply)

New Relic acceptance of each widget (RLIKE, PERCENTAGE units, TIMESERIES bucket limits, widget types, layout), widget-count versus panel-count check, and whether any NRQL returns data (expected: none for Prometheus metrics, since no Prometheus data is in the account). Cleanup script `scripts/live-test/grafana-sandbox/cleanup_dashboards.py` is written, not run; it deletes exactly the manifest entries and refuses names without `ckg-`.
