# Azure workbook parameters in KQL (2026-11-11)

- Measured on the 30 public workbooks: 91 KQL placeholders, 71 with a `{Param}`. Parameters were
  not the binding blocker. With every parameter replaced by a literal, only 1 of the 71 would
  convert; the rest stop on `let` functions, `extend` expressions, `=~`, `tostring()` and rows
  without an aggregate. Fixing those is worth more than any parameter work.
- Converted, because the meaning is exact: `| where TimeGenerated {TimeRange}` (or `:value`) is
  dropped (the dashboard time picker sets the range) and `bin(TimeGenerated, {TimeRange:grain})`
  becomes `TIMESERIES AUTO`. Only names of type 4 (time range) parameters are touched.
- Not converted on purpose: `:start`/`:end` inside `let`, dropdown/text values inside strings
  (quoting and multi-select semantics differ), scope parameters. The reason now names the
  parameters, and a remediation rule and playbook give the steps.
- `translate_kql` got an optional `time_params`; the workbook passes it only when the workbook has
  one, so test doubles with the two-argument signature keep working.
