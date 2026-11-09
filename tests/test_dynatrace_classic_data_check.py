"""check-dynatrace-metrics: does the signal behind each converted condition have data?"""

from pathlib import Path
from types import TracebackType
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.commands import dynatrace_classic as command
from importer.core.data_check import Status, has_blind_conditions
from importer.core.ir import Confidence, IRMonitor
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.data_check import check_all, render_report
from importer.sources.dynatrace.classic.models import SettingsObject, parse_object
from importer.sources.dynatrace.classic.translate import translate

KEY = "NRAK-SECRET-KEY-VALUE-1234567890"


class FakeClient:
    """Answers count queries from a table of event.attribute -> points."""

    def __init__(self, points: dict[str, int]) -> None:
        self.points = points
        self.queries: list[str] = []

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        assert is_write is False
        nrql = variables["n"]
        self.queries.append(nrql)
        hit = [n for n in self.points if n in nrql]
        points = self.points[hit[0]] if hit else 0
        row: dict[str, Any] = (
            {"count": points} if "count(" in nrql else {"value": 7.0 if points else None}
        )
        return {"actor": {"account": {"nrql": {"results": [row]}}}}


def event(
    key: str = "builtin:host.cpu.usage",
    selector: str = "",
    name: str = "e",
    **model: Any,
) -> SettingsObject:
    query: dict[str, Any] = {"type": "METRIC_KEY", "metricKey": key, "aggregation": "AVG"}
    if selector:
        query = {"type": "METRIC_SELECTOR", "metricSelector": selector}
    return parse_object(
        {
            "objectId": name,
            "schemaId": models.METRIC_EVENTS,
            "value": {
                "summary": name,
                "queryDefinition": query,
                "modelProperties": {
                    "type": "STATIC_THRESHOLD",
                    "threshold": 1,
                    "alertCondition": "ABOVE",
                    "violatingSamples": 1,
                    "samples": 1,
                    **model,
                },
            },
        }
    )


def test_a_signal_with_data_and_one_without_are_told_apart() -> None:
    monitors = translate([event(name="cpu"), event("builtin:service.response.time", name="rt")])
    client = FakeClient({"SystemSample": 12})
    checks = check_all(client, 1, monitors)  # type: ignore[arg-type]
    assert [c.status for c in checks] == [Status.DATA, Status.NO_DATA]
    assert has_blind_conditions(checks)
    assert checks[0].points == 12 and checks[0].value == 7.0
    assert "never fire" in checks[1].note
    assert "SELECT count(cpuPercent) FROM SystemSample SINCE 24 hours ago" in client.queries


def test_a_fixed_expression_row_is_counted_on_its_event() -> None:
    (m,) = translate([event("builtin:service.requestCount.server")])
    client = FakeClient({"Transaction": 5})
    (c,) = check_all(client, 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.DATA
    assert "SELECT count(*) FROM Transaction SINCE 24 hours ago" in client.queries


def test_every_term_of_an_expression_must_have_data() -> None:
    (m,) = translate(
        [
            event(
                selector="(builtin:service.errors.total.count:value) "
                "/ (builtin:service.requestCount.server:value)"
            )
        ]
    )
    assert m.confidence is Confidence.APPROXIMATE
    (c,) = check_all(FakeClient({"Transaction": 5}), 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.DATA and c.points == 5


def test_a_baseline_condition_is_checked_too() -> None:
    (m,) = translate(
        [
            event(
                selector="builtin:host.cpu.usage:avg",
                type="SEASONAL_BASELINE",
                alertCondition="OUTSIDE",
                threshold=None,
            )
        ]
    )
    (c,) = check_all(FakeClient({}), 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.NO_DATA


def test_manual_and_non_metric_monitors_are_not_queried() -> None:
    manual = translate([event("builtin:host.disk.avail")])
    profile = translate([parse_object({"objectId": "p", "schemaId": models.PROFILE, "value": {}})])
    client = FakeClient({})
    checks = check_all(client, 1, [*manual, *profile])  # type: ignore[arg-type]
    assert {c.status for c in checks} == {Status.NOT_CHECKED} and not client.queries
    assert not has_blind_conditions(checks)


def test_the_report_escapes_names_and_never_holds_an_export_key() -> None:
    (m,) = translate([event()])
    m.name = "a|b`c`<x>"
    text = render_report(check_all(FakeClient({}), 1, [m]), 6)  # type: ignore[arg-type]
    assert "a|b" not in text and "<x>" not in text and "SystemSample" not in text


def test_hours_are_an_integer_in_the_query() -> None:
    (m,) = translate([event()])
    client = FakeClient({"SystemSample": 1})
    check_all(client, 1, [m], hours=48)  # type: ignore[arg-type]
    assert all("SINCE 48 hours ago" in q for q in client.queries)


# ----- the command --------------------------------------------------------------------------


class Session:
    def __init__(self, client: FakeClient) -> None:
        self.client = client

    def __enter__(self) -> FakeClient:
        return self.client

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        return None


def write_ir(folder: Path, monitors: list[IRMonitor]) -> Path:
    (folder / "ir").mkdir(parents=True)
    for m in monitors:
        (folder / "ir" / f"{m.source_id}.json").write_text(m.model_dump_json())
    return folder


def run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, points: dict[str, int], *extra: str
) -> Any:
    monitors = translate([event()])
    alerts = write_ir(tmp_path / "alerts", monitors)
    monkeypatch.setenv("NEW_RELIC_API_KEY", KEY)
    monkeypatch.setattr(
        command, "NewRelicClient", lambda _key, _region: Session(FakeClient(points))
    )
    return CliRunner().invoke(
        app, ["check-dynatrace-metrics", "--alerts", str(alerts), "--nr-account-id", "1", *extra]
    )


def test_the_command_exits_2_when_a_signal_has_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, {})
    assert result.exit_code == 2 and "no data" in result.output
    assert KEY not in result.output


def test_the_command_exits_0_when_every_signal_has_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "report.md"
    result = run(tmp_path, monkeypatch, {"SystemSample": 3}, "--out", str(out))
    assert result.exit_code == 0 and "data arrives" in out.read_text()
    assert KEY not in result.output and KEY not in out.read_text()


@pytest.mark.parametrize("args", [["--hours", "0"], ["--hours", "500"]])
def test_the_command_checks_its_options(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    result = run(tmp_path, monkeypatch, {}, *args)
    assert result.exit_code == 1 and "--hours" in result.output


def test_the_command_needs_an_account_id(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["check-dynatrace-metrics", "--alerts", str(tmp_path)])
    assert result.exit_code == 1 and "--nr-account-id" in result.output


def test_a_term_with_no_data_is_named_by_its_letter() -> None:
    selector = "(builtin:host.cpu.usage:avg) / (builtin:host.mem.usage:avg)"
    (m,) = translate([event(selector=selector)])
    (c,) = check_all(FakeClient({"cpuPercent": 5}), 1, [m])  # type: ignore[arg-type]
    assert c.status is Status.NO_DATA and "term b of 2" in c.note
