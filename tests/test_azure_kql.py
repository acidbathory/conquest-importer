"""KQL subset: the parser, the NRQL written for each shape (expected text worked out by hand), the
data path of each table, and what stays manual with the exact operator named."""

from pathlib import Path
from typing import Any

import pytest

from azure_helpers import log_rule
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, Operator
from importer.sources.azure.kql_lex import KqlSyntax, KqlUnsupported, tokenize
from importer.sources.azure.kql_parse import And, Cmp, Empty, InList, Or, parse
from importer.sources.azure.kql_tables import classify
from importer.sources.azure.models import load_alerts
from importer.sources.azure.translate import translate, translate_alert

ROOT = Path(__file__).resolve().parent.parent
FORWARDED = "FROM Log WHERE plugin.type = 'azure'"


def one(alert: Any) -> Any:
    (monitor,) = translate_alert(alert)
    return monitor


def notes(monitor: Any) -> str:
    return " | ".join(monitor.reasons)


# -- tokenizer and parser --------------------------------------------------------------------


def test_the_tokenizer_reads_spans_strings_and_hyphenated_operators() -> None:
    kinds = [(t.kind, t.text) for t in tokenize("T | mv-expand x | where a !in ('q\\'r', 5m)")]
    assert ("id", "mv-expand") in kinds and ("id", "!in") in kinds
    assert ("str", "q'r") in kinds and ("span", "5m") in kinds


def test_a_string_escape_other_than_quote_and_backslash_is_refused() -> None:
    with pytest.raises(KqlUnsupported, match="string escape"):
        tokenize('T | where a == "x\\ny"')
    with pytest.raises(KqlSyntax, match="not closed"):
        tokenize("T | where a == 'x")


def test_where_terms_become_a_flat_list_and_the_time_filter_is_dropped() -> None:
    q = parse('T | where TimeGenerated > ago(1h) and A == "x" | where isnotempty(B)')
    assert q.table == "T" and q.time_filter == 3600
    assert q.rows == [Cmp("A", "==", "x"), Empty("B", empty=False)]


def test_or_and_in_lists_and_let_values_are_read() -> None:
    q = parse('let codes = dynamic(["a", "b"]); let n = 7; T | where C in (codes) or D > n')
    (row,) = q.rows
    assert isinstance(row, Or)
    assert row.items == (InList("C", ("a", "b")), Cmp("D", ">", 7.0))


def test_and_binds_tighter_than_or() -> None:
    (row,) = parse("T | where A == 'x' or B == 'y' and C == 'z'").rows
    assert isinstance(row, Or) and isinstance(row.items[1], And)


def test_summarize_reads_aliases_by_columns_and_bins() -> None:
    q = parse("T | summarize n = count(), p = percentile(L, 95) by bin(TimeGenerated, 5m), R")
    assert [(a.alias, a.fn, a.column, a.arg) for a in q.aggs] == [
        ("n", "count", "", None),
        ("p", "percentile", "L", 95.0),
    ]
    assert [(g.column, g.bin_seconds) for g in q.groups] == [("TimeGenerated", 300), ("R", None)]


def test_ordering_after_the_aggregation_is_ignored_but_take_before_it_is_not() -> None:
    q = parse("T | summarize count() by R | order by R | take 5 | project R")
    assert q.ignored == ["order", "take", "project"]
    with pytest.raises(KqlUnsupported, match="take"):
        parse("T | take 5 | summarize count()")


# -- NRQL for each shape ---------------------------------------------------------------------


