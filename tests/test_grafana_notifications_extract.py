"""The notifications extractor: read-only allow-list, safeguards, secrets. No network."""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core import safe_http
from importer.sources.grafana.extract import GrafanaError
from importer.sources.grafana.notifications import extract as nx
from importer.sources.grafana.notifications.loader import load_export

HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_TOKENTOKEN"  # noqa: S105 - a made-up value for leak tests
SECRET = "SECRET-VALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests
Handler = Callable[[httpx.Request], httpx.Response]

CONTACT_POINTS = [
    {
        "uid": "c1",
        "name": "pager",
        "type": "pagerduty",
        "settings": {"integrationKey": "[REDACTED]", "severity": "critical"},
    },
    {
        "uid": "c2",
        "name": "hook",
        "type": "webhook",
        "settings": {
            "url": "https://hooks.example.com/path/" + SECRET + "?token=" + SECRET,
            "headers": {"X-Api-Key": SECRET},
            "password": SECRET,
            "title": "{{ .Labels.password }}" + SECRET,
        },
    },
    {"uid": "c3", "name": "mail", "type": "email", "settings": {"addresses": "ops@example.com"}},
]
POLICIES = {
    "receiver": "mail",
    "group_by": ["grafana_folder", "alertname"],
    "routes": [
        {"receiver": "pager", "matchers": ['severity="critical"'], "mute_time_intervals": ["n"]},
    ],
}
MUTE = [
    {
        "name": "n",
        "time_intervals": [
            {
                "times": [{"start_time": "22:00", "end_time": "24:00"}],
                "weekdays": ["monday:friday"],
                "location": "Europe/Berlin",
            }
        ],
    }
]
TEMPLATES = [{"name": "custom", "template": '{{ define "custom" }}' + SECRET + "{{ end }}"}]
DATASOURCES = [
    {"type": "prometheus", "uid": "prom1", "name": "Prom " + SECRET, "basicAuthPassword": SECRET},
    {"type": "loki", "uid": "loki1", "name": "Loki"},
    {"type": "tempo", "uid": "tempo1", "name": "Tempo"},
    {"type": "prometheus", "uid": "bad/uid?x=1", "name": "odd"},
]
RULER = {
    "ns": [
        {
            "name": "g",
            "interval": "1m",
            "rules": [{"alert": "A", "expr": "up == 0", "for": "5m", "labels": {"s": "p"}}],
        }
    ]
}


