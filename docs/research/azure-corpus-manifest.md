# Azure corpus manifest (2026-11-16)

All MIT (LICENSE files read in each clone). Shallow, blobless, sparse clones under /tmp/community/azure-src/.

| Repo | URL | Commit | Licence |
|---|---|---|---|
| Azure/azure-monitor-baseline-alerts (AMBA) | https://github.com/Azure/azure-monitor-baseline-alerts | ec5e5f3acfcdc52773110517b1f51bd06cf766de | MIT, Copyright (c) Microsoft Corporation |
| microsoft/Application-Insights-Workbooks | https://github.com/microsoft/Application-Insights-Workbooks | cc8922da42fcf1ef5ae308e076d9a7dc69698655 (same pin as tests/fixtures/azure-workbooks) | MIT, Copyright (c) Microsoft Corporation |
| Azure/azure-rest-api-specs (specification/monitor only; read for facts, not added to the corpus) | https://github.com/Azure/azure-rest-api-specs | 6a42ee6dd4a2c09f9f03eba31cc13ab20e434353 | MIT, Copyright (c) 2017 Microsoft |
| Azure/Azure-Monitor-Workbooks | https://github.com/Azure/Azure-Monitor-Workbooks | does not exist (404); Application-Insights-Workbooks is the workbook repo | n/a |

Alert corpus: 821 AMBA policy definitions rendered with /tmp/community-tools/arm_render.py (own ARM renderer, placeholder ids, parameter defaults, numeric/bool coercion by fixtypes.py) into 812 distinct rules (696 metric, 83 scheduled query, 33 activity log; 3 duplicates dropped; 2 action groups and 4 processing rules read as non-conditions). 47 AMBA json files did not parse and were skipped (non-policy files). Note AMBA is formulaic (one vendor), so alert percentages are not an estate estimate.
Workbook corpus: 678 .workbook files = all 708 in the repo minus the 30 fixture files. 23 aborted the whole run (IRVariable "enum item values must be unique") and were quarantined in /tmp/community/azure-in-wb-bad; 655 files / 647 dashboards were converted. Path map: /tmp/community-tools/wb_map.tsv.
Outputs: /tmp/community/azure-out/{alerts,wb,exp-*}; scoreboard /tmp/community/azure-score/.
