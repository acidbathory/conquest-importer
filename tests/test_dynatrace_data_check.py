"""The data check says which converted Dynatrace log conditions would never fire."""

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.data_check import Status, has_blind_conditions, render
from importer.core.ir import IRMonitor
from importer.sources.dynatrace.data_check import check_all
from importer.sources.dynatrace.models import load_exports
from importer.sources.dynatrace.translate import translate

DT = Path(__file__).parent / "fixtures" / "dynatrace"


class FakeClient:
    """Answers NRQL count queries: matching logs for a WHERE query, all logs for the rest."""

    def __init__(self, matching: int = 0, all_logs: int = 0) -> None:
        self.matching = matching
        self.all_logs = all_logs
        self.queries: list[str] = []

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        assert is_write is False
        nrql = variables["n"]
        self.queries.append(nrql)
        count = self.matching if " WHERE " in nrql else self.all_logs
        return {"actor": {"account": {"nrql": {"results": [{"count": count}]}}}}


def monitors() -> list[IRMonitor]:
    return translate(load_exports(DT))


def converted() -> IRMonitor:
    return next(m for m in monitors() if m.name == "example-checkout-errors")


def test_matching_log_events_mean_the_condition_can_fire() -> None:
    client = FakeClient(matching=4)
    (check,) = check_all(client, 1, [converted()])  # type: ignore[arg-type]
    assert check.status is Status.DATA and check.points == 4
    assert "FROM Log WHERE" in client.queries[0] and "SINCE 24 hours ago" in client.queries[0]
    assert len(client.queries) == 1


def test_no_logs_at_all_says_the_logs_are_not_forwarded() -> None:
    (check,) = check_all(FakeClient(), 1, [converted()])  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "no log events" in check.note
    assert has_blind_conditions([check])


def test_logs_that_do_not_match_say_to_check_attribute_names() -> None:
    client = FakeClient(matching=0, all_logs=500)
    (check,) = check_all(client, 1, [converted()])  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA and "attribute names" in check.note
    assert len(client.queries) == 2 and " WHERE " not in client.queries[1]


def test_manual_conversions_are_not_queried() -> None:
    client = FakeClient()
    manual = [m for m in monitors() if m.confidence.value == "manual"]
    assert manual
    checks = check_all(client, 1, manual)  # type: ignore[arg-type]
    assert {c.status for c in checks} == {Status.NOT_CHECKED} and not client.queries
    assert not has_blind_conditions(checks)


def test_the_hours_are_used_in_the_query() -> None:
    client = FakeClient(matching=1)
    check_all(client, 1, [converted()], hours=6)  # type: ignore[arg-type]
    assert "SINCE 6 hours ago" in client.queries[0]


def test_the_report_escapes_names() -> None:
    m = converted()
    m.name = "a|b`c`<x>"
    text = render(check_all(FakeClient(), 1, [m]), 6, "intro")  # type: ignore[arg-type]
    assert "a|b" not in text and "<x>" not in text and "intro" in text


def test_the_command_exits_2_when_a_condition_is_blind(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ir = tmp_path / "ir"
    ir.mkdir()
    (ir / "0001.json").write_text(converted().model_dump_json())
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    client = FakeClient()

    class Stub:
        def __init__(self, *_a: object, **_k: object) -> None: ...

        def __enter__(self) -> FakeClient:
            return client

        def __exit__(self, *_a: object) -> None:
            return None

    monkeypatch.setattr("importer.cli.NewRelicClient", Stub)
    out = tmp_path / "report.md"
    args = ["check-dynatrace-data", "--alerts", str(tmp_path), "--nr-account-id", "7"]
    res = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert res.exit_code == 2, res.output
    assert "no data" in out.read_text()
    client.matching = 3
    assert CliRunner().invoke(app, args).exit_code == 0


def test_the_command_needs_an_account_and_sane_hours(tmp_path: Path) -> None:
    for args in (["--alerts", str(tmp_path)], ["--nr-account-id", "7", "--hours", "0"]):
        res = CliRunner().invoke(app, ["check-dynatrace-data", *args])
        assert res.exit_code == 1 and "--nr-account-id" in res.output