@pytest.mark.parametrize(
    ("query", "cond", "expected"),
    [
        (  # or
            'AzureDiagnostics | where Category == "A" or Category == "B"',
            {},
            f"SELECT filter(count(*), WHERE Category = 'A' OR Category = 'B') {FORWARDED}",
        ),
        (  # isnotempty and not() pushed down to a leaf negation
            'AzureDiagnostics | where isnotempty(Resource) and not(Level == "Info")',
            {},
            "SELECT filter(count(*), WHERE (Resource IS NOT NULL AND Resource != '') "
            f"AND Level != 'Info') {FORWARDED}",
        ),
        (  # regex: KQL matches anywhere, NRQL RLIKE matches the whole value
            'AzureDiagnostics | where Message matches regex "err(or)?"',
            {},
            f"SELECT filter(count(*), WHERE Message RLIKE r'.*(?:err(or)?).*') {FORWARDED}",
        ),
        (  # between
            "AzureDiagnostics | where DurationMs between (100 .. 500)",
            {},
            f"SELECT filter(count(*), WHERE (DurationMs >= 100 AND DurationMs <= 500)) {FORWARDED}",
        ),
        (  # a let value and a number comparison
            "let slow = 400; AzureDiagnostics | where DurationMs > slow",
            {},
            f"SELECT filter(count(*), WHERE DurationMs > 400) {FORWARDED}",
        ),
        (  # countif
            'AzureDiagnostics | summarize countif(ResultType == "500")',
            {"timeAggregation": "Total"},
            f"SELECT filter(count(*), WHERE ResultType = '500') {FORWARDED}",
        ),
        (  # dcount with simple where terms kept in the outer WHERE
            'AzureDiagnostics | where Category == "x" | summarize dcount(CallerIp)',
            {"timeAggregation": "Maximum", "metricMeasureColumn": "dcount_CallerIp"},
            f"SELECT uniqueCount(CallerIp) {FORWARDED} AND Category = 'x'",
        ),
        (  # percentile over one bin the size of the window
            "AzureDiagnostics | summarize percentile(Latency, 95) by bin(TimeGenerated, 5m)",
            {"timeAggregation": "Maximum", "metricMeasureColumn": "percentile_Latency_95"},
            f"SELECT percentile(Latency, 95) {FORWARDED}",
        ),
        (  # sumif on a column
            'AzureDiagnostics | summarize s = sumif(Bytes, Level == "Error")',
            {"timeAggregation": "Total", "metricMeasureColumn": "s"},
            f"SELECT filter(sum(Bytes), WHERE Level = 'Error') {FORWARDED}",
        ),
        (  # any() is the latest value
            "AzureDiagnostics | summarize any(Status)",
            {"timeAggregation": "Maximum", "metricMeasureColumn": "any_Status"},
            f"SELECT latest(Status) {FORWARDED}",
        ),
        (  # an in-list from dynamic() stays in the plain filter path
            'AzureDiagnostics | where Code in (dynamic(["a", "b"]))',
            {},
            f"SELECT count(*) {FORWARDED} AND Code IN ('a', 'b')",
        ),
        (  # a quote inside a value is escaped
            "AzureDiagnostics | where Msg == 'it\\'s'",
            {},
            f"SELECT count(*) {FORWARDED} AND Msg = 'it\\'s'",
        ),
    ],
)
def test_nrql_for_each_kql_shape(query: str, cond: dict[str, Any], expected: str) -> None:
    m = one(log_rule(query, **cond))
    assert m.confidence is Confidence.APPROXIMATE, notes(m)
    assert nrql_for(m) == expected
    assert m.data_path == "needs-log-forwarding"


def test_a_comparison_on_the_result_column_becomes_the_threshold() -> None:
    q = "AzureDiagnostics | summarize c = count() by Resource | where c > 10"
    m = one(log_rule(q, threshold=0))
    assert nrql_for(m) == f"SELECT count(*) {FORWARDED} FACET Resource"
    assert m.thresholds[0].operator is Operator.ABOVE and m.thresholds[0].value == 10
    assert "result column" in notes(m)


def test_the_result_column_comparison_needs_a_row_count_rule() -> None:
    q = "AzureDiagnostics | summarize c = count() by Resource | where c > 10"
    m = one(log_rule(q, threshold=3))
    assert m.confidence is Confidence.MANUAL and "result column" in m.reasons[0]


def test_a_finer_bin_is_summed_when_the_aggregate_composes() -> None:
    q = "AzureDiagnostics | summarize count() by bin(TimeGenerated, 1m)"
    m = one(log_rule(q, timeAggregation="Total"))
    assert nrql_for(m) == f"SELECT count(*) {FORWARDED}"
    assert "bin" in notes(m)
    q = "AzureDiagnostics | summarize avg(L) by bin(TimeGenerated, 1m)"
    m = one(log_rule(q, timeAggregation="Average", metricMeasureColumn="avg_L"))
    assert m.confidence is Confidence.MANUAL and "bin" in m.reasons[0]


def test_negation_and_text_match_caveats_are_noted() -> None:
    m = one(log_rule('AzureDiagnostics | where Level != "Info" and Msg has "x"'))
    assert "negated" in notes(m) and "whole term" in notes(m)


# -- data path of each table -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("table", "path", "azure_filter"),
    [
        ("AzureDiagnostics", "needs-log-forwarding", True),
        ("AzureActivity", "needs-log-forwarding", True),
        ("SigninLogs", "needs-log-forwarding", True),
        ("StorageBlobLogs", "needs-log-forwarding", True),
        ("AppServiceHTTPLogs", "needs-log-forwarding", True),
        ("Perf", "needs-agent", False),
        ("Syslog", "needs-agent", False),
        ("Event", "needs-agent", False),
        ("Heartbeat", "needs-agent", False),
        ("ContainerLogV2", "needs-agent", False),
        ("KubePodInventory", "needs-agent", False),
        ("AppRequests", "needs-agent", False),
        ("Usage", "unknown", False),
        ("Operation", "unknown", False),
        ("MyTable_CL", "unknown", True),
    ],
)
def test_every_table_gets_a_data_path(table: str, path: str, azure_filter: bool) -> None:
    info = classify(table)
    assert (info.path, info.azure_filter) == (path, azure_filter)


def test_an_agent_table_is_queried_without_the_forwarder_filter_and_says_so() -> None:
    q = 'Perf | where ObjectName == "Processor" and CounterName == "Total"'
    m = one(log_rule(q, timeAggregation="Average", metricMeasureColumn="CounterValue"))
    assert nrql_for(m) == (
        "SELECT average(CounterValue) FROM Log WHERE ObjectName = 'Processor' "
        "AND CounterName = 'Total'"
    )
    assert m.data_path == "needs-agent" and "UNVERIFIED" in notes(m)
    assert "Azure Monitor agent" in notes(m)


