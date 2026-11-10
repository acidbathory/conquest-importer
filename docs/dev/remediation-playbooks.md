# Remediation playbooks and `plan-remediation`

What the importer cannot convert still has to be finished by a person. This is how the tool helps.

## Three layers

1. **Reason** (fixed sentence from a converter). Never contains text from the export.
2. **Remediation rule** (`core/remediation.py`, `register_rules`): first match wins, anchored
   pattern, a title and a few generic steps. It decides what kind of problem a reason is.
3. **Playbook** (`core/playbook.py`, content in `core/playbooks/`): attached to a rule title (or
   `title|variant`, for example a contact point type or a panel type). Holds what the item does in
   the source tool, the closest New Relic construct (or `no_equivalent` and the workaround),
   concrete steps, a template with `<metric>`, `<labels>` and `<label>` tokens, what will differ,
   measured numbers, effort in minutes and documentation links.

## Adding one

1. Add or find the rule (a reason without a rule is a test failure).
2. Add `reg("<rule title>", Playbook(...))` in the module for the area. Every field is fixed text;
   keep it ASCII. A link needs a page you fetched and a claim you read in it. Mark anything else
   `Link(..., verified=False)` or say UNVERIFIED in `differs`.
3. `tests/test_playbook.py` checks that the title exists, the fields are filled, the template uses
   only known tokens and there is a link.

## The command

```
importer plan-remediation --in <convert output folder> [--out out/remediation]
```

`--in` is the output of `convert` (reads `ir/`), `convert-grafana-dashboards` (reads
`dashboard-ir/`) or `convert-grafana-notifications` (reads `report.md`, which is the only output
that command writes besides Terraform), or a folder holding several of them. It writes
`remediation-plan.md` (top 5 fixes, groups, steps, templates, checklist) and
`remediation-plan.csv` (one row per item). Offline and read-only. Item names are untrusted: they
reach markdown through `md_cell` and the CSV through `csv_cell`, which defuses spreadsheet formulas.
Templates are filled only with names that match `[A-Za-z_:][A-Za-z0-9_:]*`.

"Items unlocked" in the top 5 is a greedy cover: the fix that completes the most items, then the
next fix given the first, where an item is complete only when every reason it has is fixed.
Effort is a planning figure (minutes per item times count), not a measurement.
