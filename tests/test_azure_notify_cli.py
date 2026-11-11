"""convert-azure-notifications end to end on the Azure fixtures: plans, files, leaks, apply."""

import json
import shutil
import stat
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from hcl_schema import resource_count, schema_errors, undeclared_variables
from importer.cli import app
from importer.core.notification_ir import NotificationPlan
from importer.sources.azure.notifications.link import ACTION_GROUP_TAG, action_group_key
from test_azure_notify_rules import monitor

FIX = Path(__file__).parent / "fixtures" / "azure"
SAMPLE = (
    "/subscriptions/187f412d-1758-44d9-b052-169e2564721d/resourceGroups/Default-NotificationRules"
    "/providers/microsoft.insights/actionGroups/SampleActionGroup"
)
SYNTH = (
    "/subscriptions/00000000-0000-0000-0000-000000000000/resourceGroups/rg-synthetic"
    "/providers/Microsoft.Insights/actionGroups/ag-synthetic"
)
LEAKS = (
    "SYNTHETIC-PASSWORD",
    "SYNTHETIC-TOKEN",
    "SYNTHETIC-SIGNATURE",
    "SYNTHETIC-FUNCTION-KEY",
    "1234567890",
    "0987654321",
    "sig=",
)
runner = CliRunner()


def alerts_folder(root: Path) -> Path:
    ir = root / "alerts" / "ir"
    ir.mkdir(parents=True)
    pairs = [
        (1, "cpu high", SAMPLE),
        (2, "disk full", SAMPLE),
        (3, "queue deep", SYNTH),
        (4, "no group", ""),
        (
            5,
            "ghost group",
            "/subscriptions/s/resourceGroups/r/providers/microsoft.insights/actionGroups/Gone",
        ),
    ]
    for n, name, group in pairs:
        m = monitor(n, name)
        if group:
            m.notification.handles = [group]
            m.tags = [f"{ACTION_GROUP_TAG}:{action_group_key(group)}"]
        (ir / f"{n:04d}.json").write_text(m.model_dump_json())
    return root / "alerts"


@pytest.fixture(scope="module")
def export(tmp_path_factory: pytest.TempPathFactory) -> Path:
    folder = tmp_path_factory.mktemp("azure-export")
    shutil.copy(
        FIX / "action_groups" / "2023-01-01__createOrUpdateActionGroup.json", folder / "g1.json"
    )
    shutil.copy(
        FIX / "synthetic" / "SYNTHETIC__action_group_secrets_in_uris.json", folder / "g2.json"
    )
    shutil.copy(
        FIX / "synthetic" / "SYNTHETIC__action_group_unicode_and_empty.json", folder / "g3.json"
    )
    shutil.copytree(FIX / "alert_processing_rules", folder / "rules")
    shutil.copy(
        FIX / "synthetic" / "SYNTHETIC__alert_processing_rule_unknown_action.json",
        folder / "r2.json",
    )
    return folder


@pytest.fixture(scope="module")
def converted(tmp_path_factory: pytest.TempPathFactory, export: Path) -> Path:
    root = tmp_path_factory.mktemp("run")
    out = root / "plans"
    done = runner.invoke(
        app,
        [
            "convert-azure-notifications",
            "--in",
            str(export),
            "--alerts",
            str(alerts_folder(root)),
            "--out",
            str(out),
        ],
    )
    assert done.exit_code == 0, done.output
    return out


def text_of(out: Path, *names: str) -> str:
    files = [p for p in out.rglob("*") if p.is_file() and (not names or p.name in names)]
    return "\n".join(p.read_text() for p in files)


def test_one_plan_per_referenced_action_group_and_every_file_is_private(converted: Path) -> None:
    plans = sorted((converted / "notification-plans").glob("*.json"))
    assert len(plans) == 2  # the sample group and the synthetic one; Gone is not exported
    for path in converted.rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path.name
    loaded = [NotificationPlan.model_validate_json(p.read_text()) for p in plans]
    assert all(p.workflow is not None and p.needs_review for p in loaded)
    sample = next(p for p in loaded if p.topic == "SampleActionGroup")
    assert sample.workflow is not None
    assert sample.workflow.condition_names == ["cpu high", "disk full"]
    (predicate,) = sample.workflow.predicates
    assert predicate.attribute == "accumulations.tag.azure.actionGroup"
    assert predicate.values == [action_group_key(SAMPLE)]
    assert sample.workflow.muting_rules_handling == "DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES"
    assert len(sample.manual_destinations) >= 6 and sample.typed_destinations


def test_no_secret_phone_number_or_token_reaches_any_file(converted: Path) -> None:
    everything = text_of(converted)
    for leak in LEAKS:
        assert leak not in everything, leak


