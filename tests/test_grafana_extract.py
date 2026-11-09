"""The Grafana extractor: allow-list, host check, secrets, bounds. No network: MockTransport."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.ir import Confidence
from importer.sources import REGISTRY
from importer.sources.grafana import extract as gx
from importer.sources.grafana.extract import GrafanaError, extract

HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_SECRETSECRET"  # noqa: S105 - a made-up value for leak tests
FIXTURES = Path(__file__).parent / "fixtures" / "grafana"
RULES: list[dict[str, Any]] = json.loads((FIXTURES / "provisioning_alert_rules.json").read_text())
CONTACT_POINTS = [
    {"uid": "c1", "name": "team-slack", "type": "slack", "settings": {"token": "SLACKSECRET"}},
    {"uid": "c2", "name": "mail", "type": "email", "settings": {"addresses": "a@example.com"}},
    {"uid": "c3", "name": "mail2", "type": "email", "settings": {}},
]
POLICIES = {
    "receiver": "mail",
    "routes": [
        {"receiver": "team-slack", "routes": [{"receiver": "mail"}]},
        {"receiver": "mail"},
    ],
}
MUTE = [{"name": "nights"}, {"name": "weekends"}]

Handler = Callable[[httpx.Request], httpx.Response]


def serve(
    rules: Any = None, contact: Any = None, policies: Any = None, mute: Any = None, fail: str = ""
) -> Handler:
    bodies = {
        gx.RULES_PATH: RULES if rules is None else rules,
        gx.INVENTORY_PATHS["contact_points"]: CONTACT_POINTS if contact is None else contact,
        gx.INVENTORY_PATHS["policies"]: POLICIES if policies is None else policies,
        gx.INVENTORY_PATHS["mute_timings"]: MUTE if mute is None else mute,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path not in bodies:
            return httpx.Response(404, json={"message": "not on the list"})
        if fail and path.endswith(fail):
            return httpx.Response(403, json={"message": "no permission " + TOKEN})
        return httpx.Response(200, json=bodies[path])

    return handler


def getter(handler: Handler, token: str = TOKEN) -> AbstractContextManager[gx.Getter]:
    return gx.http_getter(token, HOST, transport=httpx.MockTransport(handler))


def read(handler: Handler, tmp_path: Path, **kwargs: Any) -> gx.ExtractResult:
    with getter(handler) as get:
        return extract(get, HOST, tmp_path / "out", **kwargs)


def test_the_real_getter_reads_rules_and_counts_and_sends_a_bearer_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve()(request)

    result = read(handler, tmp_path)
    assert result.rules == 17
    assert result.inventory == {
        "contact_points": {"count": 3, "by_type": {"slack": 1, "email": 2}},
        "policies": {"routes": 3},
        "mute_timings": {"count": 2},
    }
    assert all(r.method == "GET" for r in seen)
    assert {r.url.path for r in seen} == gx.ALLOWED_PATHS
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_no_request_has_a_query_string_so_contact_points_are_never_decrypted(
    tmp_path: Path,
) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve()(request)

    read(handler, tmp_path)
    assert seen and all(not r.url.query and "decrypt" not in str(r.url) for r in seen)


def test_contact_point_settings_are_never_written(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    written = "".join(p.read_text() for p in (tmp_path / "out").rglob("*.json"))
    assert "SLACKSECRET" not in written and "a@example.com" not in written
    assert "team-slack" not in written


def test_the_output_is_readable_by_the_loader_and_converts(tmp_path: Path) -> None:
    read(serve(), tmp_path)
    monitors = REGISTRY["grafana"].convert(tmp_path / "out")
    assert len(monitors) == 17
    assert sum(m.confidence is Confidence.APPROXIMATE for m in monitors) == 4


def test_a_path_off_the_allow_list_is_refused_by_the_real_getter() -> None:
    with getter(serve()) as get:
        for path in ("/api/v1/provisioning/contact-points/export", "/api/datasources", "", "x"):
            with pytest.raises(GrafanaError, match="allow-list"):
                get(path)
        with pytest.raises(GrafanaError, match="allow-list"):
            get(gx.INVENTORY_PATHS["contact_points"] + "?decrypt=true")


def test_the_client_is_really_closed_when_the_block_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[httpx.Client] = []

    class Recording(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(httpx, "Client", Recording)
    with gx.http_getter("token", HOST):
        assert not made[0].is_closed
    assert made[0].is_closed
    with pytest.raises(RuntimeError), gx.http_getter("token", HOST):
        raise RuntimeError("boom")
    assert made[1].is_closed


# host checks


@pytest.mark.parametrize(
    "url",
    [
        "https://mystack.grafana.net",
        "https://mystack.grafana.net/",
        "mystack.grafana.net",
        "  HTTPS://MyStack.Grafana.net  ",
        "https://my-stack-2.grafana.net",
    ],
)
def test_grafana_cloud_hosts_are_accepted(url: str) -> None:
    assert gx.validate_host(url) == "https://" + url.strip().lower().removeprefix(
        "https://"
    ).removesuffix("/")


@pytest.mark.parametrize(
    "url",
    [
        "http://mystack.grafana.net",
        "https://grafana.net",
        "https://evil.example.com",
        "https://mystack.grafana.net.evil.com",
        "https://evil.com/mystack.grafana.net",
        "https://user:pw@mystack.grafana.net",
        "https://mystack.grafana.net/path",
        "https://mystack.grafana.net:8443",
        "https://mystack.grafana.net?x=1",
        "https://my_stack.grafana.net",
        "https://a.b.grafana.net",
        "https://127.0.0.1",
        "https://myst\u212aack.grafana.net",
        "https://mystack.grafana.n\u0435t",
        "ftp://mystack.grafana.net",
        "",
    ],
)
def test_other_hosts_are_refused_without_the_option(url: str) -> None:
    with pytest.raises(ValueError, match=r"grafana\.net"):
        gx.validate_host(url)


def test_another_https_host_needs_the_explicit_option_and_must_match() -> None:
    assert gx.validate_host("https://grafana.example.com", "grafana.example.com") == (
        "https://grafana.example.com"
    )
    assert gx.validate_host("grafana.example.com:443", "grafana.example.com:443") == (
        "https://grafana.example.com:443"
    )
    with pytest.raises(ValueError, match="does not match"):
        gx.validate_host("https://other.example.com", "grafana.example.com")


@pytest.mark.parametrize(
    "allow",
    [
        "",
        "localhost",
        "127.0.0.1",
        "10.0.0.5:3000",
        "a b.com",
        "https://x.example.com",
        "x.example.com/path",
        "-x.example.com",
        "x_y.example.com",
        "user@x.example.com",
    ],
)
def test_a_bad_allow_host_is_refused(allow: str) -> None:
    with pytest.raises(ValueError, match="--allow-host"):
        gx.validate_host("https://x.example.com", allow)


def test_http_is_never_allowed_even_with_the_option() -> None:
    with pytest.raises(ValueError, match="https"):
        gx.validate_host("http://x.example.com", "x.example.com")


def test_a_bad_host_is_refused_before_any_call(tmp_path: Path) -> None:
    calls: list[str] = []

    def get(path: str) -> Any:
        calls.append(path)
        return []

    with pytest.raises(ValueError, match=r"grafana\.net"):
        extract(get, "https://evil.example.com", tmp_path / "o")
    assert calls == []


ODD_HOSTS = [
    "127.1",
    "1.1",
    "grafana.example.1",
    "0x7f.0.0.1",
    "0xa9.0xfe.0xa9.0xfe",
    "grafana.example.0x7f",
    "grafana.example.com:8443",
    "grafana.example.com:80",
    "grafana.example.com:0",
    "grafana.example.com:99999",
    "metadata.google.internal",
    "svc.internal",
    "printer.local",
    "box.localhost",
    "gr\u212aafana.example.com",
    "gr\u0430fana.example.com",
]


@pytest.mark.parametrize("host", ODD_HOSTS)
def test_hosts_that_resolve_to_internal_addresses_or_look_odd_are_refused(host: str) -> None:
    with pytest.raises(ValueError, match=r"--allow-host"):
        gx.validate_host("https://" + host, host)


def test_port_443_is_the_only_port_and_the_host_is_lowercase_ascii() -> None:
    assert gx.validate_host("https://Grafana.Example.com:443", "grafana.example.com:443") == (
        "https://grafana.example.com:443"
    )
    result = gx.validate_host("https://grafana.example.com", "grafana.example.com")
    assert result.isascii() and result == result.lower()


def test_the_getter_checks_the_host_itself_not_only_the_caller() -> None:
    for base in ("https://evil.example.com", "https://127.1", "https://metadata.google.internal"):
        with pytest.raises(GrafanaError), gx.http_getter("t", base):
            pass
    with gx.http_getter("t", "https://grafana.example.com", allow_host="grafana.example.com"):
        pass
    with (
        pytest.raises(GrafanaError),
        gx.http_getter("t", "https://grafana.example.com", allow_host="other.example.com"),
    ):
        pass


def test_the_getter_refuses_a_base_that_is_not_a_bare_host() -> None:
    for base in ("http://x.grafana.net", "https://x.grafana.net/api", "https://u@x.grafana.net"):
        with pytest.raises(GrafanaError, match="host"), gx.http_getter("t", base):
            pass


# errors and bounds


def test_an_existing_folder_with_files_is_refused(tmp_path: Path) -> None:
    out = tmp_path / "out"
    out.mkdir()
    (out / "x").write_text("1")
    with pytest.raises(ValueError, match="already has files"):
        read(serve(), tmp_path)


@pytest.mark.parametrize("body", [["a", "list"], "text", 5, None, {"message": ["x"]}])
def test_an_error_body_of_any_shape_gives_a_clean_error(body: Any, tmp_path: Path) -> None:
    with pytest.raises(GrafanaError, match="returned 500"):
        read(lambda _r: httpx.Response(500, json=body), tmp_path)


def test_a_401_that_echoes_the_token_does_not_leak_it(tmp_path: Path) -> None:
    echo = f"bad token {TOKEN}; Authorization: Bearer {TOKEN} and glsa_XYZ_LEAKED and glc_ABC_LEAK"
    with pytest.raises(GrafanaError) as err:
        read(lambda _r: httpx.Response(401, json={"message": echo}), tmp_path)
    text = str(err.value)
    assert "returned 401" in text
    for part in ("SECRETSECRET", "AbCdEf", "LEAKED", "LEAK", TOKEN):
        assert part not in text


def test_a_long_server_message_is_capped(tmp_path: Path) -> None:
    with pytest.raises(GrafanaError) as err:
        read(lambda _r: httpx.Response(400, json={"message": "x" * 5000}), tmp_path)
    assert len(str(err.value)) < 400


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_a_redirect_is_an_error_and_is_not_followed(status: int, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.host)
        return httpx.Response(status, headers={"location": "https://evil.example.com/x"})

    with pytest.raises(GrafanaError, match=f"returned {status}"):
        read(handler, tmp_path)
    assert calls == ["mystack.grafana.net"]


def test_a_body_that_is_not_json_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(GrafanaError, match="not JSON"):
        read(lambda _r: httpx.Response(200, content=b"<html>"), tmp_path)


def test_a_declared_oversize_response_is_refused(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(gx.MAX_RESPONSE_BYTES + 1)})

    with pytest.raises(GrafanaError, match="larger than"):
        read(handler, tmp_path)


def test_an_oversize_body_without_a_length_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gx, "MAX_RESPONSE_BYTES", 50)
    with pytest.raises(GrafanaError, match="larger than"):
        read(lambda _r: httpx.Response(200, content=b'["' + b"x" * 100 + b'"]'), tmp_path)


def test_a_connection_failure_names_only_the_error_type(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("secret detail " + TOKEN)

    with pytest.raises(GrafanaError) as err:
        read(handler, tmp_path)
    assert "ConnectError" in str(err.value) and "secret" not in str(err.value)


def test_the_rule_list_must_be_a_list(tmp_path: Path) -> None:
    with pytest.raises(GrafanaError, match="list of alert rules"):
        read(serve(rules={"rules": []}), tmp_path)


def test_the_rule_cap_stops_a_runaway_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gx, "MAX_RULES", 5)
    with pytest.raises(GrafanaError, match="more than 5 alert rules"):
        read(serve(), tmp_path)


def test_items_that_are_not_rules_are_dropped_with_a_warning_and_repeats_kept_once(
    tmp_path: Path,
) -> None:
    result = read(serve(rules=[*RULES[:2], RULES[0], "text", 5, None]), tmp_path)
    assert result.rules == 2
    assert any("left out" in w for w in result.warnings)


def test_a_failure_reading_rules_stops_but_inventory_only_warns(tmp_path: Path) -> None:
    with pytest.raises(GrafanaError):
        read(serve(fail="alert-rules"), tmp_path / "a")
    result = read(serve(fail="contact-points"), tmp_path / "b")
    assert result.rules == 17
    assert result.inventory["contact_points"] is None
    assert any("contact points were NOT read" in w for w in result.warnings)
    assert all("SECRET" not in w for w in result.warnings)


def test_odd_inventory_shapes_do_not_crash(tmp_path: Path) -> None:
    result = read(
        serve(contact={"x": 1}, policies=[1, 2], mute="text"),
        tmp_path,
    )
    assert result.inventory["contact_points"] is None
    assert result.inventory["policies"] is None
    assert result.inventory["mute_timings"] is None
    assert len(result.warnings) == 3


def test_a_contact_point_type_with_odd_characters_is_counted_as_other(tmp_path: Path) -> None:
    contact = [{"type": "x" * 200}, {"type": "<script>"}, {"type": 5}, {}]
    result = read(serve(contact=contact), tmp_path)
    assert result.inventory["contact_points"] == {"count": 4, "by_type": {"other": 4}}


def test_a_deep_policy_tree_is_bounded(tmp_path: Path) -> None:
    tree: dict[str, Any] = {"receiver": "x"}
    for _ in range(90):  # two brackets a level, so 180 deep: under the 200 limit, over the cap
        tree = {"receiver": "x", "routes": [tree]}
    result = read(serve(policies=tree), tmp_path)
    assert result.inventory["policies"] == {"routes": gx.MAX_POLICY_DEPTH}


# token and CLI


def test_the_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRAFANA_SA_TOKEN", raising=False)
    with pytest.raises(GrafanaError, match="GRAFANA_SA_TOKEN"):
        gx.api_token()
    monkeypatch.setenv("GRAFANA_SA_TOKEN", f" {TOKEN} ")
    assert gx.api_token() == TOKEN


@pytest.mark.parametrize("token", ["glsa_éé_SECRET", "glsa_ab cd", "glsa_a" + chr(0x2013) + "b"])
def test_a_token_with_odd_characters_is_refused_without_echoing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(), token)
    assert out.exit_code == 1 and "plain ASCII" in out.output
    for part in ("glsa", "é", "SECRET", chr(0x2013), "cd"):
        assert part not in out.output


def run_cli(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    handler: Handler,
    token: str = TOKEN,
    url: str = HOST,
    extra: list[str] | None = None,
) -> Any:
    monkeypatch.setenv("GRAFANA_URL", url)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", token)
    monkeypatch.setattr(
        "importer.cli.grafana_getter",
        lambda t, b, allow_host=None: gx.http_getter(
            t, b, httpx.MockTransport(handler), allow_host=allow_host
        ),
    )
    return CliRunner().invoke(
        app, ["extract-grafana", "--out", str(tmp_path / "o"), *(extra or [])]
    )


def test_the_cli_reads_the_stack_and_prints_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve())
    assert out.exit_code == 0, out.output
    folder = tmp_path / "o"
    assert "17 alert rules" in out.output and "3 contact points" in out.output
    assert (
        f"importer convert --source grafana --in {folder} --out {folder / 'alerts'}" in out.output
    )
    assert f"importer report {folder} --source grafana" in out.output
    assert TOKEN not in out.output and "SLACKSECRET" not in out.output


def test_the_cli_refuses_a_host_that_is_not_grafana_cloud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(), url="https://grafana.example.com")
    assert out.exit_code == 1 and "--allow-host" in out.output
    assert TOKEN not in out.output and not (tmp_path / "o").exists()


def test_the_cli_accepts_another_host_with_the_explicit_option(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(
        monkeypatch,
        tmp_path,
        serve(),
        url="https://grafana.example.com",
        extra=["--allow-host", "grafana.example.com"],
    )
    assert out.exit_code == 0, out.output


def test_the_cli_reports_a_missing_url_without_printing_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("GRAFANA_URL", raising=False)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    result = CliRunner().invoke(app, ["extract-grafana", "--out", str(tmp_path / "o")])
    assert result.exit_code == 1 and "GRAFANA_URL" in result.output
    assert TOKEN not in result.output


def test_the_cli_fails_and_hides_the_token_when_rules_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"message": f"invalid {TOKEN}"})

    out = run_cli(monkeypatch, tmp_path, handler)
    assert out.exit_code == 1 and "returned 401" in out.output
    assert "SECRETSECRET" not in out.output and TOKEN not in out.output


def test_the_cli_says_when_inventory_was_not_read_and_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve(fail="policies"))
    assert out.exit_code == 0, out.output
    assert "notification policies were NOT read" in out.output
    assert "SECRETSECRET" not in out.output


def test_the_cli_help_documents_the_host_rule_and_the_certificate_variable(
    help_text: Callable[..., str],
) -> None:
    text = help_text("extract-grafana", "--help")
    assert "GRAFANA_SA_TOKEN" in text and "grafana.net" in text and "--allow-host" in text
    assert "SSL_CERT_FILE" in text


def test_the_documented_sequence_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run_cli(monkeypatch, tmp_path, serve()).exit_code == 0
    folder = tmp_path / "o"
    runner = CliRunner()
    args = ["convert", "--source", "grafana", "--in", str(folder), "--out", str(folder / "alerts")]
    assert runner.invoke(app, args).exit_code == 0
    target = tmp_path / "r.html"
    report = runner.invoke(
        app, ["report", str(folder), "--source", "grafana", "--out", str(target)]
    )
    assert report.exit_code == 0, report.output
    html = target.read_text()
    assert "4 of 17 alert rules migrated" in html and "Grafana" in html
