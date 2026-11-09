"""check-grafana-data with a fake New Relic client: validated names only, exit 2 if blind."""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, cast

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import NewRelicClient
from importer.core.data_check import Status
from importer.core.ir import Confidence, IRMonitor
from importer.sources import REGISTRY
from importer.sources.grafana.datapath import check as ck

FIXTURES = Path(__file__).parent / "fixtures" / "grafana"
SECRET = "SECRET-VALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests


class FakeClient:
    """Answers the check queries from a table of metric name -> what New Relic would hold."""

    def __init__(
        self,
        metrics: dict[str, dict[str, Any]] | None = None,
        logs: int = 0,
        log_keys: list[str] | None = None,
    ) -> None:
        self.metrics = metrics or {}
        self.logs = logs
        self.log_keys = log_keys or []
        self.queries: list[str] = []

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        assert is_write is False and "mutation" not in query
        nrql = variables["n"]
        self.queries.append(nrql)
        return {"actor": {"account": {"nrql": {"results": self._rows(nrql)}}}}

    def _rows(self, nrql: str) -> list[dict[str, Any]]:
        if "FROM Log" in nrql:
            if "keyset()" in nrql:
                return [{"key": k, "type": "string"} for k in self.log_keys]
            return [{"count": self.logs}]
        name = next((n for n in self.metrics if f"'{n}'" in nrql), None)
        held = self.metrics.get(name or "", {"count": 0, "keys": [], "servers": []})
        if "keyset()" in nrql:
            return [{"key": k, "type": "string"} for k in held["keys"]]
        if "uniques(" in nrql:
            return [{"uniques.prometheus_server": held["servers"]}]
        return [{"count": held["count"]}]


def monitors() -> dict[str, IRMonitor]:
    found = REGISTRY["grafana"].convert(FIXTURES)
    return {m.name: m for m in found}


def check(client: FakeClient, name: str, hours: int = 24) -> Any:
    return ck.check_monitor(cast(NewRelicClient, client), 1, monitors()[name], hours)


def test_a_metric_that_arrives_is_data() -> None:
    client = FakeClient(
        {"node_cpu_busy": {"count": 40, "keys": ["job", "env", "instance"], "servers": ["p1"]}}
    )
    result = check(client, "ckg-prom-cpu-gauge")
    assert (result.status, result.points) == (Status.DATA, 40) and result.note == ""
    assert client.queries[0] == (
        "SELECT count(*) FROM Metric WHERE metricName = 'node_cpu_busy' SINCE 24 hours ago"
    )


def test_a_metric_that_never_arrived_is_no_data() -> None:
    result = check(FakeClient(), "ckg-prom-cpu-gauge")
    assert result.status is Status.NO_DATA and "has not arrived" in result.note


def test_a_filter_label_missing_from_the_keyset_makes_the_condition_blind() -> None:
    client = FakeClient({"node_cpu_busy": {"count": 9, "keys": ["instance"], "servers": ["p1"]}})
    result = check(client, "ckg-prom-cpu-gauge")
    assert result.status is Status.NO_DATA
    # `env` is a negated filter in this fixture: it holds when the attribute is missing
    assert "job" in result.note and "env" not in result.note and "instance" not in result.note


def test_a_missing_group_by_label_is_a_note_only() -> None:
    client = FakeClient({"node_cpu_busy": {"count": 9, "keys": ["job", "env"], "servers": ["p1"]}})
    result = check(client, "ckg-prom-cpu-gauge")
    assert result.status is Status.DATA and "instance" in result.note


def test_several_prometheus_servers_are_reported_as_double_counting() -> None:
    client = FakeClient(
        {"node_cpu_busy": {"count": 9, "keys": ["job", "env", "instance"], "servers": ["a", "b"]}}
    )
    result = check(client, "ckg-prom-cpu-gauge")
    assert result.status is Status.DATA and "2 Prometheus servers" in result.note


def test_a_log_condition_counts_matching_events_and_names_missing_attributes() -> None:
    result = check(FakeClient(logs=5, log_keys=["env", "app", "message"]), "ckg-loki-errors")
    assert (result.status, result.points) == (Status.DATA, 5)
    blind = check(FakeClient(logs=0, log_keys=["message"]), "ckg-loki-errors")
    assert blind.status is Status.NO_DATA and "env" in blind.note and "app" in blind.note


def test_a_manual_monitor_is_not_checked() -> None:
    manual = next(m for m in monitors().values() if m.confidence is Confidence.MANUAL)
    client = FakeClient()
    result = ck.check_monitor(cast(NewRelicClient, client), 1, manual, 24)
    assert result.status is Status.NOT_CHECKED and client.queries == []