def test_email_addresses_are_only_in_the_plans_and_the_terraform(converted: Path) -> None:
    assert "johndoe@email.com" in text_of(
        converted, "topic-0001.json", "topic-0002.json", "notifications.tf"
    )
    for name in ("report.md", "secrets.tfvars.example"):
        assert "@" not in text_of(converted, name) or "example" in name, name
    assert "@email.com" not in (converted / "report.md").read_text()


def test_the_terraform_fits_the_provider_schema_and_declares_every_secret(converted: Path) -> None:
    tf = (converted / "notifications.tf").read_text()
    assert schema_errors(tf) == []
    assert undeclared_variables(tf) == []
    assert resource_count(tf) >= 8
    assert "sensitive = true" in tf and "var.nr_secret_azure_" in tf
    assert 'resource "newrelic_alert_muting_rule"' in tf
    example = (converted / "secrets.tfvars.example").read_text()
    assert example.count("nr_secret_azure_") >= 4
    assert "REPLACE_WITH_THE_SECRET" in example and "sig" not in example


def test_report_has_the_pair_header_the_counts_and_the_manual_methods(converted: Path) -> None:
    report = (converted / "report.md").read_text()
    assert report.startswith("# Azure notification conversion: Azure Monitor to New Relic")
    assert "## Receivers by type" in report and "| SMS |" in report and "| Event Hub |" in report
    assert "PagerDuty" in report and "Opsgenie" in report
    assert "adds: " in report  # the AddActionGroups rules name the groups they add
    assert "s/r/gone" in report  # the group no export holds is listed
    assert "no group" in report  # a converted condition with no action group


def test_apply_notifications_reads_the_folder_in_a_dry_run(converted: Path) -> None:
    done = runner.invoke(app, ["apply-notifications", "--in", str(converted), "--account-id", "1"])
    assert done.exit_code == 0, done.output
    assert "2 workflows to create (disabled)" in done.output and "DRY RUN" in done.output


def test_route_by_name_filters_on_condition_names(tmp_path: Path, export: Path) -> None:
    out = tmp_path / "plans"
    done = runner.invoke(
        app,
        [
            "convert-azure-notifications",
            "--in",
            str(export),
            "--alerts",
            str(alerts_folder(tmp_path)),
            "--out",
            str(out),
            "--route-by",
            "name",
        ],
    )
    assert done.exit_code == 0, done.output
    plan = NotificationPlan.model_validate_json(
        sorted((out / "notification-plans").glob("*.json"))[0].read_text()
    )
    assert plan.workflow is not None and plan.workflow.predicates == []


def test_errors_are_plain_and_exit_one(tmp_path: Path, export: Path) -> None:
    alerts = alerts_folder(tmp_path)

    def run(*args: str) -> tuple[int, str]:
        done = runner.invoke(app, ["convert-azure-notifications", *args])
        return done.exit_code, done.output

    code, _ = run(
        "--in", str(export), "--alerts", str(tmp_path / "none"), "--out", str(tmp_path / "o1")
    )
    assert code == 1
    code, _ = run(
        "--in", str(tmp_path / "missing"), "--alerts", str(alerts), "--out", str(tmp_path / "o2")
    )
    assert code == 1
    code, out = run(
        "--in",
        str(export),
        "--alerts",
        str(alerts),
        "--out",
        str(tmp_path / "o3"),
        "--route-by",
        "x",
    )
    assert code == 1 and "--route-by" in out
    ok = ["--in", str(export), "--alerts", str(alerts), "--out", str(tmp_path / "o4")]
    assert run(*ok)[0] == 0
    assert run(*ok)[0] == 1  # never overwrites


def test_help_documents_the_options(help_text: Callable[..., str]) -> None:
    text = help_text("convert-azure-notifications", "--help")
    for word in ("--in", "--alerts", "--out", "--route-by", "--prefix"):
        assert word in text


def test_the_command_is_listed_among_the_commands(help_text: Callable[..., str]) -> None:
    assert "convert-azure-notifications" in help_text("--help")


def test_a_broken_ir_file_does_not_echo_its_contents(tmp_path: Path, export: Path) -> None:
    alerts = alerts_folder(tmp_path)
    (alerts / "ir" / "9999.json").write_text('{"name": "TOPSECRETTEXT", "source_id": "x"}')
    done = runner.invoke(
        app,
        [
            "convert-azure-notifications",
            "--in",
            str(export),
            "--alerts",
            str(alerts),
            "--out",
            str(tmp_path / "o"),
        ],
    )
    assert done.exit_code == 1 and "TOPSECRETTEXT" not in json.dumps(done.output)
