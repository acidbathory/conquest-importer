"""The real Dynatrace getter and extractor, driven by an in-memory HTTP transport."""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace import extract as dx
from importer.sources.dynatrace.extract import DynatraceError, extract

HOST = "https://abc12345.apps.dynatrace.com"
TOKEN = "dt0s16.AAAAAAAA.SECRETSECRETSECRET"  # noqa: S105 - a made-up value for leak tests
WORKFLOW: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/dynatrace/workflow-manual-trigger.json").read_text()
)
SLO: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/dynatrace/slo-second.json").read_text()
)
UUIDS = [f"00000000-0000-0000-0000-00000000000{i}" for i in range(1, 6)]

Handler = Callable[[httpx.Request], httpx.Response]


def getter(handler: Handler, token: str = TOKEN) -> AbstractContextManager[dx.Getter]:
    return dx.http_getter(token, transport=httpx.MockTransport(handler))


def serve(
    workflows: Any = None, slos: Any = None, detail: Any = None, slo_status: int = 200
) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == dx.WORKFLOWS_PATH:
            body = {"count": len(workflows), "results": workflows}
            return httpx.Response(200, json=body)
        if path.startswith(dx.WORKFLOWS_PATH + "/"):
            return httpx.Response(200, json=detail)
        if slo_status != 200:
            return httpx.Response(slo_status, json={"error": {"message": "no scope " + TOKEN}})
        return httpx.Response(200, json={"slos": slos or []})

    return handler


def read(handler: Handler, tmp_path: Path, token: str = TOKEN) -> dx.ExtractResult:
    with getter(handler, token) as get:
        return extract(get, HOST, tmp_path / "out")


def test_the_real_getter_reads_both_lists_and_sends_a_bearer_header(tmp_path: Path) -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return serve([WORKFLOW], [SLO])(request)

    result = read(handler, tmp_path)
    assert (result.workflows, result.slos, result.slos_read) == (1, 1, True)
    assert all(r.method == "GET" for r in seen)
    assert seen[0].headers["authorization"] == f"Bearer {TOKEN}"


def test_the_client_is_really_closed_when_the_block_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    made: list[httpx.Client] = []

    class Recording(httpx.Client):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            made.append(self)

    monkeypatch.setattr(httpx, "Client", Recording)
    with dx.http_getter("token"):
        assert not made[0].is_closed
    assert made[0].is_closed
    with pytest.raises(RuntimeError), dx.http_getter("token"):
        raise RuntimeError("boom")
    assert made[1].is_closed


@pytest.mark.parametrize("body", [["a", "list"], "text", 5, None, {"error": ["x"]}])
def test_an_error_body_of_any_shape_gives_a_clean_error(body: Any, tmp_path: Path) -> None:
    with pytest.raises(DynatraceError, match="returned 500"):
        read(lambda _r: httpx.Response(500, json=body), tmp_path)


def test_a_401_that_echoes_the_token_does_not_leak_it(tmp_path: Path) -> None:
    echo = f"bad token {TOKEN}; header was Authorization: Bearer {TOKEN} and dt0s16.XYZ.LEAKED"
    with pytest.raises(DynatraceError) as err:
        read(lambda _r: httpx.Response(401, json={"error": {"message": echo}}), tmp_path)
    text = str(err.value)
    assert "returned 401" in text
    for part in ("SECRETSECRET", "AAAAAAAA", "LEAKED", TOKEN):
        assert part not in text


def test_a_long_server_message_is_capped(tmp_path: Path) -> None:
    body = {"error": {"message": "x" * 5000}}
    with pytest.raises(DynatraceError) as err:
        read(lambda _r: httpx.Response(400, json=body), tmp_path)
    assert len(str(err.value)) < 400


@pytest.mark.parametrize("status", [301, 302, 307])
def test_a_redirect_is_an_error_and_is_not_followed(status: int, tmp_path: Path) -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(status, headers={"location": "https://evil.example/x"})

    with pytest.raises(DynatraceError, match=f"returned {status}"):
        read(handler, tmp_path)
    assert calls == [calls[0]] and "evil" not in calls[0]


def test_a_body_that_is_not_json_is_an_error(tmp_path: Path) -> None:
    with pytest.raises(DynatraceError, match="not JSON"):
        read(lambda _r: httpx.Response(200, content=b"<html>"), tmp_path)


def test_a_declared_oversize_response_is_refused(tmp_path: Path) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": str(dx.MAX_RESPONSE_BYTES + 1)})

    with pytest.raises(DynatraceError, match="larger than"):
        read(handler, tmp_path)


def test_an_oversize_body_without_a_length_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_RESPONSE_BYTES", 50)
    with pytest.raises(DynatraceError, match="larger than"):
        read(lambda _r: httpx.Response(200, json={"results": ["x" * 100]}), tmp_path)


def test_a_connection_failure_names_only_the_error_type(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"cannot reach {request.url} with {TOKEN}")

    with pytest.raises(DynatraceError) as err:
        read(handler, tmp_path)
    assert str(err.value) == "could not reach Dynatrace: ConnectError"