def serve(
    fail: dict[str, int] | None = None,
    bodies: dict[str, Any] | None = None,
    seen: list[str] | None = None,
) -> Handler:
    table: dict[str, Any] = {
        nx.CONTACT_POINTS: CONTACT_POINTS,
        nx.POLICIES: POLICIES,
        nx.MUTE_TIMINGS: MUTE,
        nx.TEMPLATES: TEMPLATES,
        nx.DATASOURCES: DATASOURCES,
        nx.ruler_path("prom1"): RULER,
        nx.ruler_path("loki1"): RULER,
        **(bodies or {}),
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if seen is not None:
            seen.append(str(request.url))
        if fail and path in fail:
            return httpx.Response(fail[path], json={"message": "no permission " + TOKEN})
        if path not in table:
            return httpx.Response(404, json={"message": "not on the list"})
        return httpx.Response(200, json=table[path])

    return handler


def getter(handler: Handler, token: str = TOKEN) -> Any:
    return nx.notifications_getter(token, HOST, transport=httpx.MockTransport(handler))


def run(handler: Handler, out: Path) -> nx.NotificationsResult:
    with getter(handler) as get:
        return nx.extract_notifications(get, out)


def written(folder: Path) -> str:
    return "".join(p.read_text() for p in sorted(folder.rglob("*")) if p.is_file())


def test_the_real_getter_reads_every_section_and_writes_sanitised_files(tmp_path: Path) -> None:
    result = run(serve(), tmp_path / "o")
    assert result.warnings == []
    assert (result.contact_points, result.mute_timings, result.templates) == (3, 1, 1)
    assert (result.datasources, result.rule_groups) == (3, 2)
    export = load_export(tmp_path / "o")
    assert [(c.type, c.name) for c in export.contact_points] == [
        ("pagerduty", "pager"), ("webhook", "hook"), ("email", "mail")
    ]  # fmt: skip
    assert export.contact_points[0].redacted == ["integrationKey"]
    assert export.contact_points[1].url_host == "hooks.example.com"
    assert export.policy is not None and export.policy.root.routes[0].receiver == "pager"
    assert export.mute_timings[0].intervals[0].location == "Europe/Berlin"
    assert {g.kind for g in export.rule_groups} == {"prometheus", "loki"}
    assert export.problems == []


def test_no_secret_reaches_any_output_file(tmp_path: Path) -> None:
    run(serve(), tmp_path / "o")
    text = written(tmp_path / "o")
    for leak in (SECRET, TOKEN, "token=", "X-Api-Key", "/path/", "Prom "):
        assert leak not in text
    assert "ops@example.com" in (tmp_path / "o" / "contact-points.json").read_text()
    assert "ops@example.com" not in written(tmp_path / "o").replace(
        (tmp_path / "o" / "contact-points.json").read_text(), ""
    )


def test_every_request_is_a_get_without_a_query_string(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve()(request)

    run(handler, tmp_path / "o")
    assert seen and all(r.method == "GET" for r in seen)
    assert all(not r.url.query and "decrypt" not in str(r.url) for r in seen)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


@pytest.mark.parametrize(
    "path",
    [
        nx.CONTACT_POINTS + "?decrypt=true",
        nx.CONTACT_POINTS + "/export?decrypt=true",
        "/api/v1/provisioning/contact-points/export",
        nx.CONTACT_POINTS + "#x",
        nx.CONTACT_POINTS + "/",
        "/api/v1/provisioning/alert-rules",
        "/api/ruler/../../admin/api/v1/rules",
        "/api/ruler/a b/api/v1/rules",
        "/api/ruler/" + "x" * 41 + "/api/v1/rules",
        "",
        "x",
        "https://evil.example.com/api/datasources",
    ],
)
def test_the_decrypt_parameter_and_other_paths_can_never_be_sent(path: str) -> None:
    seen: list[str] = []
    with getter(serve(seen=seen)) as get, pytest.raises(GrafanaError, match="allow-list"):
        get(path)
    assert seen == []


def test_a_ruler_path_for_a_valid_uid_is_allowed() -> None:
    with getter(serve()) as get:
        assert get(nx.ruler_path("prom1")) == RULER


def test_ruler_calls_are_made_only_for_prometheus_and_loki_sources_with_plain_uids(
    tmp_path: Path,
) -> None:
    seen: list[str] = []
    run(serve(seen=seen), tmp_path / "o")
    rulers = sorted(u for u in seen if "/api/ruler/" in u)
    assert rulers == [
        HOST + nx.ruler_path("loki1"),
        HOST + nx.ruler_path("prom1"),
    ]


def test_ruler_calls_are_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nx, "MAX_RULER_SOURCES", 3)
    many = [{"type": "prometheus", "uid": f"p{i}"} for i in range(50)]
    bodies: dict[str, Any] = {
        nx.DATASOURCES: many,
        **{nx.ruler_path(f"p{i}"): RULER for i in range(50)},
    }
    seen: list[str] = []
    result = run(serve(bodies=bodies, seen=seen), tmp_path / "o")
    assert len([u for u in seen if "/api/ruler/" in u]) == 3
    assert any("rule files" in w for w in result.warnings)


def test_a_failing_section_is_a_warning_and_the_others_are_still_written(tmp_path: Path) -> None:
    result = run(serve(fail={nx.POLICIES: 403}), tmp_path / "o")
    assert any("notification policies" in w and "403" in w for w in result.warnings)
    assert all(TOKEN not in w for w in result.warnings)
    assert not (tmp_path / "o" / "policies.json").exists()
    assert (tmp_path / "o" / "contact-points.json").exists()


def test_a_missing_ruler_is_a_warning_not_an_error(tmp_path: Path) -> None:
    result = run(serve(fail={nx.ruler_path("prom1"): 404}), tmp_path / "o")
    assert result.rule_groups == 1 and any("rule" in w for w in result.warnings)


def test_a_redirect_is_an_error_and_is_not_followed(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(302, headers={"location": "https://evil.example.com/x"})

    with getter(handler) as get, pytest.raises(GrafanaError, match="302"):
        get(nx.POLICIES)
    assert seen == [HOST + nx.POLICIES]


def test_server_text_is_scrubbed_of_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"message": f"boom {TOKEN} Bearer {TOKEN}"})

    with getter(handler) as get, pytest.raises(GrafanaError) as caught:
        get(nx.POLICIES)
    assert TOKEN not in str(caught.value) and "500" in str(caught.value)


