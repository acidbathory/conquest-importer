# Azure source inspection (rule 9), 2026-11-16

PROGRESS: yes. Lever with sampled ceiling >= 2pp: conditional visibility as dashboard pages (+3.8pp), see the table. Details are in the final message; pins are in azure-corpus-manifest.md.

Baseline (fresh corpus, converter from worktree integrate-1111):
- Alerts 694 of 812 (85%): metric 661/696, log (KQL) 0/83, activity log 33/33. AMBA only, formulaic.
- Workbooks: 647 dashboards read (23 more abort the run), 11504 widgets. Data widgets (scoreboard definition: converted + manual items) 665 of 8396 = 7.9%. Raw count of query/metrics items: 6519 (10.2%).
- Manual items by reason: conditional visibility 4936, ARG 448, ADX 239, other-template group 90, static JSON 85, merge 76; the rest is a flat KQL tail (parameters in text about 1500, let, arg_max, distinct, join, parse_json, take).

Experiments (each re-ran the converter on a mutated copy; ceilings are on data widgets, denominator 8396):
- strip conditionalVisibility: 665 -> 984 (+319, +3.8pp)
- plus time-range parameter forms and literal substitution of other parameters: -> 1103 (+438 total, +5.2pp; parameters add +119 = 1.4pp on top)
- inline scalar lets: +0
- AMBA tag-override log alerts (36): drop the arg("") lookup and inline the threshold; only 2 of 34 convert (0.25pp of 812), the rest hit todynamic(Tags), parse, datetime_diff