@pytest.mark.parametrize("detail", [["not", "a", "dict"], {"no": "tasks"}, "text"])
def test_a_detail_response_that_is_not_a_workflow_is_dropped_with_a_warning(
    detail: Any, tmp_path: Path
) -> None:
    result = read(serve([{"id": UUIDS[0], "title": "s"}, WORKFLOW], [], detail), tmp_path)
    assert result.workflows == 1 and any("left out" in w for w in result.warnings)
    written = json.loads((tmp_path / "out/workflows/workflows.json").read_text())
    assert [w.get("title") for w in written] == [WORKFLOW["title"]]


def test_an_entry_with_a_bad_id_and_no_tasks_is_dropped_and_not_fetched(tmp_path: Path) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return serve([{"id": "../../etc", "title": "x"}, {"title": "no id"}], [])(request)

    result = read(handler, tmp_path)
    assert result.workflows == 0 and result.warnings
    assert all(not p.startswith(dx.WORKFLOWS_PATH + "/") for p in seen)


def test_list_entries_with_a_repeated_id_are_kept_once(tmp_path: Path) -> None:
    item = {**WORKFLOW, "id": UUIDS[0]}
    assert read(serve([item, item, {**item, "id": UUIDS[1]}], []), tmp_path).workflows == 2


def test_the_detail_reads_are_capped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dx, "MAX_DETAIL_FETCHES", 1)
    fetched: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith(dx.WORKFLOWS_PATH + "/"):
            fetched.append(request.url.path)
        return serve([{"id": u, "title": "s"} for u in UUIDS[:3]], [], WORKFLOW)(request)

    result = read(handler, tmp_path)
    assert len(fetched) == 1 and result.workflows == 1
    assert any("2 workflows were left out" in w for w in result.warnings)


def test_the_detail_reads_have_a_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(dx, "DEADLINE_SECONDS", -1)
    result = read(serve([{"id": u, "title": "s"} for u in UUIDS[:2]], [], WORKFLOW), tmp_path)
    assert result.workflows == 0 and any("2 workflows were left out" in w for w in result.warnings)


def test_the_workflow_cap_stops_a_runaway_listing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_WORKFLOWS", 1)
    with pytest.raises(DynatraceError, match="more than 1 workflows"):
        read(serve([WORKFLOW, WORKFLOW], []), tmp_path)


@pytest.mark.parametrize("status", [401, 403, 500])
def test_an_slo_failure_is_reported_as_not_read(status: int, tmp_path: Path) -> None:
    result = read(serve([WORKFLOW], slo_status=status), tmp_path)
    assert result.workflows == 1 and result.slos_read is False
    assert any("SLOs were NOT read" in w for w in result.warnings)
    assert all("SECRETSECRET" not in w for w in result.warnings)
    assert not (tmp_path / "out/slos").exists()


def run_cli(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, handler: Handler, token: str = TOKEN
) -> Any:
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", token)
    monkeypatch.setattr(
        "importer.cli.dynatrace_getter", lambda t: dx.http_getter(t, httpx.MockTransport(handler))
    )
    return CliRunner().invoke(app, ["extract-dynatrace", "--out", str(tmp_path / "o")])


def test_the_cli_says_when_slos_were_not_read_and_still_exits_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve([WORKFLOW], slo_status=403))
    assert out.exit_code == 0, out.output
    assert "SLOs were NOT read" in out.output and "0 SLOs" not in out.output
    assert "SECRETSECRET" not in out.output


def test_the_cli_fails_and_hides_the_token_when_workflows_cannot_be_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": {"message": f"invalid {TOKEN}"}})

    out = run_cli(monkeypatch, tmp_path, handler)
    assert out.exit_code == 1 and "returned 401" in out.output
    assert "SECRETSECRET" not in out.output and TOKEN not in out.output


@pytest.mark.parametrize(
    "token", ["dt0s16.éé.SECRET", "dt0s16.ab cd", "dt0s16.a" + chr(0x2013) + "b"]
)
def test_a_token_with_odd_characters_is_refused_without_echoing_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, token: str
) -> None:
    out = run_cli(monkeypatch, tmp_path, serve([], []), token)
    assert out.exit_code == 1 and "plain ASCII" in out.output
    for part in ("dt0s16", "é", "SECRET", chr(0x2013), "cd"):
        assert part not in out.output


def test_the_documented_sequence_runs_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scheduled = json.loads(
        (Path(__file__).parent / "fixtures/dynatrace/workflow-scheduled-dql-check.json").read_text()
    )
    out = run_cli(monkeypatch, tmp_path, serve([scheduled, WORKFLOW], [SLO]))
    assert out.exit_code == 0, out.output
    folder = tmp_path / "o"
    assert (
        f"importer convert --source dynatrace --in {folder} --out {folder / 'alerts'}" in out.output
    )
    assert f"importer report {folder} --source dynatrace" in out.output
    runner = CliRunner()
    convert = runner.invoke(
        app,
        ["convert", "--source", "dynatrace", "--in", str(folder), "--out", str(folder / "alerts")],
    )
    assert convert.exit_code == 0 and "Read 3 monitors, emitted 1" in convert.output
    report = runner.invoke(app, ["report", str(folder), "--source", "dynatrace"])
    assert report.exit_code == 0 and "1 of 3 assets" in report.output
    again = runner.invoke(
        app,
        ["convert", "--source", "dynatrace", "--in", str(folder), "--out", str(folder / "alerts")],
    )
    assert again.exit_code == 0 and "Read 3 monitors" in again.output
