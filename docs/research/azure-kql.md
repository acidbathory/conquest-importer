# Azure scheduled query rules: the KQL subset translator

What converts, how it is written in NRQL, what each table needs before the NRQL returns data, and
what stays manual. Code: `src/importer/sources/azure/kql_lex.py`, `kql_parse.py`, `kql_lower.py`,
`kql_nrql.py`, `kql_tables.py`, hooked in by `logs.py`. Measure: `scripts/analysis/azure_kql_measure.py`.
Status of every New Relic fact: only `plugin.type = 'azure'` on forwarded logs is documented
(`azure-log-forwarding.mdx`, azure-datapath.md section 4). Everything else below that names an
attribute or an event for an agent table is UNVERIFIED.

## Measurement (offline, same method as azure-baseline.md)

| Set | Rules | Converted before | Converted after | Data path of the converted rules |
|---|---|---|---|---|
| Official examples (34 files, 15 distinct rules) | 15 | 0 | 7 | needs-agent 7 |
| Live rules (7 rules, 9 with API-version copies) | 9 | 0 | 6 | unknown 5, needs-log-forwarding 1 |

Placeholders never count; a converted rule is confidence approximate (nothing here is exact).
Remaining manual: official 6 bare `Heartbeat` counts (no filter that tells the rows apart from other
logs) and 2 dynamic thresholds; live 1 last-seen check (`max(TimeGenerated)` compared with `ago()`,
an absence check) and 2 dynamic thresholds.

## Grammar read

`let name = <literal | ago() | span | dynamic([...])>;` then `Table | stage | stage ...`.

| Stage | Read as |
|---|---|
| `where` | predicate: `== != < <= > >=` (numbers for the ordered ones), `contains has startswith endswith` and their `!` forms, `in !in` (list, `dynamic([..])` or a `let` list), `between (a .. b)`, `matches regex`, `isempty isnotempty isnull isnotnull`, `TimeGenerated > ago(x)` (dropped: the rule window is the range), `and or not()` and parentheses |
| `extend`, `project`, `project-rename` | only `alias = column` (a rename) |
| `summarize` | `count countif dcount dcountif avg sum min max avgif sumif minif maxif percentile stdev any`, optional alias, `by column, bin(TimeGenerated, span)` |
| `where` after `summarize` | one comparison of a result column with a number, read as the threshold (see below) |
| `take limit top order sort render project*` after the aggregation | ignored, with a note |
| `count` | `summarize count()` |

## Mapping to NRQL (all over `Log`)

| KQL | NRQL | Note |
|---|---|---|
| `count()` | `count(*)` | an empty window is NULL; a below or equals rule gets the null-safe form |
| `countif(p)` | `filter(count(*), WHERE p)` | 0 for an empty window when other logs exist |
| `dcount(c)` | `uniqueCount(c)` | both are estimates |
| `percentile(c, 95)` | `percentile(c, 95)` | both are estimates |
| `avg sum min max stdev` | `average sum min max stddev` | |
| `any(c)` | `latest(c)` | one possible value |
| `*if(c, p)` | `filter(f(c), WHERE p)` | |
| `by c` | `FACET c` | only when `c` is a rule dimension, or the result `where` form |
| `bin(TimeGenerated, X)` | the aggregation window | X may be finer than the window only when the aggregate composes (count and sum with Total, min with Minimum, max with Maximum); Azure aligns bins to the epoch, New Relic to the clock |
| `a or b`, `not`, `between` | `OR`, De Morgan pushed to the leaves, `>= AND <=` | inside `filter(..., WHERE ...)` |
| `x matches regex "r"` | `x RLIKE r'.*(?:r).*'` | RLIKE matches the whole value (New Relic docs, RE2); KQL finds the pattern anywhere |
| `isnotempty(x)` | `(x IS NOT NULL AND x != '')` | |
| `x contains "t"` | `x LIKE '%t%'` | case: see `N_FILTER_CASE`; `has` matches whole terms, LIKE any substring |

