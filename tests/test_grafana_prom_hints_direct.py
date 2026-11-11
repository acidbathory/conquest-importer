"""grafana-prom-hints against a Prometheus URL, and the guided flow's hints step (no network)."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana import prom_hints as ph

BASE = "http://127.0.0.1:9090"
METADATA = {"status": "success", "data": {"node_load1": [{"type": "gauge"}]}}
NAMES = {"status": "success", "data": ["node_load1"]}
ROWS = {
    "status": "success",
    "data": [
        {"__name__": "node_load1", "instance": "a"},
        {"__name__": "node_load1", "instance": "b"},
    ],
}


def handle(request: httpx.Request) -> httpx.Response:
    answers = {
        "/api/v1/metadata": METADATA,
        "/api/v1/label/__name__/values": NAMES,
        "/api/v1/series": ROWS,
    }
    if request.url.path in answers:
        return httpx.Response(200, json=answers[request.url.path])
    return httpx.Response(404, json={"message": "not found"})


def test_direct_getter_reads_hints_without_a_data_source() -> None:
    with ph.direct_hint_getter(BASE, httpx.MockTransport(handle)) as get:
        found = ph.read_hints(get, None, {"node_load1"})
    assert found.types == {"node_load1": "gauge"}
    assert found.series == {"node_load1": ["instance"]}


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/v1/query", {"query": "up"}),
        ("/api/v1/series", {}),
        ("/api/v1/series", {"match[]": "up"}),
        ("/api/v1/admin/tsdb/delete_series", {}),
        ("/api/datasources/uid/x", {"a": "1"}),
    ],
)
def test_direct_getter_allows_only_the_three_read_paths(path: str, params: dict[str, str]) -> None:
    with (
        ph.direct_hint_getter(BASE, httpx.MockTransport(handle)) as get,
        pytest.raises(ph.HintsError),
    ):
        get(path, params)


@pytest.mark.parametrize(
    "url", ["http://prom.example.com", "ftp://127.0.0.1", "http://u:p@127.0.0.1"]
)
def test_direct_getter_refuses_remote_or_odd_urls(url: str) -> None:
    with pytest.raises(ph.HintsError), ph.direct_hint_getter(url, httpx.MockTransport(handle)):
        pass


def _extract(tmp_path: Path) -> Path:
    folder = tmp_path / "extract"
    folder.mkdir()
    panel = {"type": "stat", "title": "p", "targets": [{"expr": "sum(node_load1)"}]}
    dash = {"uid": "d1", "title": "D", "panels": [panel]}
    (folder / "d.json").write_text(json.dumps({"dashboard": dash}))
    return folder


def test_command_with_prometheus_url_writes_hints_that_unlock_the_sum(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)  # a direct URL needs no Grafana
    real = httpx.Client

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handle)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    extract, hints = _extract(tmp_path), tmp_path / "hints.json"
    args = ["grafana-prom-hints", "--prometheus-url", BASE, "--metrics-from", str(extract)]
    result = CliRunner().invoke(app, [*args, "--out", str(hints)])
    assert result.exit_code == 0, result.output
    assert json.loads(hints.read_text())["series"] == {"node_load1": ["instance"]}
    base = ["convert-grafana-dashboards", "--in", str(extract)]
    plain = CliRunner().invoke(app, [*base, "--out", str(tmp_path / "a")])
    hinted = CliRunner().invoke(app, [*base, "--out", str(tmp_path / "b"), "--hints", str(hints)])
    assert "1 placeholders" in plain.output
    assert "0 placeholders" in hinted.output


def test_command_needs_exactly_one_of_datasource_and_url(tmp_path: Path) -> None:
    extract = _extract(tmp_path)
    for extra in ([], ["--datasource", "x", "--prometheus-url", BASE]):
        result = CliRunner().invoke(
            app,
            [
                "grafana-prom-hints",
                "--metrics-from",
                str(extract),
                "--out",
                str(tmp_path / "h"),
                *extra,
            ],
        )
        assert result.exit_code == 1
