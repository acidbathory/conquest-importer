# Azure workbook KQL gaps: let, extend, tostring, =~ (2026-11-11)

Branch `improve/azure-kql2`. Code: `kql_parse.py`, `kql_nrql.py`, `kql_lower.py`,
`dashboard_kql.py`, `dashboard_rules.py`, `dashboard_playbooks.py`.

## What converts now (and only when the meaning is exact)

- `let name = Table | stages; name | ...`: the let text is inlined when the name is used exactly
  once and starts the next statement (chains work). Used twice, unused, a function, or `toscalar`
  stays a placeholder with the `let` reason.
- `let x = <scalar>` was already inlined; a `let` that holds `where TimeGenerated {TimeRange}` now
  works because the time-range filter may end at a `;`.
- `extend a = <arithmetic>` over columns and numbers with `+ - * /` and brackets, used as the
  argument of `sum`, `avg`, `min` or `max` (written inside the NRQL aggregate). Refused with a
  reason: `%`, integer division (KQL divides two integers as integers, NRQL does not; one operand
  must be written with a decimal point), division by a column (a zero denominator reads
  differently) or by zero, and use of the value in `where` or `by`. Alerts do not take it.
- `tostring(column)` in `summarize ... by` and as the whole of an `extend` is the column itself
  (a FACET shows the same groups). In a comparison or around `parse_json(...)` it stays manual.
- `=~` stays manual by design: no NRQL equality ignores case and the `(?i)` RLIKE flag is not
  verified. New remediation rule and playbook "Azure workbook KQL case-insensitive comparison".
- Rows without an aggregate stay manual (a table of raw rows is not a chart of one value).

## Measured on tests/fixtures/azure-workbooks (458 widgets)

Before: 273 converted, 185 placeholders. After: 274 converted, 184 placeholders.
Of the 91 KQL placeholders before, 67 name a workbook parameter. Replacing every parameter by a
literal, the next reasons were: table/function `let` 43 (almost all with `join`, `union`, `top` or
`toscalar`, which have no NRQL form), `{`-less syntax gaps 10, string escapes 9, rows without an
aggregate 10, `=~` 3. Few of the real queries are plain; the converted one is the
`by tostring(ResponseCode)` chart.

## Pinned output change (left for the integrator)

`tests/test_azure_dashboard_corpus.py`, `EXPECTED["public templates"]`:

```
-    "public templates": (30, 458, 273, 185),
+    "public templates": (30, 458, 274, 184),
```

Widget `Requests` of `azure-api-management-analytics`:

```
before: placeholder "function tostring() has no New Relic form (only a plain column is read here)"
after:  SELECT count(*) AS 'count_' FROM Log WHERE plugin.type = 'azure'
        FACET ResponseCode TIMESERIES AUTO
```

No golden file, hash file or baseline entry changes.