def test_a_hostile_metric_name_is_not_queried() -> None:
    monitor = monitors()["ckg-prom-cpu-gauge"].model_copy(deep=True)
    assert monitor.signal.metric is not None
    for term in monitor.signal.metric.terms.values():
        term.metric = "x'; DROP"
        assert term.nr_target is not None
        term.nr_target.attribute = "x'; DROP"
    client = FakeClient()
    result = ck.check_monitor(cast(NewRelicClient, client), 1, monitor, 24)
    assert result.status is Status.NOT_CHECKED and client.queries == []
    assert "x'" not in result.note


def test_every_query_is_a_select_built_from_validated_names() -> None:
    client = FakeClient(
        {"queue_depth": {"count": 1, "keys": ["env", "queue"], "servers": ["p"]}}, logs=1
    )
    ck.check_all(cast(NewRelicClient, client), 1, list(monitors().values()), 6)
    assert client.queries
    assert all(q.startswith("SELECT ") and "SINCE 6 hours ago" in q for q in client.queries)
    assert all(";" not in q and "`" not in q for q in client.queries)


def test_targets_are_listed_for_the_guide() -> None:
    metrics, attributes = ck.targets_from_monitors(list(monitors().values()))
    assert {"queue_depth", "node_cpu_busy"} <= set(metrics)
    assert {"env", "app"} <= set(attributes)
    assert metrics == sorted(set(metrics))


def test_the_report_names_conditions_through_the_markdown_escape() -> None:
    monitor = monitors()["ckg-prom-cpu-gauge"].model_copy(update={"name": "evil | `name` <b>"})
    result = ck.check_monitor(cast(NewRelicClient, FakeClient()), 1, monitor, 24)
    text = ck.render_report([result], 24)
    assert "evil" in text and "|  name" not in text and "<b>" not in text
    assert text.count("\n| ") == 2  # the header row and one condition row


# --- the command ---


@contextmanager
def fake_client_factory(client: FakeClient) -> Iterator[FakeClient]:
    yield client


def write_ir(folder: Path) -> Path:
    (folder / "ir").mkdir(parents=True)
    for i, m in enumerate(monitors().values()):
        (folder / "ir" / f"{i}.json").write_text(m.model_dump_json())
    return folder


def run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, client: FakeClient, *extra: str) -> Any:
    monkeypatch.setattr(
        "importer.commands.grafana_notifications.new_relic_client",
        lambda _region: fake_client_factory(client),
    )
    alerts = write_ir(tmp_path / "alerts")
    return CliRunner().invoke(
        app,
        ["check-grafana-data", "--alerts", str(alerts), "--nr-account-id", "1", *extra],
    )


def test_the_command_exits_2_when_a_condition_has_no_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = run(tmp_path, monkeypatch, FakeClient())
    assert result.exit_code == 2, result.output
    assert "no data" in result.output


def test_the_command_exits_0_when_all_data_arrives(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = FakeClient(
        {
            "queue_depth": {"count": 4, "keys": ["env", "queue"], "servers": ["p"]},
            "node_cpu_busy": {"count": 4, "keys": ["job", "env", "instance"], "servers": ["p"]},
        },
        logs=3,
        log_keys=["env", "app", "message"],
    )
    result = run(tmp_path, monkeypatch, client, "--out", str(tmp_path / "report.md"))
    assert result.exit_code == 0, result.output
    assert (tmp_path / "report.md").read_text().startswith("# Data check")


def test_the_command_needs_an_account_and_a_sane_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for extra in (["--hours", "0"], ["--hours", "999"]):
        assert run(tmp_path / str(extra), monkeypatch, FakeClient(), *extra).exit_code == 1
    missing = CliRunner().invoke(app, ["check-grafana-data", "--alerts", str(tmp_path)])
    assert missing.exit_code == 1 and "--nr-account-id" in missing.output


def test_a_hostile_condition_name_does_not_break_the_report_or_the_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alerts = write_ir(tmp_path / "alerts")
    first = next((alerts / "ir").glob("*.json"))
    monitor = IRMonitor.model_validate_json(first.read_text()).model_copy(
        update={"name": f"{SECRET} | `x` \x1b[31m"}
    )
    first.write_text(monitor.model_dump_json())
    monkeypatch.setattr(
        "importer.commands.grafana_notifications.new_relic_client",
        lambda _region: fake_client_factory(FakeClient()),
    )
    result = CliRunner().invoke(
        app, ["check-grafana-data", "--alerts", str(alerts), "--nr-account-id", "1"]
    )
    assert "\x1b" not in result.output and "`x`" not in result.output
