"""Dashboard KQL goes through the subset translator of the scheduled query rules."""

import pytest

from importer.sources.azure import dashboard_kql
from importer.sources.azure.dashboard_kql import KqlNrql, translate_kql


def ok(query: str) -> KqlNrql:
    found = translate_kql(query, ())
    assert isinstance(found, KqlNrql), found
    return found


def test_a_count_by_a_column_with_a_bin_is_a_faceted_timeseries() -> None:
    found = ok(
        "AzureDiagnostics | where ResourceType == 'VAULTS' "
        "| summarize n=count() by OperationName, bin(TimeGenerated, 1h)"
    )
    assert found.nrql == (
        "SELECT count(*) AS 'n' FROM Log WHERE plugin.type = 'azure' AND ResourceType = 'VAULTS' "
        "FACET OperationName TIMESERIES 1 hour"
    )
    assert found.timeseries
    assert found.data_path == "needs-log-forwarding"
    assert any("Event Hub forwarder" in n for n in found.notes)


def test_an_or_is_bracketed_next_to_the_forwarder_term() -> None:
    found = ok("AzureActivity | where Op contains 'x' or Op contains 'y' | summarize count() by Op")
    assert "plugin.type = 'azure' AND (Op LIKE '%x%' OR Op LIKE '%y%')" in found.nrql


def test_an_agent_table_keeps_its_data_path_and_says_so() -> None:
    found = ok(
        "Perf | where CounterName == 'Free Megabytes' | summarize avg(CounterValue) by Computer"
    )
    assert found.data_path == "needs-agent"
    assert "plugin.type" not in found.nrql
    assert any("Azure Monitor agent" in n for n in found.notes)


def test_a_count_of_an_agent_table_with_no_filter_is_refused() -> None:
    found = translate_kql("Heartbeat | summarize count() by Computer", ())
    assert isinstance(found, str) and found.startswith("workbook query is KQL")
    assert "every log line" in found


@pytest.mark.parametrize(
    ("query", "words"),
    [
        ("AzureActivity | take 10", "take"),
        ("AzureActivity | project Caller", "no aggregate"),
        ("AzureActivity | summarize c=count() by Op | where c > 5", "result"),
        ("AzureDiagnostics | where Resource == '{Name}' | summarize count()", "parameter"),
        ("AzureActivity | join (X) on Y | summarize count()", "join"),
        ("", "empty"),
    ],
)
def test_what_the_subset_cannot_read_stays_a_placeholder_with_the_reason(
    query: str, words: str
) -> None:
    found = translate_kql(query, ())
    assert isinstance(found, str)
    assert found.startswith("workbook query is KQL that cannot be converted")
    assert words in found


def test_untrusted_text_is_escaped_in_the_nrql() -> None:
    found = ok('AzureActivity | where Op == "a\'b" | summarize count()')
    assert "'a\\'b'" in found.nrql or "a\\'b" in found.nrql


def test_the_module_is_the_one_swap_point() -> None:
    assert dashboard_kql.translate_kql is translate_kql


TIME = ("TimeRange",)
LOGS = "AzureDiagnostics | where ResourceType == 'VAULTS' "


@pytest.mark.parametrize("stage", ["{TimeRange}", "{TimeRange:value}"])
@pytest.mark.parametrize("column", ["TimeGenerated", "timestamp"])
def test_a_time_range_filter_is_dropped_for_the_dashboard_time_picker(
    stage: str, column: str
) -> None:
    found = translate_kql(f"{LOGS}| where {column} {stage} | summarize n=count() by Op", (), TIME)
    assert isinstance(found, KqlNrql), found
    assert "SINCE" not in found.nrql and "{" not in found.nrql
    assert found.nrql.endswith("FACET Op")


def test_a_time_range_grain_is_timeseries_auto() -> None:
    q = (
        f"{LOGS}| where TimeGenerated {{TimeRange}} "
        "| summarize count() by bin(TimeGenerated, {TimeRange:grain})"
    )
    found = translate_kql(q, (), TIME)
    assert isinstance(found, KqlNrql), found
    assert found.nrql.endswith("TIMESERIES AUTO") and found.timeseries


@pytest.mark.parametrize(
    ("query", "named"),
    [
        (
            f"{LOGS}| where TimeGenerated > {{TimeRange:start}} | summarize count()",
            "TimeRange:start",
        ),
        (f"{LOGS}| where Op == '{{Pick}}' | summarize count()", "Pick"),
        (f"let t = {{TimeRange:end}}; {LOGS}| summarize count()", "TimeRange:end"),
    ],
)
def test_any_other_use_of_a_parameter_stays_a_placeholder_naming_it(query: str, named: str) -> None:
    found = translate_kql(query, (), TIME)
    assert isinstance(found, str) and "{" + named + "}" in found


def test_a_name_that_is_not_a_time_range_parameter_is_not_dropped() -> None:
    found = translate_kql(f"{LOGS}| where TimeGenerated {{Other}} | summarize count()", (), TIME)
    assert isinstance(found, str) and "{Other}" in found


# -- let, extend and tostring (constructs the public workbook templates use) -------------------


def test_a_scalar_let_is_inlined_into_the_where() -> None:
    found = ok(
        "let code = 'VAULTS'; AzureDiagnostics | where ResourceType == code | summarize count()"
    )
    assert found.nrql == (
        "SELECT count(*) AS 'count_' FROM Log "
        "WHERE plugin.type = 'azure' AND ResourceType = 'VAULTS'"
    )


