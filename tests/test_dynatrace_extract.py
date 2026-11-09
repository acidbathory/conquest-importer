"""The Dynatrace extractor reads only allow-listed lists, checks the host, and bounds its paging."""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace import extract as dx
from importer.sources.dynatrace.extract import DynatraceError, extract
from importer.sources.dynatrace.models import load_exports, load_slos, load_workflows

HOST = "https://abc12345.apps.dynatrace.com"
WORKFLOW: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/dynatrace/workflow-manual-trigger.json").read_text()
)
SLO: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures/dynatrace/slo-second.json").read_text()
)


class Fake:
    """A fake Dynatrace API: two pages of workflows and two pages of SLOs."""

    def __init__(self, fail: str = "", summary_only: bool = False) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail = fail
        self.summary_only = summary_only

    def __call__(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, params))
        if self.fail and self.fail in url:
            raise DynatraceError("403 denied")
        if url.endswith("/workflows"):
            offset = int(params.get("offset", "0"))
            uid = f"00000000-0000-0000-0000-00000000000{offset}"
            item = {"id": uid, "title": "summary"} if self.summary_only else {**WORKFLOW, "id": uid}
            return {"count": 2, "results": [item], "offset": offset}
        if "/workflows/" in url:
            return WORKFLOW
        if "page-key" not in params:
            return {"slos": [SLO], "nextPageKey": "k2"}
        return {"slos": [{**SLO, "name": "second", "id": "other"}]}


def test_reads_all_pages_and_writes_two_folders(tmp_path: Path) -> None:
    fake = Fake()
    result = extract(fake, HOST, tmp_path / "out")
    assert (result.workflows, result.slos) == (2, 2)
    assert len(load_workflows(tmp_path / "out" / "workflows")) == 2
    assert len(load_slos(tmp_path / "out" / "slos")) == 2
    assert len(load_exports(tmp_path / "out")) == 4


def test_only_the_allow_listed_get_endpoints_are_called(tmp_path: Path) -> None:
    fake = Fake()
    extract(fake, HOST, tmp_path / "out")
    paths = {url.removeprefix(HOST) for url, _ in fake.calls}
    assert paths == {"/platform/automation/v1/workflows", "/platform/slo/v1/slos"}


def test_paging_parameters(tmp_path: Path) -> None:
    fake = Fake()
    extract(fake, HOST, tmp_path / "out")
    workflow_params = [p for u, p in fake.calls if u.endswith("/workflows")]
    assert workflow_params[0] == {"limit": "100", "offset": "0"}
    slo_params = [p for u, p in fake.calls if u.endswith("/slos")]
    assert slo_params == [{"page-size": "100"}, {"page-key": "k2"}]


def test_a_list_without_task_details_is_completed_from_the_single_workflow(
    tmp_path: Path,
) -> None:
    fake = Fake(summary_only=True)
    extract(fake, HOST, tmp_path / "out")
    assert any(
        u == f"{HOST}/platform/automation/v1/workflows/00000000-0000-0000-0000-000000000001"
        for u, _ in fake.calls
    )
    assert len(load_workflows(tmp_path / "out" / "workflows")) == 2


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "http://abc12345.apps.dynatrace.com",
        "https://abc12345.apps.dynatrace.com.evil.example",
        "https://evil.example/abc12345.apps.dynatrace.com",
        "https://abc12345.live.dynatrace.com",
        "https://abc12345.apps.dynatrace.com/path",
        "https://user@abc12345.apps.dynatrace.com",
        "https://abc12345.apps.dynatrace.com:8443",
        "https://ABC12345.apps.dynatrace.com",
        "https://abc12345.apps.dynatrace.com\n",
    ],
)
def test_a_bad_host_is_refused_before_any_call(tmp_path: Path, bad: str) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="DYNATRACE_ENV_URL"):
        extract(fake, bad, tmp_path / "out")
    assert not fake.calls


def test_a_trailing_slash_is_accepted(tmp_path: Path) -> None:
    assert extract(Fake(), HOST + "/", tmp_path / "out").workflows == 2


def test_an_existing_folder_with_files_is_refused(tmp_path: Path) -> None:
    (tmp_path / "x.txt").write_text("hi")
    with pytest.raises(ValueError, match="new folder"):
        extract(Fake(), HOST, tmp_path)


def test_paging_is_bounded(tmp_path: Path) -> None:
    calls = 0

    def endless(url: str, params: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        if url.endswith("/slos"):
            return {"slos": [SLO], "nextPageKey": "again"}
        return {"count": 10**9, "results": [WORKFLOW] * 100}

    with pytest.raises(DynatraceError, match="more than"):
        extract(endless, HOST, tmp_path / "out")
    assert calls <= dx.MAX_PAGES + 1


def test_the_page_count_is_bounded_even_when_the_item_cap_is_not_reached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(dx, "MAX_WORKFLOWS", 10**9)
    calls = 0

    def endless(url: str, params: dict[str, str]) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"count": 10**9, "results": [WORKFLOW] * 100}

    with pytest.raises(DynatraceError, match="pages"):
        extract(endless, HOST, tmp_path / "out")
    assert calls == dx.MAX_PAGES


def test_failure_reading_workflows_stops_but_slos_only_warn(tmp_path: Path) -> None:
    with pytest.raises(DynatraceError):
        extract(Fake(fail="/workflows"), HOST, tmp_path / "a")
    result = extract(Fake(fail="/slos"), HOST, tmp_path / "b")
    assert result.workflows == 2 and result.slos == 0 and result.warnings


def test_the_token_comes_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DYNATRACE_API_TOKEN", raising=False)
    with pytest.raises(DynatraceError, match="DYNATRACE_API_TOKEN"):
        dx.api_token()
    monkeypatch.setenv("DYNATRACE_API_TOKEN", " dt0s16.abc ")
    assert dx.api_token() == "dt0s16.abc"


def test_cli_reports_a_missing_host_without_printing_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DYNATRACE_ENV_URL", raising=False)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "dt0s16.SECRETVALUE")
    result = CliRunner().invoke(app, ["extract-dynatrace", "--out", str(tmp_path / "o")])
    assert result.exit_code == 1 and "DYNATRACE_ENV_URL" in result.output
    assert "SECRETVALUE" not in result.output


def test_cli_reads_the_tenant_and_prints_next_steps(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DYNATRACE_ENV_URL", HOST)
    monkeypatch.setenv("DYNATRACE_API_TOKEN", "dt0s16.SECRETVALUE")

    @contextmanager
    def fake_getter(_token: str) -> Iterator[Fake]:
        yield Fake()

    monkeypatch.setattr("importer.cli.dynatrace_getter", fake_getter)
    out = tmp_path / "o"
    result = CliRunner().invoke(app, ["extract-dynatrace", "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert "2 workflows" in result.output and "convert --source dynatrace" in result.output
    assert "SECRETVALUE" not in result.output
    assert (out / "workflows").is_dir()


def test_cli_help_mentions_the_certificate_variable() -> None:
    result = CliRunner().invoke(app, ["extract-dynatrace", "--help"])
    assert "SSL_CERT_FILE" in " ".join(result.output.split())
