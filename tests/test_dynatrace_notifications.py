"""Davis problem notification workflows become New Relic notification plans."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.notifications import notifications_terraform, workflow_input
from importer.core.notification_ir import NotificationPlan
from importer.sources.dynatrace.models import DtWorkflow, load_workflows
from importer.sources.dynatrace.notifications import davis_plans, render_report

FIXTURES = Path(__file__).parent / "fixtures"
DT = FIXTURES / "dynatrace"
AWS = FIXTURES / "notifications-aws"


def davis(
    tasks: dict[str, Any], value: dict[str, Any] | None = None, title: str = "w"
) -> DtWorkflow:
    return DtWorkflow.model_validate(
        {
            "id": "w1",
            "title": title,
            "tasks": tasks,
            "trigger": {
                "eventTrigger": {
                    "isActive": True,
                    "triggerConfiguration": {"type": "davis-problem", "value": value or {}},
                }
            },
        }
    )


def email(*to: str) -> dict[str, Any]:
    return {"action": "dynatrace.email:send-email", "input": {"to": list(to)}}


def plans_by_title() -> dict[str, NotificationPlan]:
    return {p.topic.split("/", 1)[1]: p for p in davis_plans(load_workflows(DT), "Imported:")}


def test_only_davis_problem_workflows_get_a_plan() -> None:
    assert sorted(plans_by_title()) == ["example-problem-email", "example-problem-slack"]


def test_an_email_task_becomes_an_email_destination_marked_for_review() -> None:
    plan = plans_by_title()["example-problem-email"]
    assert [(d.kind.value, d.target) for d in plan.destinations] == [
        ("email", "oncall@example.com")
    ]
    assert plan.needs_review and plan.workflow is None
    assert any("filter" in n for n in plan.notes)


def test_slack_and_pagerduty_are_manual_destinations_with_secret_steps() -> None:
    plan = plans_by_title()["example-problem-slack"]
    assert not plan.destinations and plan.needs_review
    got = {m.service: m for m in plan.manual_destinations}
    assert got["slack"].nr_type == "SLACK" and got["pagerduty"].nr_type.startswith("PAGERDUTY")
    assert all(m.steps for m in got.values())
    assert got["slack"].label == "#example"


@pytest.mark.parametrize(
    ("action", "service", "nr_type"),
    [
        ("dynatrace.jira:jira-create-issue", "jira", "JIRA"),
        ("dynatrace.msteams:send-message", "ms teams", "MICROSOFT_TEAMS"),
        ("dynatrace.servicenow:create-incident", "servicenow", "SERVICE_NOW_APP"),
        ("dynatrace.automations:http-function", "http request", "WEBHOOK"),
        ("dynatrace.opsgenie:create-alert", "opsgenie", "WEBHOOK"),
        ("dynatrace.automations:run-javascript", "other", ""),
    ],
)
def test_every_other_action_is_manual_with_the_closest_type(
    action: str, service: str, nr_type: str
) -> None:
    (plan,) = davis_plans([davis({"t": {"action": action, "input": {"url": "https://x"}}})], "P:")
    (manual,) = plan.manual_destinations
    assert (manual.service, manual.nr_type) == (service, nr_type)
    assert manual.steps and not plan.destinations
    if not nr_type:
        assert any("no equivalent" in s or "manual" in s for s in manual.steps)


def test_entity_tags_become_issue_filter_predicates() -> None:
    wf = davis(
        {"e": email("a@example.com")},
        {"entityTags": {"team": ["payments", "core"]}, "categories": {"error": True}},
    )
    (plan,) = davis_plans([wf], "P:")
    assert plan.workflow is not None
    (pred,) = plan.workflow.predicates
    assert (pred.attribute, pred.operator, pred.values) == (
        "accumulations.tag.team",
        "EXACTLY_MATCHES",
        ["payments", "core"],
    )
    assert workflow_input(plan, ["c0"])["issuesFilter"]["predicates"][0]["values"] == [
        "payments",
        "core",
    ]
    tf = notifications_terraform([plan])
    assert 'attribute = "accumulations.tag.team"' in tf and '"payments", "core"' in tf
    assert "enabled               = false" in tf


@pytest.mark.parametrize(
    ("value", "note"),
    [
        ({"categories": {"availability": True}}, "categories"),
        ({"customFilter": "contains(event.name, 'x')"}, "custom filter"),
        ({"onProblemClose": True}, "closed"),
        ({"severityThreshold": "HIGH"}, "severityThreshold"),
        ({"entityTags": {"a": "1", "b": "2"}, "entityTagsMatch": "any"}, "any"),
        ({"entityTags": {"bad key!": "1"}}, "tag"),
        ({"entityTags": "nonsense"}, "tag"),
    ],
)
def test_what_cannot_be_mapped_is_a_note(value: dict[str, Any], note: str) -> None:
    (plan,) = davis_plans([davis({"e": email("a@example.com")}, value)], "P:")
    assert any(note in n for n in plan.notes), plan.notes
    assert plan.workflow is None


def test_an_invalid_address_is_not_migrated_and_not_echoed() -> None:
    (plan,) = davis_plans([davis({"e": email("good@example.com", "not an address<x>")})], "P:")
    assert [d.target for d in plan.destinations] == ["good@example.com"]
    text = " ".join(plan.notes)
    assert "not an address" not in text and "good@example.com" not in text
    assert "address" in text


def test_recipients_are_capped() -> None:
    wf = davis({"e": email(*[f"u{i}@example.com" for i in range(200)])})
    (plan,) = davis_plans([wf], "P:")
    assert 0 < len(plan.destinations) <= 20
    assert any("more than" in n for n in plan.notes)


def test_the_report_names_no_recipient_and_escapes_the_title() -> None:
    wf = davis(
        {
            "e": email("secret@example.com"),
            "s": {"action": "dynatrace.slack:x", "input": {"channel": "#a|b"}},
        },
        title="evil|`x`<b>‮",
    )
    text = render_report(davis_plans([wf], "P:"))
    assert "secret@example.com" not in text and "evil|" not in text and "<b>" not in text
    assert "#a|b" not in text


def test_hostile_title_and_address_reach_terraform_only_through_escaping() -> None:
    wf = davis(
        {"e": email("a@example.com")},
        {"entityTags": {"team": 'x"\n${evil}'}},
        title='t"${evil}\n',
    )
    tf = notifications_terraform(davis_plans([wf], "P:"))
    assert "${evil}" not in tf.replace("\\${", "").replace("$${", "")
    assert "\n${" not in tf


def test_two_workflows_with_one_title_get_distinct_names_and_resources() -> None:
    plans = davis_plans(
        [davis({"e": email("a@example.com")}, {"entityTags": {"t": "1"}}) for _ in range(2)], "P:"
    )
    assert plans[0].topic != plans[1].topic
    assert plans[0].workflow and plans[1].workflow
    assert plans[0].workflow.name != plans[1].workflow.name
    tf = notifications_terraform(plans)
    addresses = [ln for ln in tf.splitlines() if ln.startswith("resource ")]
    assert len(addresses) == len(set(addresses))


def test_an_inactive_trigger_is_noted() -> None:
    wf = davis({"e": email("a@example.com")})
    wf.trigger["eventTrigger"]["isActive"] = False
    (plan,) = davis_plans([wf], "P:")
    assert any("inactive" in n for n in plan.notes)


def test_email_pattern_is_safe_on_adversarial_input() -> None:
    from importer.core.notification_ir import EMAIL_ADDRESS

    for attack in ("a" * 100_000, "a@" + "b." * 50_000, "@" * 100_000, "a@b." + "c" * 100_000):
        start = time.perf_counter()
        EMAIL_ADDRESS.match(attack)
        assert time.perf_counter() - start < 0.5


# the AWS path must not change by a byte


def aws_run(tmp_path: Path) -> Path:
    runner = CliRunner()
    alerts, out = tmp_path / "alerts", tmp_path / "n"
    res = runner.invoke(
        app,
        [
            "convert",
            "--source",
            "cloudwatch",
            "--in",
            str(FIXTURES / "cloudwatch"),
            "--out",
            str(alerts),
        ],
    )
    assert res.exit_code == 0, res.output
    res = runner.invoke(
        app,
        [
            "convert-notifications",
            "--topics",
            str(AWS / "topic-input.json"),
            "--alerts",
            str(alerts),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    return out


def test_the_aws_notification_output_is_byte_identical(tmp_path: Path) -> None:
    out = aws_run(tmp_path)
    for name, produced in (
        ("topic-0001.json", out / "notification-plans" / "topic-0001.json"),
        ("notifications.tf", out / "notifications.tf"),
        ("report.md", out / "report.md"),
    ):
        assert produced.read_bytes() == (AWS / name).read_bytes(), name


def test_cli_converts_a_dynatrace_export(tmp_path: Path) -> None:
    out = tmp_path / "n"
    res = CliRunner().invoke(
        app,
        ["convert-notifications", "--source", "dynatrace", "--topics", str(DT), "--out", str(out)],
    )
    assert res.exit_code == 0, res.output
    assert "2 Davis" in res.output
    files = sorted((out / "notification-plans").glob("*.json"))
    assert len(files) == 2
    plans = [NotificationPlan.model_validate_json(f.read_text()) for f in files]
    assert all(p.needs_review for p in plans)
    report = (out / "report.md").read_text()
    assert "oncall@example.com" not in report and "needs review" in report
    tf = (out / "notifications.tf").read_text()
    assert "oncall@example.com" in tf  # the address belongs in the plan and Terraform only
    raw = json.loads(files[0].read_text())
    assert raw["needs_review"] is True


def test_cli_rejects_an_unknown_source(tmp_path: Path) -> None:
    res = CliRunner().invoke(
        app, ["convert-notifications", "--source", "gcp", "--out", str(tmp_path)]
    )
    assert res.exit_code == 1 and "cloudwatch or dynatrace" in res.output


def test_teams_is_its_own_destination_with_the_app_install_steps() -> None:
    (plan,) = davis_plans(
        [davis({"t": {"action": "dynatrace.msteams:send-message", "input": {}}})], "P:"
    )
    (manual,) = plan.manual_destinations
    text = " ".join(manual.steps)
    assert manual.nr_type == "MICROSOFT_TEAMS"
    assert "Install the New Relic app in Microsoft Teams" in text
    assert "security code" in text and "team id" in text and "channel id" in text
    assert "webhook" not in text.lower()


def test_servicenow_prefers_the_application_and_says_who_installs_it() -> None:
    (plan,) = davis_plans(
        [davis({"t": {"action": "dynatrace.servicenow:create-incident", "input": {}}})], "P:"
    )
    (manual,) = plan.manual_destinations
    assert manual.nr_type == "SERVICE_NOW_APP"
    assert any(
        "ServiceNow administrator installs the New Relic application" in s for s in manual.steps
    )


def test_opsgenie_goes_to_a_webhook_that_needs_a_vendor_payload_template() -> None:
    (plan,) = davis_plans(
        [davis({"t": {"action": "dynatrace.opsgenie:create-alert", "input": {}}})], "P:"
    )
    (manual,) = plan.manual_destinations
    assert manual.nr_type == "WEBHOOK"
    assert any("vendor payload template is required" in s for s in manual.steps)


@pytest.mark.parametrize("service", ["victorops", "xmatters"])
def test_the_vendor_webhook_services_have_steps(service: str) -> None:
    from importer.sources.dynatrace.notifications import SERVICES

    nr_type, steps = SERVICES[service]
    assert nr_type == "WEBHOOK" and any("vendor payload template" in s for s in steps)
