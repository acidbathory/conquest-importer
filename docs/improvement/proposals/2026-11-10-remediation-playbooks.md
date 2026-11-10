# Proposal: a full way to finish every item that did not convert

New files and additive rules only. No frozen file, golden file, hash file or baseline changed.

## What was added

- `core/playbook.py` and `core/playbooks/*`: 180 playbook keys (82 distinct playbooks, several shared by related rule titles) keyed by remediation rule title (the rule
  engine still decides what a reason is). Each has what the item does, the closest New Relic
  construct or "no equivalent" and the workaround, steps, a template, what differs, measured numbers
  where we have them, effort and documentation links.
- `commands/plan_remediation.py` and `core/remediation_plan.py`: `plan-remediation --in <convert
  output> [--out]` writes `remediation-plan.md` and `.csv`.
- `sources/grafana/dashboard_notes/`: six new remediation rules for dashboard-level losses that had
  none (links, annotations, interval and data source variables, repeated rows, tabs, widget cap).
- `docs/dev/remediation-playbooks.md`.

## Before and after (items = things that did not convert, counted as the plan counts them)

| Set | Items | With a rule before | With a rule now | With a full playbook before | now |
|---|---|---|---|---|---|
| 19 real Grafana dashboards | 371 | 290 | 371 | 0 | 371 |
| Grafana fixtures (alerts, dashboards, notifications) | 63 | 55 | 63 | 0 | 63 |
| Grafana rules2 and exact-dashboard fixtures | 12 | 12 | 12 | 0 | 12 |
| CloudWatch, GCP, Dynatrace fixtures | 95 | 95 | 95 | 0 | 95 |

Top 5 fixes on the 19 real dashboards, by items unlocked (greedy, cumulative): the 60 widgets over
the page limit (1 step, 2 minutes each), comparison or vector matching in 47 more, sum() of a gauge
in 30, time and range functions in 17, and the variables that stayed manual (54 more once the panel
queries are done).

## Limits

- Documentation links were fetched on 2026-11-10 (all 25 returned 200) and the claims they carry
  (nested query and subquery limits in alert conditions, lookup tables not usable in alerts, nested
  variables plan limits, muting schedule options, log parsing rules, destination types) were read in
  them. Not verified: `bucketPercentile` inside an alert condition, the UI menu names beyond what
  the pages show, and that every template returns the same numbers as Grafana. Templates were not
  run against any account; the gauge `sum()` numbers (4x, 41x) and the alert latency (+~5 minutes)
  come from the earlier live tests in `docs/research/`.
- Notifications are read from `report.md` because `convert-grafana-notifications` writes no IR; a
  change to that report's headings breaks the reader (a test runs the real command to catch it).
- Effort per item is a planning figure.