def test_a_body_that_is_not_json_is_an_error() -> None:
    with (
        getter(lambda _r: httpx.Response(200, text="<html>")) as get,
        pytest.raises(GrafanaError, match="not JSON"),
    ):
        get(nx.POLICIES)


def test_a_response_over_the_cap_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_RESPONSE_BYTES", 50)
    with getter(serve()) as get, pytest.raises(GrafanaError, match="larger than"):
        get(nx.CONTACT_POINTS)


def test_the_total_bytes_across_responses_are_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(safe_http, "MAX_TOTAL_BYTES", 300)
    with getter(serve()) as get:
        get(nx.POLICIES)
        with pytest.raises(GrafanaError, match="in total"):
            get(nx.CONTACT_POINTS)


def test_the_deadline_stops_a_slow_run(monkeypatch: pytest.MonkeyPatch) -> None:
    now = [1000.0]
    monkeypatch.setattr(safe_http, "_clock", lambda: now[0])
    with getter(serve()) as get:
        get(nx.POLICIES)
        now[0] += safe_http.DEADLINE_SECONDS + 1
        with pytest.raises(GrafanaError, match="took longer"):
            get(nx.POLICIES)


def test_a_connection_failure_names_only_the_exception_type() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connect failed for " + TOKEN)

    with getter(handler) as get, pytest.raises(GrafanaError) as caught:
        get(nx.POLICIES)
    assert str(caught.value) == "could not reach Grafana: ConnectError"


@pytest.mark.parametrize(
    "base", ["http://mystack.grafana.net", "https://evil.example.com", "https://127.0.0.1"]
)
def test_only_a_validated_host_gets_the_token(base: str) -> None:
    with (
        pytest.raises(GrafanaError),
        nx.notifications_getter(TOKEN, base, transport=httpx.MockTransport(serve())),
    ):
        pass


def test_the_client_is_closed_when_the_block_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[httpx.Client] = []

    class Recording(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(httpx, "Client", Recording)
    with getter(serve()):
        assert not made[0].is_closed
    assert made[0].is_closed
    with pytest.raises(RuntimeError), getter(serve()):
        raise RuntimeError("boom")
    assert made[1].is_closed


def test_an_output_folder_with_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x").write_text("1")
    with getter(serve()) as get, pytest.raises(ValueError, match="already has files"):
        nx.extract_notifications(get, tmp_path)


def test_the_policy_round_trips_through_the_file(tmp_path: Path) -> None:
    run(serve(), tmp_path / "o")
    policy = json.loads((tmp_path / "o" / "policies.json").read_text())
    assert policy["routes"][0]["object_matchers"] == [["severity", "=", "critical"]]
    assert load_export(tmp_path / "o").policy is not None


# --- the command ---


def cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Handler, token: str = TOKEN
) -> Any:
    monkeypatch.setenv("GRAFANA_URL", HOST)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", token)
    monkeypatch.setattr(
        "importer.commands.grafana_notifications.notifications_getter",
        lambda t, b, allow_host=None: nx.notifications_getter(
            t, b, httpx.MockTransport(handler), allow_host=allow_host
        ),
    )
    return CliRunner().invoke(app, ["extract-grafana-notifications", "--out", str(tmp_path / "o")])


def test_the_command_prints_counts_only(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    result = cli(monkeypatch, tmp_path, serve())
    assert result.exit_code == 0, result.output
    assert "3 contact points" in result.output and "1 mute timings" in result.output
    for leak in (SECRET, TOKEN, "ops@example.com", "pager", "hooks.example.com"):
        assert leak not in result.output


def test_the_command_reports_a_missing_token_without_a_traceback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)
    monkeypatch.setenv("GRAFANA_URL", HOST)
    result = CliRunner().invoke(
        app, ["extract-grafana-notifications", "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1 and "GRAFANA_SA_TOKEN" in result.output
    assert "Traceback" not in result.output


def test_the_command_refuses_a_foreign_host(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAFANA_URL", "https://evil.example.com")
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    result = CliRunner().invoke(
        app, ["extract-grafana-notifications", "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1 and TOKEN not in result.output
