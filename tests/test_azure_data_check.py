"""check-azure-data: read-only NRQL that the metrics and attributes the conditions use exist."""

from typing import Any

from azure_helpers import criterion, log_rule, metric_alert
from importer.core.data_check import Status
from importer.core.ir import IRMonitor
from importer.sources.azure import data_check as dc
from importer.sources.azure.translate import translate_alert


class FakeNR:
    """Answers NRQL by looking at the query text; records every query."""

    def __init__(self, answers: dict[str, list[dict[str, Any]]]) -> None:
        self.answers = answers
        self.queries: list[str] = []
        self.writes = 0

    def run(self, query: str, variables: dict[str, Any], is_write: bool = False) -> dict[str, Any]:
        self.writes += is_write
        nrql = str(variables["n"])
        self.queries.append(nrql)
        for needle, rows in self.answers.items():
            if needle in nrql:
                return {"actor": {"account": {"nrql": {"results": rows}}}}
        return {"actor": {"account": {"nrql": {"results": []}}}}


def monitors(*alerts: Any) -> list[IRMonitor]:
    return [m for a in alerts for m in translate_alert(a)]


def test_metric_with_data_is_confirmed_and_only_selects_are_sent() -> None:
    nr = FakeNR(
        {
            "count(azure.compute.virtualmachines.PercentageCPU)": [{"count": 120}],
            "IS NOT NULL": [{"count": 7}],
        }
    )
    (check,) = dc.check_all(nr, 1, monitors(metric_alert()), 24)  # type: ignore[arg-type]
    assert check.status is Status.DATA and check.points == 120
    assert all(q.startswith("SELECT") for q in nr.queries) and nr.writes == 0
    assert "SINCE 24 hours ago" in nr.queries[0]


def test_a_missing_metric_is_blind_and_candidates_are_offered() -> None:
    nr = FakeNR(
        {
            "uniques(metricName": [
                {"uniques.metricName": ["azure.compute.virtualmachines.CPUPercent"]}
            ]
        }
    )
    (check,) = dc.check_all(nr, 1, monitors(metric_alert()), 6)  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "CPUPercent" in check.note
    assert "name-map.json" in check.note
    assert any("LIKE 'azure.compute.virtualmachines.%'" in q for q in nr.queries)


def test_a_missing_dimension_attribute_is_reported() -> None:
    dims = [{"name": "Api", "operator": "Include", "values": ["*"]}]
    nr = FakeNR(
        {
            "count(azure.compute.virtualmachines.PercentageCPU)": [{"count": 5}],
            "IS NOT NULL": [{"count": 0}],
        }
    )
    (check,) = dc.check_all(nr, 1, monitors(metric_alert(criterion(dimensions=dims))), 24)  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "azure.compute.virtualmachines.Api" in check.note


def test_log_conditions_check_forwarded_azure_logs() -> None:
    nr = FakeNR({"plugin.type = 'azure'": [{"count": 9}]})
    (check,) = dc.check_all(nr, 1, monitors(log_rule("T | where A == 'x'")), 24)  # type: ignore[arg-type]
    assert check.status is Status.DATA
    nr2 = FakeNR({})
    (blind,) = dc.check_all(nr2, 1, monitors(log_rule("T | where A == 'x'")), 24)  # type: ignore[arg-type]
    assert blind.status is Status.NO_DATA and "Event Hub" in blind.note


def test_manual_conditions_are_not_checked_and_send_nothing() -> None:
    nr = FakeNR({})
    (check,) = dc.check_all(nr, 1, monitors(metric_alert(windowSize="P1D")), 24)  # type: ignore[arg-type]
    assert check.status is Status.NOT_CHECKED and nr.queries == []


def test_the_report_names_the_pair_and_escapes_cells() -> None:
    nr = FakeNR({})
    checks = dc.check_all(nr, 1, monitors(metric_alert(name="a|b")), 24)  # type: ignore[arg-type]
    text = dc.render_report(checks, 24)
    assert "Azure Monitor to New Relic" in text and "a b" in text and "a|b" not in text
    assert dc.has_blind_conditions(checks)
