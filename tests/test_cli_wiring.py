"""CLI wiring of the notification emitters: review flag, violations, secrets, defaults."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer import cli
from importer.cli import app
from importer.core.apply import ApplyResult
from importer.core.emit.notifications import TerraformResult
from importer.core.notification_ir import (
    AuthKind,
    DestinationAuth,
    DestinationType,
    NotificationPlan,
    NrDestination,
    PlannedWorkflow,
    SecretRef,
    plan_json,
)

FIXTURES = Path(__file__).parent / "fixtures"
TOPIC = "arn:aws:sns:us-east-1:123456789012:ops-alerts"
SECRET_VALUE = "s3cret-value"  # noqa: S105


def pagerduty(secret: str) -> NrDestination:
    return NrDestination(
        type=DestinationType.PAGERDUTY_SERVICE_INTEGRATION,
        name="pd",
        auth=DestinationAuth(
            kind=AuthKind.TOKEN, prefix="Token token=", token=SecretRef(name=secret)
        ),
        channel_properties={"summary": "{{ issueTitle }}"},
    )


def typed_plan(topic: str = "t", *, review: bool = False) -> NotificationPlan:
    wf = PlannedWorkflow(name=f"wf {topic}", condition_names=["c"], destination_indexes=[0])
    return NotificationPlan(
        topic=topic,
        destinations=[],
        typed_destinations=[pagerduty("PD_KEY")],
        workflow=wf,
        needs_review=review,
    )


def write_plans(folder: Path, *plans: NotificationPlan) -> Path:
    (folder / "notification-plans").mkdir(parents=True)
    for n, plan in enumerate(plans, start=1):
        (folder / "notification-plans" / f"topic-{n:04d}.json").write_text(plan_json(plan))
    return folder


class _Session:
    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *_: object) -> None:
        return None


class Spy:
    """Stands in for core apply_notifications and the client around it."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.calls: list[dict[str, Any]] = []
        self.result = ApplyResult()
        monkeypatch.setattr(cli, "apply_notifications", self)
        monkeypatch.setattr(cli, "get_account_name", lambda *_: "test")
        monkeypatch.setattr(cli, "NewRelicClient", lambda *_: _Session())

    def __call__(self, _client: object, _account: int, plans: list[Any], **kw: Any) -> ApplyResult:
        self.calls.append({"plans": plans, **kw})
        return self.result


def apply_args(folder: Path, *extra: str) -> list[str]:
    return [
        "apply-notifications", "--in", str(folder), "--account-id", "7",
        "--apply", "--confirm-account", "7", *extra,
    ]  # fmt: skip


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch) -> Spy:
    monkeypatch.setenv("NEW_RELIC_API_KEY", "nrak-test-key-not-real")
    monkeypatch.setenv("NEW_RELIC_ALLOWED_ACCOUNT_IDS", "7")
    return Spy(monkeypatch)


def run(*args: str, env: dict[str, str] | None = None) -> Any:
    return CliRunner().invoke(app, list(args), env=env)


@pytest.mark.parametrize("flag", [False, True])
def test_the_review_flag_reaches_core(live: Spy, tmp_path: Path, flag: bool) -> None:
    folder = write_plans(tmp_path, typed_plan(review=True))
    extra = ["--include-needs-review"] if flag else []
    res = run(*apply_args(folder, *extra), env={"PD_KEY": SECRET_VALUE})
    assert res.exit_code == 0, res.output
    assert live.calls[0]["include_needs_review"] is flag


def test_skipped_review_plans_are_listed(live: Spy, tmp_path: Path) -> None:
    folder = write_plans(tmp_path, typed_plan(review=True))
    live.result.skipped_review.append("wf t")
    res = run(*apply_args(folder), env={"PD_KEY": SECRET_VALUE})
    assert res.exit_code == 0, res.output
    assert "Skipped 1 plans that need review" in res.output and "wf t" in res.output


def test_plan_violations_are_printed(live: Spy, tmp_path: Path) -> None:
    folder = write_plans(tmp_path, typed_plan("same"), typed_plan("same"))
    res = run(*apply_args(folder), env={"PD_KEY": SECRET_VALUE})
    assert "same topic" in res.output


def test_secrets_come_from_variables_named_like_the_reference(live: Spy, tmp_path: Path) -> None:
    folder = write_plans(tmp_path, typed_plan())
    res = run(*apply_args(folder), env={"PD_KEY": SECRET_VALUE})
    assert res.exit_code == 0, res.output
    assert live.calls[0]["secrets"] == {"PD_KEY": SECRET_VALUE}
    assert SECRET_VALUE not in res.output


