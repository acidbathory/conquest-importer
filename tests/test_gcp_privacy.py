"""Names that look like addresses, URLs or tokens never reach an export, a plan or a report."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.redact import safe_name
from importer.sources.gcp.extract import extract
from importer.sources.gcp.uptime import plan_check

FIXTURES = Path(__file__).parent / "fixtures"
P = "projects/demo-project-123/"
HOSTILE_NAMES = [
    "https://hooks.example/h?token=SECRET123",
    "bob@corp.example",
    "https://user:pw@host.example/",
    "key=SECRET777",
    "deadbeefdeadbeefdeadbeef0123456789",
    "AbCdEf0123456789AbCdEf0123456789xyz",
]
HOSTILE_HANDLE = "https://evil.example/x?k=SECRETH"
PATHS = ["/health?api_key=SECRET999&x=1", "/hook#SECRETFRAG", "/a/AbCdEf0123456789AbCdEf0123456789"]
LEAKS = [
    "SECRET123",
    "corp.example",
    "bob",
    "pw@",
    "hooks.example",
    "SECRET777",
    "deadbeefdeadbeef",
    "AbCdEf0123456789",
    "SECRETH",
    "evil.example",
    "SECRET999",
    "SECRETFRAG",
    "host.example",
]


def channels() -> list[dict[str, Any]]:
    return [
        {"name": f"{P}notificationChannels/{i}", "type": "email", "displayName": n, "enabled": True}
        for i, n in enumerate(HOSTILE_NAMES, start=1)
    ]


def uptime() -> list[dict[str, Any]]:
    return [
        {
            "name": f"{P}uptimeCheckConfigs/{i}",
            "displayName": n,
            "period": "60s",
            "kind": "http",
            "path": p,
            "monitoredResource": "uptime_url",
        }
        for i, (n, p) in enumerate(zip(HOSTILE_NAMES, PATHS * 2, strict=False), start=1)
    ]


def everything(root: Path, *outputs: str) -> str:
    texts = [p.read_text() for p in root.rglob("*") if p.is_file()]
    return "".join(texts) + "".join(outputs)


def policy(handles: list[str]) -> dict[str, Any]:
    fixture = json.loads((FIXTURES / "gcp-notifications/policies/policies.json").read_text())
    return {**fixture[0], "notificationChannels": handles}


def test_hostile_channel_and_check_names_appear_nowhere(tmp_path: Path) -> None:
    chans = tmp_path / "channels"
    chans.mkdir()
    (chans / "channels.json").write_text(json.dumps(channels()))
    pols = tmp_path / "policies"
    pols.mkdir()
    handles = [c["name"] for c in channels()] + [HOSTILE_HANDLE]
    (pols / "policies.json").write_text(json.dumps([policy(handles)]))
    ups = tmp_path / "up"
    ups.mkdir()
    (ups / "uptime-checks.json").write_text(json.dumps(uptime()))
    runner = CliRunner()
    alerts = tmp_path / "alerts"
    done = [
        runner.invoke(app, ["convert", "--source", "gcp", "--in", str(pols), "--out", str(alerts)])
    ]
    done.append(
        runner.invoke(
            app,
            [
                "convert-gcp-notifications",
                *["--channels", str(chans), "--alerts", str(alerts)],
                *["--out", str(tmp_path / "notes")],
            ],
        )
    )
    done.append(
        runner.invoke(
            app, ["convert-gcp-uptime", "--checks", str(ups), "--out", str(tmp_path / "upout")]
        )
    )
    assert [d.exit_code for d in done] == [0, 0, 0]
    text = everything(tmp_path / "notes", *(d.output for d in done)) + everything(
        tmp_path / "upout"
    )
    for leak in LEAKS:
        assert leak not in text, leak


class Api:
    def __call__(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        if url.endswith("notificationChannels"):
            return {"notificationChannels": channels()}
        if url.endswith("uptimeCheckConfigs"):
            return {"uptimeCheckConfigs": uptime()}
        return {}


def test_the_extractor_redacts_names_before_writing(tmp_path: Path) -> None:
    extract(Api(), "my-project", tmp_path / "out")
    text = everything(tmp_path / "out")
    for leak in LEAKS:
        assert leak not in text, leak
    shown = json.loads((tmp_path / "out/channels/channels.json").read_text())
    assert shown[0]["displayName"] == "1"  # the resource id tail stands in for the name


@pytest.mark.parametrize("name", HOSTILE_NAMES)
def test_safe_name_replaces_anything_that_looks_like_an_address_or_token(name: str) -> None:
    assert safe_name(name, "fallback-1") == "fallback-1"


@pytest.mark.parametrize("name", ["Ops mailbox", "#alerts", "Primary pager", "prod-db-primary"])
def test_safe_name_keeps_ordinary_names(name: str) -> None:
    assert safe_name(name, "x") == name


@pytest.mark.parametrize(
    ("path", "kept"),
    [
        ("/health?api_key=SECRET999&x=1", "/health"),
        ("/hook#SECRETFRAG", "/hook"),
        ("/a/AbCdEf0123456789AbCdEf0123456789", "/a/***"),
        ("/plain/path", "/plain/path"),
    ],
)
def test_uptime_paths_lose_query_fragment_and_token_segments(path: str, kept: str) -> None:
    got = plan_check({"kind": "http", "period": "60s", "displayName": "x", "path": path}).path
    assert got == kept
