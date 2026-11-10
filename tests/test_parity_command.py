"""verify-parity end to end with fake HTTP on both sides (no network)."""

import json
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.parity.model import Fetched, ParityError, Series
from importer.parity.session import Options, parse_every, parse_variables, verify

FIX = Path(__file__).parent / "fixtures"
NOW = 1_700_000_100
STEP = 60
START = NOW // STEP * STEP - 3600


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("guided")
    shutil.copytree(FIX / "grafana-dashboards", root / "export" / "dashboards")
    shutil.copytree(FIX / "grafana", root / "export" / "alerts")
    run = CliRunner().invoke
    dash = ["convert-grafana-dashboards", "--in", str(root / "export" / "dashboards"), "--out"]
    assert run(app, [*dash, str(root / "converted" / "dashboards")]).exit_code == 0
    alerts = ["convert", "--source", "grafana", "--in", str(root / "export" / "alerts"), "--out"]
    assert run(app, [*alerts, str(root / "converted" / "alerts")]).exit_code == 0
    return root / "converted"


def values() -> list[float]:
    return [1.0 + (i % 5) for i in range(60)]


def fake_source(query: str, start: int) -> Fetched:
    return Fetched(
        [Series({"instance": "a"}, {START + (i + 1) * STEP: v for i, v in enumerate(values())})]
    )


@contextmanager
def open_source(spec: str) -> Iterator[Any]:
    def fetch(family: str, query: str, start: int, end: int, step: int) -> Fetched:
        if "node_cpu_seconds_total" not in query and "node_cpu_busy" not in query:
            return Fetched([])
        return fake_source(query, start)

    yield fetch


@contextmanager
def open_nr(region: str, account: int) -> Iterator[Any]:
    def rows(nrql: str) -> list[dict[str, Any]]:
        if "node_cpu_seconds_total" not in nrql and "node_cpu_busy" not in nrql:
            return []
        return [
            {"beginTimeSeconds": START + i * STEP, "facet": "a", "v": v}
            for i, v in enumerate(values())
        ]

    yield rows


def options(converted: Path, out: Path, **kw: Any) -> Options:
    return Options(converted=converted, source_prom="prom", nr_account_id=7, out=out, hours=1, **kw)


def test_verify_writes_the_reports_and_exits_zero(converted: Path, tmp_path: Path) -> None:
    lines: list[str] = []
    code = verify(
        options(converted, tmp_path / "parity", variables=["job=node"]),
        lines.append,
        open_source=open_source,
        open_nr=open_nr,
        clock=lambda: NOW,
    )
    assert code == 0
    data = json.loads((tmp_path / "parity" / "parity.json").read_text())
    verdicts = {i["id"].split(":", 1)[1]: i["verdict"] for i in data["items"]}
    assert verdicts["CPU busy by instance"] == "VERIFIED"
    assert verdicts["Load 1m (average)"] == "NOT_COMPARABLE"
    assert verdicts["Memory available"] == "NO_DATA"
    labels = json.loads((tmp_path / "parity" / "verified-labels.json").read_text())
    assert [x["widget"] for x in labels["verified"]] == ["CPU busy by instance"]
    assert any("Report:" in line for line in lines)


def test_watch_rewrites_the_report_each_cycle(converted: Path, tmp_path: Path) -> None:
    lines: list[str] = []
    sleeps: list[float] = []
    verify(
        options(converted, tmp_path / "w", watch=True, every="10m", variables=["job=node"]),
        lines.append,
        open_source=open_source,
        open_nr=open_nr,
        clock=lambda: NOW,
        sleep=sleeps.append,
        max_cycles=2,
    )
    assert sleeps == [600] and sum("Report:" in x for x in lines) == 2 and "Stopped." in lines


def test_bad_settings_are_refused(converted: Path, tmp_path: Path) -> None:
    for bad in (
        options(converted, tmp_path, step=5),
        options(converted, tmp_path, tolerance=2.0),
        Options(converted=converted, source_prom="p", nr_account_id=0),
        Options(converted=converted, source_prom="p", nr_account_id=1, hours=24, step=60),
    ):
        with pytest.raises(ParityError):
            verify(bad, print)
    with pytest.raises(ParityError):
        parse_every("5s")
    assert parse_every("10m") == 600
    with pytest.raises(ParityError):
        parse_variables(["no-equals"])


def test_command_is_registered_with_the_documented_options() -> None:
    result = CliRunner().invoke(app, ["verify-parity", "--help"])
    assert result.exit_code == 0
    for flag in ("--in", "--source-prom", "--nr-account-id", "--hours", "--step", "--watch"):
        assert flag in result.output


def test_command_reports_an_error_without_a_traceback(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app,
        ["verify-parity", "--in", str(tmp_path), "--source-prom", "http://127.0.0.1:1",
         "--nr-account-id", "7"],
    )  # fmt: skip
    assert result.exit_code == 1 and "Error:" in result.output