def test_a_table_let_used_once_is_inlined_before_the_summarize() -> None:
    found = ok(
        "let vaults = AzureDiagnostics | where ResourceType == 'VAULTS'; "
        "vaults | summarize n=count() by OperationName"
    )
    assert found.nrql == (
        "SELECT count(*) AS 'n' FROM Log WHERE plugin.type = 'azure' AND ResourceType = 'VAULTS' "
        "FACET OperationName"
    )


def test_a_table_let_inside_a_workbook_time_range_is_inlined() -> None:
    found = translate_kql(
        "let t = AzureDiagnostics | where TimeGenerated {TimeRange}; "
        "t | summarize count() by OperationName",
        (),
        TIME,
    )
    assert isinstance(found, KqlNrql), found
    assert "{" not in found.nrql and found.nrql.endswith("FACET OperationName")


def test_a_chain_of_table_lets_each_used_once_is_inlined() -> None:
    found = ok(
        "let a = AzureDiagnostics | where ResourceType == 'V'; "
        "let b = a | where Category == 'C'; b | summarize count()"
    )
    assert "ResourceType = 'V' AND Category = 'C'" in found.nrql


@pytest.mark.parametrize(
    ("query", "words"),
    [
        (  # used twice: inlining would run the let body twice, which is a different query
            "let a = AzureDiagnostics | where ResourceType == 'V'; "
            "let n = toscalar(a | count); a | summarize x = count() * 100.0 / n",
            "let",
        ),
        (
            "let f = (x:string) { AzureDiagnostics | where Op == x }; f('a') | summarize count()",
            "'{'",
        ),
        ("let a = AzureDiagnostics | join (B) on K; a | summarize count()", "join"),
        (
            "let a = AzureDiagnostics; a | where Op == 'x' | summarize count() | join (a) on K",
            "let",
        ),
    ],
)
def test_a_let_that_cannot_be_inlined_exactly_stays_a_placeholder(query: str, words: str) -> None:
    found = translate_kql(query, ())
    assert isinstance(found, str) and words in found


def test_an_unused_table_let_is_not_dropped_silently() -> None:
    found = translate_kql(
        "let a = AzureDiagnostics | where Op == 'x'; AzureDiagnostics | summarize count()", ()
    )
    assert isinstance(found, str) and "let" in found


def test_extend_arithmetic_is_written_inside_the_aggregate() -> None:
    found = ok(
        "AzureDiagnostics | where ResourceType == 'V' "
        "| extend KiB = (BytesIn + BytesOut) * 1.5 | summarize total = sum(KiB) by OperationName"
    )
    assert "sum((BytesIn + BytesOut) * 1.5) AS 'total'" in found.nrql
    assert found.nrql.endswith("FACET OperationName")


def test_a_later_extend_may_use_an_earlier_one() -> None:
    found = ok(
        "AzureDiagnostics | where ResourceType == 'V' | extend A = X + 1, B = A * 2 "
        "| summarize avg(B)"
    )
    assert "average((X + 1) * 2)" in found.nrql


def test_a_float_division_converts_and_an_integer_division_does_not() -> None:
    base = "AzureDiagnostics | where ResourceType == 'V' | extend R = {} | summarize max(R)"
    found = ok(base.format("Bytes / 1024.0"))
    assert "max(Bytes / 1024.0)" in found.nrql
    for expr in ("Bytes / 1024", "Bytes / Total", "Bytes / 0.0", "Bytes % 7"):
        refused = translate_kql(base.format(expr), ())
        assert isinstance(refused, str) and refused.startswith("workbook query is KQL"), expr


def test_an_extend_value_used_outside_an_aggregate_is_refused() -> None:
    found = translate_kql(
        "AzureDiagnostics | where ResourceType == 'V' | extend R = X + 1 "
        "| where R > 5 | summarize count()",
        (),
    )
    assert isinstance(found, str) and "extend" in found


def test_tostring_of_a_plain_column_in_a_group_by_or_an_extend_is_the_column() -> None:
    found = ok(
        "AzureDiagnostics | where ResourceType == 'V' "
        "| extend Code = tostring(ResultCode) | summarize count() by Code, tostring(Level)"
    )
    assert found.nrql.endswith("FACET ResultCode, Level")


@pytest.mark.parametrize(
    "query",
    [
        "AzureDiagnostics | where tostring(ResultCode) == '400' | summarize count()",
        "AzureActivity | extend R = tostring(parse_json(Properties).resource) "
        "| summarize count() by R",
    ],
)
def test_tostring_anywhere_else_stays_a_placeholder(query: str) -> None:
    found = translate_kql(query, ())
    assert isinstance(found, str) and ("tostring" in found or "parse_json" in found)


def test_case_insensitive_equality_stays_manual_with_a_specific_reason() -> None:
    found = translate_kql("AzureActivity | where Op =~ 'Delete' | summarize count()", ())
    assert isinstance(found, str) and "=~" in found and "case-sensitive" in found


def test_the_case_insensitive_reason_has_its_own_steps() -> None:
    from importer.core.remediation import steps_for
    from importer.core.taxonomy import Category, classify

    found = translate_kql("AzureActivity | where Op =~ 'Delete' | summarize count()", ())
    assert isinstance(found, str)
    assert classify(found) is not Category.OTHER
    assert any("RLIKE" in step for step in steps_for([found]))
