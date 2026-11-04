"""The short path: settings from .env, accounts from the environment, report opened, one prompt."""

import json
import webbrowser
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer import cli
from importer.cli import app
from importer.core.dotenv import load_dotenv_file
from importer.core.manifest import CreatedObject, Manifest, save_manifest
from importer.core.rollback import RollbackResult
from importer.sources.cloudwatch import extract as ex

FIXTURES = Path(__file__).parent / "fixtures"
DASH = json.loads(
    sorted((FIXTURES / "cloudwatch-dashboards").glob("conquest-ops*.json"))[0].read_text()
)
ACCOUNT = "123456789012"


def test_dotenv_sets_only_what_is_missing_and_ignores_noise(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("KEEP_ME", "from-environment")
    for name in ("NEW_ONE", "QUOTED", "EXPORTED"):
        monkeypatch.delenv(name, raising=False)
    env = tmp_path / ".env"
    env.write_text(
        "# a comment\n\nNEW_ONE=1\nKEEP_ME=from-file\nQUOTED=\"two words\"\nexport EXPORTED='x'\n"
        "not a setting\n"
    )
    names = load_dotenv_file(env)
    import os

    assert sorted(names) == ["EXPORTED", "NEW_ONE", "QUOTED"]
    assert os.environ["KEEP_ME"] == "from-environment" and os.environ["QUOTED"] == "two words"
    for name in names:
        monkeypatch.delenv(name)


def test_a_missing_dotenv_is_fine(tmp_path: Path) -> None:
    assert load_dotenv_file(tmp_path / "nope.env") == []


class DashboardsOnly:
    def __call__(
        self, service: str, op: str, region: str, profile: str | None, params: dict[str, Any]
    ) -> dict[str, Any]:
        answers: dict[str, dict[str, Any]] = {
            "sts get_caller_identity": {"Account": ACCOUNT, "Arn": "arn:aws:iam::x:user/y"},
            "cloudwatch describe_alarms": {"MetricAlarms": [], "CompositeAlarms": []},
            "cloudwatch list_dashboards": {
                "DashboardEntries": [{"DashboardName": DASH["DashboardName"]}]
            },
            "cloudwatch get_dashboard": DASH,
        }
        return answers.get(f"{service} {op}", {})


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    created: list[dict[str, Any]] = []
    monkeypatch.setattr(ex, "run_boto3", DashboardsOnly())
    monkeypatch.setattr(cli, "apply_dashboards_command", lambda **kw: created.append(kw))
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    return created


def test_with_nothing_but_the_environment_it_asks_about_the_account_and_then_creates(
    world: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    opened: list[str] = []
    monkeypatch.setattr(cli, "_interactive", lambda: True)

    def record(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(webbrowser, "open", record)
    res = CliRunner().invoke(app, ["migrate", "--out", str(tmp_path / "run")], input="y\ny\n")
    assert res.exit_code == 0, res.output
    assert f"signed in to AWS account {ACCOUNT}" in res.output
    assert len(opened) == 1 and opened[0].endswith("report.html")
    assert len(world) == 1 and world[0]["account_id"] == 7


def test_declining_the_account_stops_before_reading_anything_else(
    world: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    res = CliRunner().invoke(app, ["migrate", "--out", str(tmp_path / "run")], input="n\n")
    assert res.exit_code == 1 and not world and not (tmp_path / "run").exists()


def test_a_script_must_name_the_aws_account(world: list[dict[str, Any]], tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["migrate", "--out", str(tmp_path / "run")])
    assert res.exit_code == 1 and "--aws-account-id" in res.output and not world


def test_the_new_relic_account_has_to_come_from_somewhere(
    world: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("NEW_RELIC_ACCOUNT_ID")
    res = CliRunner().invoke(app, ["migrate", "--aws-account-id", ACCOUNT])
    assert res.exit_code == 1 and "NEW_RELIC_ACCOUNT_ID" in res.output


def test_the_run_folder_is_dated_and_remembered(
    world: list[dict[str, Any]], tmp_path: Path
) -> None:
    res = CliRunner().invoke(app, ["migrate", "--aws-account-id", ACCOUNT])
    assert res.exit_code == 0, res.output
    remembered = Path(cli.LATEST.read_text().strip())
    assert remembered.name.startswith("migration-") and (remembered / "report.html").exists()
    import shutil

    shutil.rmtree(remembered)


def test_rollback_without_arguments_undoes_the_latest_run_after_asking(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    run = tmp_path / "run"
    save_manifest(
        Manifest(account_id=7, objects=[CreatedObject(kind="condition", id="1", name="c")]),
        run / "manifest.json",
    )
    cli.LATEST.write_text(str(run))
    seen: list[bool] = []

    def fake(_client: Any, _account: int, record: Manifest, *, apply: bool) -> RollbackResult:
        seen.append(apply)
        return RollbackResult(deleted=record.objects)

    monkeypatch.setattr(cli, "rollback", fake)
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    monkeypatch.setenv("NEW_RELIC_API_KEY", "k")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "7")
    res = CliRunner().invoke(app, ["rollback"], input="y\n")
    assert res.exit_code == 0, res.output
    assert "Using the latest run" in res.output and seen == [False, True]


def test_rollback_says_when_there_is_no_recent_run() -> None:
    res = CliRunner().invoke(app, ["rollback"])
    assert res.exit_code == 1 and "no recent run" in res.output
