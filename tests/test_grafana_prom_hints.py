"""grafana-prom-hints: recorded-style Prometheus responses through a mock transport, no network."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana import prom_hints as ph

HOST = "https://stack.grafana.net"
TOKEN = "glsa_secret_token_123"  # noqa: S105 - a made-up value
UID = "prom-uid-1"
PROXY = f"/api/datasources/proxy/uid/{UID}/api/v1"

METADATA = {
    "status": "success",
    "data": {
        "node_load1": [{"type": "gauge", "help": "1m load", "unit": ""}],
        "process_open_fds_total": [{"type": "gauge", "help": "odd name", "unit": ""}],
        "http_requests": [{"type": "counter", "help": "no suffix", "unit": ""}],
        "http_requests_total": [{"type": "counter", "help": "", "unit": ""}],
        "lat": [{"type": "histogram", "help": "", "unit": ""}],
        "rpc": [{"type": "summary", "help": "", "unit": ""}],
        "big_gauge": [{"type": "gauge", "help": "", "unit": ""}],
    },
}


PRESENT = [*METADATA["data"], "recorded_ratio"]


def series(name: str, *label_sets: dict[str, str]) -> dict[str, Any]:
    return {"status": "success", "data": [{"__name__": name, **ls} for ls in label_sets]}


SERIES = {
    "node_load1": series(
        "node_load1",
        {"job": "node", "instance": "a:9100"},
        {"job": "node", "instance": "b:9100"},
    ),
    "process_open_fds_total": series("process_open_fds_total", {"job": "api"}),
    "recorded_ratio": series("recorded_ratio", {"job": "a"}, {"job": "b"}),
    "big_gauge": series("big_gauge", *({"pod": f"p{i}"} for i in range(ph.MAX_SERIES + 1))),
}


def handler(seen: list[httpx.Request]):  # type: ignore[no-untyped-def]
    def handle(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == f"/api/datasources/uid/{UID}":
            return httpx.Response(200, json={"uid": UID, "type": "prometheus"})
        if path == "/api/datasources/name/Cloud":
            return httpx.Response(200, json={"uid": UID, "type": "prometheus"})
        if path == "/api/datasources/uid/loki1":
            return httpx.Response(200, json={"uid": "loki1", "type": "loki"})
        if path == f"{PROXY}/metadata":
            return httpx.Response(200, json=METADATA)
        if path == f"{PROXY}/label/__name__/values":
            return httpx.Response(200, json={"status": "success", "data": PRESENT})
        if path == f"{PROXY}/series":
            match = request.url.params["match[]"]
            name = match.split('"')[1]
            return httpx.Response(200, json=SERIES.get(name, series(name)))
        return httpx.Response(404, json={"message": "not found"})

    return handle


def getter(seen: list[httpx.Request], max_requests: int = ph.MAX_REQUESTS):  # type: ignore[no-untyped-def]
    return ph.hint_getter(
        TOKEN, HOST, transport=httpx.MockTransport(handler(seen)), max_requests=max_requests
    )


def test_types_and_series_keys() -> None:
    seen: list[httpx.Request] = []
    wanted = {
        "node_load1",
        "process_open_fds_total",
        "http_requests",
        "http_requests_total",
        "lat_bucket",
        "lat_count",
        "rpc_sum",
        "rpc",
        "mystery_total",
        "recorded_ratio",
        "big_gauge",
    }
    with getter(seen) as get:
        assert ph.find_datasource(get, UID) == UID
        found = ph.read_hints(get, UID, wanted)
    assert found.types == {
        "node_load1": "gauge",
        "process_open_fds_total": "gauge",
        "http_requests": "counter",
        "http_requests_total": "counter",
        "lat_bucket": "histogram_bucket",
        "lat_count": "counter",
        "rpc_sum": "counter",
        "rpc": "gauge",
        "big_gauge": "gauge",
    }
    # no type in the metadata: no type hint, but the series are still read when the name exists
    assert found.unknown == ["mystery_total", "recorded_ratio"]
    assert found.series["recorded_ratio"] == ["job"]
    assert "mystery_total" not in found.series
    assert found.series["node_load1"] == ["instance"]
    assert found.series["process_open_fds_total"] == []
    assert "big_gauge" not in found.series and found.truncated == ["big_gauge"]
    assert all(r.method == "GET" for r in seen)
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in seen)


def test_find_datasource_by_name_and_refuses_other_types() -> None:
    with getter([]) as get:
        assert ph.find_datasource(get, "Cloud") == UID
        with pytest.raises(ph.HintsError, match="not a Prometheus"):
            ph.find_datasource(get, "loki1")
        with pytest.raises(ph.HintsError):
            ph.find_datasource(get, "../x")


@pytest.mark.parametrize(
    ("path", "params"),
    [
        ("/api/datasources/proxy/uid/x/api/v1/query", {}),
        ("/api/datasources/proxy/uid/x/api/v1/query_range", {"query": "up"}),
        (f"{PROXY}/series", {}),
        (f"{PROXY}/series", {"match[]": "up"}),
        (f"{PROXY}/series", {"match[]": '{__name__="a"}', "x": "1"}),
        (f"{PROXY}/metadata", {"metric": "a"}),
        ("/api/admin/users", {}),
        ("/api/datasources/uid/a/../../../x", {}),
    ],
)
def test_other_paths_and_parameters_are_refused(path: str, params: dict[str, str]) -> None:
    seen: list[httpx.Request] = []
    with getter(seen) as get, pytest.raises(ph.HintsError, match="allow-list"):
        get(path, params)
    assert seen == []


def test_request_cap_stops_the_run() -> None:
    with getter([], max_requests=2) as get:
        found = ph.read_hints(get, UID, {"node_load1", "process_open_fds_total", "big_gauge"})
    assert found.types and found.warnings and "more than 2 requests" in found.warnings[0]


def test_token_is_scrubbed_from_server_text_and_redirects_are_errors() -> None:
    def hostile(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("metadata"):
            return httpx.Response(302, headers={"location": "https://evil.example/"})
        return httpx.Response(500, json={"message": f"bad {TOKEN} Bearer {TOKEN}"})

    with ph.hint_getter(TOKEN, HOST, transport=httpx.MockTransport(hostile)) as get:
        with pytest.raises(ph.HintsError) as err:
            ph.find_datasource(get, UID)
        assert TOKEN not in str(err.value)
        with pytest.raises(ph.HintsError) as err2:
            get(f"{PROXY}/metadata", {"limit": "5"})
        assert "302" in str(err2.value)


def test_host_must_be_a_grafana_host() -> None:
    with pytest.raises(ph.HintsError), ph.hint_getter(TOKEN, "https://evil.example"):
        pass
    with pytest.raises(ph.HintsError), ph.hint_getter(TOKEN, "http://stack.grafana.net"):
        pass


def test_key_labels() -> None:
    assert ph.key_labels([]) == []
    assert ph.key_labels([{"__name__": "m", "job": "a"}]) == []
    rows = [{"job": "n", "instance": i, "mountpoint": m} for i in "ab" for m in ("/", "/boot")]
    assert ph.key_labels(rows) == ["instance", "mountpoint"]
    # constant labels never count; an absent label counts as ""
    assert ph.key_labels([{"job": "x", "pod": "p"}, {"job": "x"}]) == ["pod"]
    # identical label sets cannot be told apart
    assert ph.key_labels([{"job": "x"}, {"job": "x"}]) is None


@pytest.mark.parametrize(
    ("expr", "names"),
    [
        (
            'sum by (instance) (rate(node_cpu_seconds_total{mode!="idle"}[5m]))',
            {"node_cpu_seconds_total"},
        ),
        (
            "node_load1 / on(instance) count(node_cpu_seconds_total) by (instance)",
            {"node_load1", "node_cpu_seconds_total"},
        ),
        (
            'histogram_quantile(0.9, sum by (le) (rate(lat_bucket{job="$job"}[$__rate_interval])))',
            {"lat_bucket"},
        ),
        ('up{job=~"a|b"} == 0', {"up"}),
        ("100 - avg(irate(x_total[5m])) * 100 offset 1h", {"x_total"}),
        ('label_replace(up, "dst", "$1", "src", "(.*)")', {"up"}),
    ],
)
def test_metric_names_in_expressions(expr: str, names: set[str]) -> None:
    assert ph.metrics_in_expression(expr) == names


def dashboard(exprs: list[str]) -> dict[str, Any]:
    return {
        "uid": "d1",
        "title": "D",
        "schemaVersion": 39,
        "panels": [
            {
                "id": i + 1,
                "type": "timeseries",
                "title": f"p{i}",
                "datasource": {"type": "prometheus", "uid": "p"},
                "targets": [{"refId": "A", "expr": e}],
            }
            for i, e in enumerate(exprs)
        ],
    }


def test_the_command_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    extract = tmp_path / "extract" / "dashboards"
    extract.mkdir(parents=True)
    exprs = ["sum by (instance) (node_load1)", "process_open_fds_total", "rate(http_requests[5m])"]
    (extract / "dashboard-1.json").write_text(json.dumps({"dashboard": dashboard(exprs)}))
    monkeypatch.setenv("GRAFANA_URL", HOST)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    real = httpx.Client
    seen: list[httpx.Request] = []

    def client(*args: Any, **kwargs: Any) -> httpx.Client:
        kwargs["transport"] = httpx.MockTransport(handler(seen))
        return real(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", client)
    out = tmp_path / "hints.json"
    args = ["grafana-prom-hints", "--datasource", UID, "--metrics-from", str(tmp_path / "extract")]
    result = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert TOKEN not in result.output and TOKEN not in out.read_text()
    data = json.loads(out.read_text())
    assert data["types"] == {
        "http_requests": "counter",
        "node_load1": "gauge",
        "process_open_fds_total": "gauge",
    }
    assert data["series"] == {"node_load1": ["instance"], "process_open_fds_total": []}
    again = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert again.exit_code == 1 and "already exists" in again.output + (again.stderr or "")

    # the hints file feeds the converter: the gauge named *_total now converts
    conv = tmp_path / "conv"
    convert = ["convert-grafana-dashboards", "--in", str(tmp_path / "extract")]
    done = CliRunner().invoke(app, [*convert, "--out", str(conv), "--hints", str(out)])
    assert done.exit_code == 0, done.output
    assert "Hints loaded: 3 types, 2 series sets" in done.output
    nerdgraph = "".join(p.read_text() for p in (conv / "nerdgraph").glob("*.json"))
    assert "average(process_open_fds_total)" in nerdgraph
    assert "average(node_load1)" in nerdgraph


def test_the_command_needs_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)
    extract = tmp_path / "e"
    extract.mkdir()
    (extract / "d.json").write_text(json.dumps({"dashboard": dashboard(["up"])}))
    result = CliRunner().invoke(
        app,
        [
            "grafana-prom-hints",
            "--datasource",
            "x",
            "--metrics-from",
            str(extract),
            "--out",
            str(tmp_path / "h.json"),
        ],
    )
    assert result.exit_code == 1
