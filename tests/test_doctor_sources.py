"""`doctor --source` for the Dynatrace classic and Grafana sources: offline, token never shown."""

import socket

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.doctor import DOCTOR_SOURCES, passed, render, run_checks
from importer.sources import REGISTRY

DT_TOKEN = "dt0s16.ABCDEFGH.secretsecret"  # noqa: S105 - fake
GF_TOKEN = "glsa_abcdefghijklmnop_12345678"  # noqa: S105 - fake
DT_GOOD = {
    "DYNATRACE_API_TOKEN": DT_TOKEN,
    "DYNATRACE_ENV_URL": "https://abc12345.apps.dynatrace.com",
}
GF_GOOD = {"GRAFANA_SA_TOKEN": GF_TOKEN, "GRAFANA_URL": "https://mystack.grafana.net"}


def checks(source: str, env: dict[str, str]):  # type: ignore[no-untyped-def]
    return run_checks("us-east-1", None, which=lambda _: None, env=env, source=source)


def test_every_source_with_an_extractor_can_be_checked() -> None:
    assert set(DOCTOR_SOURCES) == set(REGISTRY)


@pytest.mark.parametrize("source", ["dynatrace-classic", "grafana"])
def test_the_new_sources_are_accepted_by_the_command(source: str) -> None:
    res = CliRunner().invoke(app, ["doctor", "--source", source])
    assert "--source must be" not in res.output  # it fails on the missing settings, not the name


def test_an_unknown_source_is_refused_and_the_list_comes_from_the_table() -> None:
    res = CliRunner().invoke(app, ["doctor", "--source", "nagios"])
    assert res.exit_code == 1
    assert all(name in res.output for name in DOCTOR_SOURCES)


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://abc12345.apps.dynatrace.com", True),
        ("https://abc12345.live.dynatrace.com", True),
        ("https://abc12345.live.dynatrace.com/", True),
        ("http://abc12345.live.dynatrace.com", False),
        ("https://abc12345.live.dynatrace.com/e/x", False),
        ("https://evil.example.com", False),
    ],
)
def test_classic_accepts_both_tenant_kinds(url: str, ok: bool) -> None:
    result = checks("dynatrace-classic", {**DT_GOOD, "DYNATRACE_ENV_URL": url})
    assert passed(result) is ok
    assert DT_TOKEN not in render(result)


def test_classic_names_the_failing_setting_and_never_shows_the_token() -> None:
    result = checks("dynatrace-classic", {"DYNATRACE_ENV_URL": DT_GOOD["DYNATRACE_ENV_URL"]})
    assert [c.name for c in result if c.required and not c.ok] == ["dynatrace token"]
    bad = checks("dynatrace-classic", {**DT_GOOD, "DYNATRACE_API_TOKEN": "dt0s16.“x”"})
    assert DT_TOKEN not in render(bad) and "dt0s16.“x”" not in render(bad)
    assert "aws keys" not in render(bad)


def test_grafana_good_settings_pass_and_the_token_is_not_shown() -> None:
    result = checks("grafana", GF_GOOD)
    assert passed(result)
    text = render(result)
    assert GF_TOKEN not in text and "abcdefghijklmnop" not in text
    assert "grafana token" in text and "grafana url" in text


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"GRAFANA_URL": GF_GOOD["GRAFANA_URL"]}, "grafana token"),
        ({**GF_GOOD, "GRAFANA_SA_TOKEN": "glsa_ab cd"}, "grafana token"),
        ({**GF_GOOD, "GRAFANA_SA_TOKEN": "glsa_“x”"}, "grafana token"),
        ({"GRAFANA_SA_TOKEN": GF_TOKEN}, "grafana url"),
        ({**GF_GOOD, "GRAFANA_URL": "http://mystack.grafana.net"}, "grafana url"),
        ({**GF_GOOD, "GRAFANA_URL": "https://10.0.0.5"}, "grafana url"),
        ({**GF_GOOD, "GRAFANA_URL": "https://grafana.internal"}, "grafana url"),
        ({**GF_GOOD, "GRAFANA_URL": "https://localhost"}, "grafana url"),
    ],
)
def test_grafana_bad_settings_fail_by_name(env: dict[str, str], name: str) -> None:
    result = checks("grafana", env)
    assert [c.name for c in result if c.required and not c.ok] == [name]
    assert GF_TOKEN not in render(result)


def test_a_self_hosted_grafana_passes_with_a_hint_for_allow_host() -> None:
    result = checks("grafana", {**GF_GOOD, "GRAFANA_URL": "https://grafana.example.com"})
    assert passed(result)
    (url,) = [c for c in result if c.name == "grafana url"]
    assert "--allow-host grafana.example.com" in url.detail


def test_no_network_call_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("doctor must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert passed(checks("grafana", GF_GOOD)) and passed(checks("dynatrace-classic", DT_GOOD))
