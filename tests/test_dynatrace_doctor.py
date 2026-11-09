"""`doctor --source dynatrace` checks the settings offline and never shows the token."""

import socket
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.doctor import passed, render, run_checks

TOKEN = "dt0s16.ABCDEFGH.secretsecret"  # noqa: S105 - fake
GOOD = {"DYNATRACE_API_TOKEN": TOKEN, "DYNATRACE_ENV_URL": "https://abc12345.apps.dynatrace.com"}


def checks(env: dict[str, str]):  # type: ignore[no-untyped-def]
    return run_checks("us-east-1", None, which=lambda _: None, env=env, source="dynatrace")


def test_good_settings_pass_and_the_token_is_not_shown() -> None:
    result = checks(GOOD)
    assert passed(result)
    text = render(result)
    assert TOKEN not in text and "secretsecret" not in text
    assert "dynatrace token" in text and "dynatrace url" in text
    assert "aws keys" not in text and "google login" not in text


@pytest.mark.parametrize(
    ("env", "name"),
    [
        ({"DYNATRACE_ENV_URL": GOOD["DYNATRACE_ENV_URL"]}, "dynatrace token"),
        ({**GOOD, "DYNATRACE_API_TOKEN": "   "}, "dynatrace token"),
        ({**GOOD, "DYNATRACE_API_TOKEN": "dt0s16.“abc”"}, "dynatrace token"),
        ({**GOOD, "DYNATRACE_API_TOKEN": "dt0s16.ab cd"}, "dynatrace token"),
        ({**GOOD, "DYNATRACE_API_TOKEN": "dt0s16.ab\ncd"}, "dynatrace token"),
        ({"DYNATRACE_API_TOKEN": TOKEN}, "dynatrace url"),
        ({**GOOD, "DYNATRACE_ENV_URL": "http://abc12345.apps.dynatrace.com"}, "dynatrace url"),
        ({**GOOD, "DYNATRACE_ENV_URL": "https://abc12345.live.dynatrace.com"}, "dynatrace url"),
        ({**GOOD, "DYNATRACE_ENV_URL": "https://abc12345.apps.dynatrace.com/ui"}, "dynatrace url"),
        ({**GOOD, "DYNATRACE_ENV_URL": "https://evil.example.com"}, "dynatrace url"),
        (
            {**GOOD, "DYNATRACE_ENV_URL": "https://abc.apps.dynatrace.com.evil.example.com"},
            "dynatrace url",
        ),
    ],
)
def test_a_bad_setting_fails_by_name(env: dict[str, str], name: str) -> None:
    result = checks(env)
    assert not passed(result)
    failed = [c.name for c in result if not c.ok and c.required]
    assert failed == [name]
    assert TOKEN not in render(result)


def test_a_trailing_slash_on_the_url_is_accepted() -> None:
    assert passed(checks({**GOOD, "DYNATRACE_ENV_URL": GOOD["DYNATRACE_ENV_URL"] + "/"}))


def test_the_certificate_file_is_a_warning_with_a_hint(tmp_path: Path) -> None:
    unset = checks(GOOD)
    (cert,) = [c for c in unset if c.name == "tls certificates"]
    assert not cert.ok and not cert.required and "SSL_CERT_FILE" in cert.detail
    assert passed(unset)
    bundle = tmp_path / "ca.pem"
    bundle.write_text("x")
    ok = checks({**GOOD, "SSL_CERT_FILE": str(bundle)})
    assert [c.ok for c in ok if c.name == "tls certificates"] == [True]
    missing = checks({**GOOD, "SSL_CERT_FILE": str(tmp_path / "nope.pem")})
    assert [c.ok for c in missing if c.name == "tls certificates"] == [False] and passed(missing)


def test_no_network_call_is_made(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_a: object, **_k: object) -> None:
        raise AssertionError("doctor must not use the network")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    monkeypatch.setattr(socket, "getaddrinfo", refuse)
    assert passed(checks(GOOD))


def test_the_command_exits_by_the_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    for key, value in GOOD.items():
        monkeypatch.setenv(key, value)
    ok = CliRunner().invoke(app, ["doctor", "--source", "dynatrace"])
    assert ok.exit_code == 0 and "[ok  ] dynatrace token" in ok.output
    assert TOKEN not in ok.output
    monkeypatch.delenv("DYNATRACE_API_TOKEN")
    bad = CliRunner().invoke(app, ["doctor", "--source", "dynatrace"])
    assert bad.exit_code == 1 and "[FAIL] dynatrace token" in bad.output


def test_the_command_rejects_an_unknown_source() -> None:
    res = CliRunner().invoke(app, ["doctor", "--source", "datadog"])
    assert res.exit_code == 1 and "dynatrace" in res.output
