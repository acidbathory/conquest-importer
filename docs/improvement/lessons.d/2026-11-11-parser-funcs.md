## 2026-11-11: cycle `parser-funcs`

| Defect class | How it hid | Check that catches the class |
|---|---|---|
| Two PromQL readers, each with its own idea of spelling (quotes, case, comments) | The AST parser read `'a'`, `SUM` and `#` comments, so the census called them parser work, but the regular-expression readers that write NRQL did not, so the same query was convertible in one place and refused in another | One `normalize_promql` called at the entry of the planner, the converter and the alert plan; `tests/test_promql_normalize.py` asserts that every spelling gives the same planner verdict and the same NRQL as the plain one, and that LogQL is never rewritten |
| A function that looks like another (`round(x, 0.5)` and NRQL `round(x, 1)`) | The second argument means a multiple in PromQL and a number of decimals in NRQL; copying the name would have drawn a plausible wrong chart | Parametrized refusal tests list the multiples that must stay manual (5, 10, 0.25, 0, a variable, an exponent) next to the powers of ten that convert |
| A refusal reason too general to give steps (`delta`, `time()`) | Both fell under a catch-all reason | Each now has its own reason text starting with the catch-all prefix (so the rule matches) and a row in `tests/test_grafana_dashboards_rules.py` |