def test_a_missing_secret_fails_before_any_write(live: Spy, tmp_path: Path) -> None:
    folder = write_plans(tmp_path, typed_plan())
    res = run(*apply_args(folder))
    assert res.exit_code == 1 and "PD_KEY" in res.output
    assert live.calls == []


def test_a_secret_of_a_skipped_review_plan_is_not_needed(live: Spy, tmp_path: Path) -> None:
    folder = write_plans(tmp_path, typed_plan(review=True))
    res = run(*apply_args(folder))
    assert res.exit_code == 0, res.output
    assert live.calls[0]["secrets"] == {}


def test_plans_without_typed_destinations_read_no_environment(live: Spy, tmp_path: Path) -> None:
    aws = (FIXTURES / "notifications-aws" / "topic-0001.json").read_text()
    (tmp_path / "notification-plans").mkdir()
    (tmp_path / "notification-plans" / "topic-0001.json").write_text(aws)
    res = run(*apply_args(tmp_path))
    assert res.exit_code == 0, res.output
    assert not live.calls[0]["secrets"]


def sns_inputs(root: Path) -> tuple[Path, Path]:
    alerts = root / "alerts"
    done = run("convert", "--in", str(FIXTURES / "cloudwatch"), "--out", str(alerts))
    assert done.exit_code == 0, done.output
    topics = root / "topics"
    topics.mkdir()
    sub = {
        "Protocol": "https",
        "Endpoint": "https://h.example.com/x",
        "SubscriptionArn": "arn:aws:sns:x:1:t:abc",
    }
    (topics / "t.json").write_text(json.dumps({"TopicArn": TOPIC, "Subscriptions": [sub]}))
    return topics, alerts


def convert_notes(root: Path, *extra: str) -> tuple[Any, Path]:
    topics, alerts = sns_inputs(root)
    out = root / "out"
    res = run(
        "convert-notifications", "--topics", str(topics), "--alerts", str(alerts),
        "--out", str(out), *extra,
    )  # fmt: skip
    return res, out


def test_warn_https_payload_is_off_by_default_and_on_with_the_flag(tmp_path: Path) -> None:
    plain, out = convert_notes(tmp_path / "a")
    assert plain.exit_code == 0, plain.output
    assert "not the SNS message envelope" not in (out / "report.md").read_text()
    warned, out = convert_notes(tmp_path / "b", "--warn-https-payload")
    assert warned.exit_code == 0, warned.output
    assert "not the SNS message envelope" in (out / "report.md").read_text()


def test_convert_notifications_prints_plan_violations(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        cli,
        "notifications_terraform_result",
        lambda _plans: TerraformResult("", ["too many workflows"]),
    )
    res, _ = convert_notes(tmp_path)
    assert res.exit_code == 0 and "too many workflows" in res.output


def test_muting_handling_changes_the_workflow_and_is_checked(tmp_path: Path) -> None:
    res, out = convert_notes(tmp_path / "a", "--muting-handling", "DONT_NOTIFY_FULLY_MUTED_ISSUES")
    assert res.exit_code == 0, res.output
    assert '"DONT_NOTIFY_FULLY_MUTED_ISSUES"' in (out / "notifications.tf").read_text()
    _, out = convert_notes(tmp_path / "b")
    assert '"NOTIFY_ALL_ISSUES"' in (out / "notifications.tf").read_text()
    bad, _ = convert_notes(tmp_path / "c", "--muting-handling", "EVERYTHING")
    assert bad.exit_code == 1 and "--muting-handling" in bad.output


def test_incident_preference_reaches_the_policy_and_is_checked(tmp_path: Path) -> None:
    base = ["convert", "--in", str(FIXTURES / "cloudwatch")]
    res = run(*base, "--out", str(tmp_path / "a"), "--incident-preference", "PER_CONDITION")
    assert res.exit_code == 0, res.output
    assert '"PER_CONDITION"' in (tmp_path / "a" / "main.tf").read_text()
    plain = run(*base, "--out", str(tmp_path / "b"))
    assert "incident_preference" not in (tmp_path / "b" / "main.tf").read_text()
    bad = run(*base, "--out", str(tmp_path / "c"), "--incident-preference", "PER_DAY")
    assert bad.exit_code == 1 and "--incident-preference" in bad.output
    assert plain.exit_code == 0