def test_a_workspace_table_has_an_unknown_data_path() -> None:
    m = one(log_rule("Usage | where IsBillable == true", threshold=0))
    assert nrql_for(m) == "SELECT filter(count(*), WHERE IsBillable = true) FROM Log"
    assert m.data_path == "unknown" and "no route" in notes(m)


def test_a_table_that_cannot_be_told_apart_without_a_filter_stays_manual() -> None:
    m = one(log_rule("Heartbeat", threshold=360))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith("kql table comes from an agent or service")
    assert m.data_path is None


def test_a_measure_column_with_spaces_is_written_in_backticks() -> None:
    m = one(
        log_rule(
            'Perf | where ObjectName == "Processor"',
            timeAggregation="Average",
            metricMeasureColumn="% Processor Time",
        )
    )
    assert nrql_for(m).startswith("SELECT average(`% Processor Time`) FROM Log WHERE")


# -- what stays manual, with the operator named ----------------------------------------------


@pytest.mark.parametrize(
    ("query", "operator"),
    [
        ("AzureDiagnostics | join kind=inner (Other) on Id", "join has no New Relic form"),
        ("AzureDiagnostics | union Other", "union has no New Relic form"),
        ("union AzureDiagnostics, Other", "union has no New Relic form"),
        ("AzureDiagnostics | mv-expand Tags", "mv-expand has no New Relic form"),
        ("AzureDiagnostics | parse Msg with 'a' x 'b'", "parse has no New Relic form"),
        (
            "AzureDiagnostics | extend c = extract('id=(\\\\d+)', 1, Msg)",
            "function extract() has no New Relic form",
        ),
        (
            "let t = materialize(AzureDiagnostics | take 5); t",
            "materialize has no New Relic form",
        ),
        (
            "AzureDiagnostics | make-series n = count() on TimeGenerated step 5m",
            "make-series has no New Relic form",
        ),
        (
            "AzureDiagnostics | extend s = series_decompose_anomalies(x)",
            "series_* function has no New Relic form",
        ),
        ("AzureDiagnostics | evaluate autocluster()", "evaluate has no New Relic form"),
        ('workspace("other").AzureDiagnostics', "cross-resource query has no New Relic form"),
        (
            "AzureDiagnostics | summarize arg_max(TimeGenerated, *) by Id",
            "aggregate arg_max() has no New Relic form",
        ),
        ('AzureDiagnostics | where A =~ "x"', "case-insensitive comparison"),
        ('AzureDiagnostics | where tolower(A) == "x"', "function tolower() has no New Relic form"),
        ("AzureDiagnostics | where A == B", "comparison of two columns"),
    ],
)
def test_unsupported_operators_are_named_in_the_reason(query: str, operator: str) -> None:
    m = one(log_rule(query))
    assert m.confidence is Confidence.MANUAL
    assert m.reasons[0].startswith("kql query cannot be converted")
    assert operator in m.reasons[0]
    assert m.data_path is None


def test_a_last_seen_check_is_named_as_an_absence_check() -> None:
    q = (
        "Heartbeat | summarize LastSeen = max(TimeGenerated) by Computer"
        " | where LastSeen < ago(10m)"
    )
    m = one(log_rule(q, threshold=0))
    assert m.confidence is Confidence.MANUAL
    assert "absence check" in m.reasons[0] and "loss-of-signal" in m.reasons[0]


# -- the fixtures ----------------------------------------------------------------------------


def _converted(folder: Path) -> tuple[int, int]:
    monitors = translate(load_alerts(folder))
    done = [m for m in monitors if m.confidence in (Confidence.EXACT, Confidence.APPROXIMATE)]
    return len(done), len(monitors)


def test_official_examples_that_have_a_query_convert_where_the_query_allows() -> None:
    folder = ROOT / "tests" / "fixtures" / "azure" / "scheduled_query_rules"
    assert _converted(folder) == (7, 15)  # Perf converts; two dynamic and six bare Heartbeat stay


def test_live_rules_convert_where_the_query_allows() -> None:
    folder = ROOT / "tests" / "fixtures" / "azure-live" / "scheduled_query_rules"
    assert _converted(folder) == (6, 9)  # Heartbeat last-seen and two dynamic thresholds stay


def test_converted_fixture_rules_carry_a_data_path() -> None:
    folder = ROOT / "tests" / "fixtures" / "azure-live" / "scheduled_query_rules"
    paths = {
        m.data_path
        for m in translate(load_alerts(folder))
        if m.confidence is Confidence.APPROXIMATE
    }
    assert paths == {"unknown", "needs-log-forwarding"}


def test_a_star_in_a_value_is_literal_for_equals_and_kept_out_of_the_plain_filters() -> None:
    m = one(log_rule("AzureDiagnostics | where Name == 'a*b'"))
    assert nrql_for(m) == f"SELECT filter(count(*), WHERE Name = 'a*b') {FORWARDED}"
