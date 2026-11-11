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