Plain shapes (`==`, `!=`, text matches and `in` on text, `count()` or one aggregate of a plain
column) keep the older IR form (`CountSignal` filters, `TagFilter` scope), so earlier output is
unchanged. Everything else is an NRQL aggregate in `NrTarget.select` built only from `quote_string`
and `identifier`; a pattern NRQL cannot hold (a `%` in LIKE, a quote in a regex) becomes a manual
reason instead of being escaped away.

Semantic notes written on every converted rule where they apply: negated terms (`!=`, `!in`,
`!contains`, `not`) also match records with the column missing in KQL, NRQL `!=` skips them; the bin
alignment; the result-column threshold (Azure fires when any row passes, New Relic opens one
violation per facet value); dcount, percentile and any; ignored `take/top/order`; an `ago(x)` that
differs from the window; and the failing-periods note already present (m of n is stricter in New
Relic because the windows must be consecutive).

## The rule decides what is compared

`timeAggregation`, `metricMeasureColumn`, `operator`, `threshold`, `dimensions` and `failingPeriods`
come from the rule. The measured column picks the aggregate (by alias, or the only one). With no
`summarize`, `Count` counts rows and any other aggregation applies to `metricMeasureColumn`, whose
name may contain spaces or `%` (`% Processor Time`) and is then written in backticks. A rule that
counts result rows (timeAggregation Count after a `summarize ... by`) is a count of groups and is
manual, except the case of "any row passes" (`Count > 0`, or `>= 1`) with a `where` on the result
column: that comparison becomes the New Relic threshold and the `by` columns become the facets.
Dynamic thresholds stay manual (azure-monitor-alerting.md section 4.4).

## Data path of each table (`kql_tables.py`)

| Class | Tables | `data_path` | Query shape |
|---|---|---|---|
| Diagnostic and activity logs | AzureDiagnostics, AzureActivity, AzureMetrics, SigninLogs, AuditLogs, AAD*, AppService*, AGW*, AZFW*, AZKV*, AKS*, ADF*, names ending in `Logs` | `needs-log-forwarding` | `plugin.type = 'azure'` kept; Event Hub forwarder plus a diagnostic setting (the activity log uses the template flags) |
| Agent tables | Perf, InsightsMetrics, Heartbeat, Event, Syslog, WindowsEvent, SecurityEvent, CommonSecurityLog, ConfigurationData, Update*, W3CIISLog, ContainerLog*, Kube*, VM*, App Insights tables (AppRequests ...) | `needs-agent` | no forwarder filter; New Relic's agent or an OpenTelemetry collector must send equivalent data; event type and names UNVERIFIED |
| Workspace-generated and unknown | Usage, Operation, Alert, Sentinel tables, anything else | `unknown` | no documented route; unknown tables keep the forwarder assumption the importer always made |

A count over an agent or workspace table with no `where` and no `by` column is refused: once the
table name is gone nothing tells its rows apart from other logs.

## Stays manual, with the operator in the reason

`join`, `lookup`, `union`, `mv-expand`, `mv-apply`, `parse`, `parse-where`, `extend`/`project`
with a function (`extract()` with regex captures, `tolower()`, `strcat()`), `materialize`, a `let`
that defines a table or function, `make-series`, `series_*`, `evaluate`, `scan`, `distinct`, `search`,
`find`, cross-resource `workspace()` `app()` `adx()` `cluster()`, case-insensitive `=~`, `_cs`
operators, aggregates such as `arg_max` `make_set`, a column compared with another column, a
`take`/`top` before the aggregation, and an absence check (`max(TimeGenerated) < ago(10m)`). The
reason is `kql query cannot be converted: <operator> has no New Relic form`, and the remediation
steps (Azure KQL join or union, parsing or expansion, time series or absence check, cross-resource
query or function) are in `remediation_rules.py` and `playbooks.py`.

## NRQL validity

The NRQL of each shape in `tests/test_azure_kql.py` was sent read-only through NerdGraph
(`nrql` query with `SINCE 5 minutes ago`) and every one parsed without a syntax error. This checks
syntax only: the query returns no rows because no Azure log is in the account, so the attribute
names are still UNVERIFIED.
