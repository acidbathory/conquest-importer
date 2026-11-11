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


def test_the_proxy_getter_rejects_the_direct_shape_and_the_reverse() -> None:
    transport = httpx.MockTransport(handle)
    proxy = "/api/datasources/proxy/uid/u1/api/v1/metadata"
    with ph.direct_hint_getter(BASE, transport) as get, pytest.raises(ph.HintsError):
        get(proxy, {})
    with ph.hint_getter("tok", "https://stack.grafana.net", transport) as get:
        with pytest.raises(ph.HintsError):
            get("/api/v1/metadata", {})
        with pytest.raises(ph.HintsError):
            get("/api/v1/label/__name__/values", {})


def test_the_direct_client_ignores_proxy_environment_variables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, Any] = {}
    real = httpx.Client

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        seen.update(kwargs)
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    with ph.direct_hint_getter(BASE, httpx.MockTransport(handle)):
        pass
    assert seen["trust_env"] is False


def test_the_series_window_is_a_week_and_the_claim_says_so() -> None:
    assert ph.WINDOW_SECONDS >= 7 * 24 * 3600
    seen: list[httpx.Request] = []

    def spy(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handle(request)

    with ph.direct_hint_getter(BASE, httpx.MockTransport(spy)) as get:
        found = ph.read_hints(get, None, {"node_load1"}, now=1_800_000_000)
    series = next(r for r in seen if r.url.path.endswith("/series"))
    assert int(series.url.params["end"]) - int(series.url.params["start"]) == ph.WINDOW_SECONDS
    note = ph.hints_document(found, BASE)["_comment"]
    assert "7 days" in note and BASE in note and "one series" in note


CONVERT = ["convert-grafana-dashboards"]


def _widgets(out: Path) -> list[dict[str, Any]]:
    return [
        w
        for f in sorted((out / "dashboard-ir").glob("*.json"))
        for w in json.loads(f.read_text())["widgets"]
    ]


def _convert(tmp_path: Path, name: str, hints: dict[str, Any] | None) -> list[dict[str, Any]]:
    extract, out = _extract(tmp_path), tmp_path / name
    args = [*CONVERT, "--in", str(extract), "--out", str(out)]
    if hints is not None:
        path = tmp_path / f"{name}.json"
        path.write_text(json.dumps(hints))
        args += ["--hints", str(path)]
    assert CliRunner().invoke(app, args).exit_code == 0
    return _widgets(out)


def test_without_hints_the_gauge_sum_stays_a_placeholder(tmp_path: Path) -> None:
    (widget,) = [w for w in _convert(tmp_path, "none", None) if w["source_type"] != "row"]
    assert widget["kind"] == "unsupported"


def test_many_series_never_become_a_flat_average(tmp_path: Path) -> None:
    """With several series the engine sums the newest value of each; it must not average."""
    many = _convert(tmp_path, "many", {"series": {"node_load1": ["instance"]}})
    (widget,) = [w for w in many if w["source_type"] != "row"]
    text = json.dumps(widget)
    assert "FACET instance" in text and "average(node_load1)" not in text


def test_one_series_converts_as_approximate_with_the_window_in_the_note(tmp_path: Path) -> None:
    one = _convert(tmp_path, "one", {"series": {"node_load1": []}})
    (widget,) = [w for w in one if w["source_type"] != "row"]
    assert widget["kind"] != "unsupported" and widget["confidence"] == "approximate"
    assert any("7 days" in reason for reason in widget["reasons"])
