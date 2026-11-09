"""Round 2 review fixes for the Dynatrace phase 1 work."""

import json
import time
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.data_check import Check, Status, render
from importer.core.emit.notifications import notifications_terraform
from importer.core.ir import IRMonitor
from importer.core.notification_ir import EMAIL_ADDRESS, NotificationPlan
from importer.core.taxonomy import Category, classify
from importer.sources.cloudwatch.notifications import translate_topic
from importer.sources.dynatrace import extract
from importer.sources.dynatrace.data_check import check_monitor
from importer.sources.dynatrace.dql import (
    MEMBER_NOTE,
    DqlError,
    parse_dql,
    parse_filter,
)
from importer.sources.dynatrace.models import DtSlo, DtWorkflow, load_exports
from importer.sources.dynatrace.notifications import davis_plans, render_report
from importer.sources.dynatrace.service_levels import (
    render_report as sl_report,
)
from importer.sources.dynatrace.service_levels import (
    service_level_plans,
    service_levels_terraform,
)
from importer.sources.dynatrace.translate import translate

DT = Path(__file__).parent / "fixtures" / "dynatrace"


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


def slo(criteria: list[dict[str, Any]]) -> DtSlo:
    return DtSlo.model_validate(
        {"id": "i", "name": "s", "criteria": criteria, "customSli": {"indicator": "x"}}
    )


def converted() -> IRMonitor:
    return next(m for m in translate(load_exports(DT)) if m.name == "example-checkout-errors")


class FakeClient:
    def __init__(self, matching: int = 0, all_logs: int = 0) -> None:
        self.matching = matching
        self.all_logs = all_logs

    def run(self, query: str, variables: dict[str, Any], *, is_write: bool) -> dict[str, Any]:
        count = self.matching if " WHERE " in variables["n"] else self.all_logs
        return {"actor": {"account": {"nrql": {"results": [{"count": count}]}}}}


# 1. email addresses


@pytest.mark.parametrize(
    "address",
    [
        "x@y.com\n",
        "x@y.com ",
        "x@y.com\x00",
        " x@y.com",
        "x@y.com\r\n",
        "a@b",
        "a@-b.com",
        "a@b-.com",
        "a@b..com",
        "a@.com",
        "a@b.c",
        "a..b@x.com",
        ".a@x.com",
        "a${x}@b.com",
        "a{{x}}@b.com",
        "a`b@c.com",
        "é@b.com",
        "a@b.com,c@d.com",
    ],
)
def test_bad_addresses_are_rejected(address: str) -> None:
    assert EMAIL_ADDRESS.match(address) is None and EMAIL_ADDRESS.fullmatch(address) is None


@pytest.mark.parametrize(
    "address",
    ["ops@example.com", "a.b+tag@sub.example.co.uk", "first_last%x@ex-ample.com", "A@B.COM"],
)
def test_ordinary_addresses_still_pass(address: str) -> None:
    assert EMAIL_ADDRESS.match(address)


def test_the_aws_path_rejects_a_trailing_newline() -> None:
    raw = {
        "TopicArn": "arn:aws:sns:us-east-1:123456789012:t",
        "Subscriptions": [{"Protocol": "email", "Endpoint": "x@y.com\n", "SubscriptionArn": "a:b"}],
    }
    topic = translate_topic(raw)
    assert not topic.channels and topic.unsupported


def test_the_dynatrace_path_rejects_a_trailing_newline() -> None:
    (plan,) = davis_plans([davis({"e": email("x@y.com\n", "ok@example.com")})], "P:")
    assert [d.target for d in plan.destinations] == ["ok@example.com"]


@pytest.mark.parametrize(
    "attack",
    ["a" * 100_000, "a@" + "b." * 50_000, "a@" + "b-" * 50_000, "a." * 50_000 + "@x.com"],
)
def test_the_address_pattern_is_fast(attack: str) -> None:
    start = time.perf_counter()
    EMAIL_ADDRESS.match(attack)
    assert time.perf_counter() - start < 0.5


# 2. recipients that cannot be read


@pytest.mark.parametrize(
    ("inputs", "word"),
    [
        ({"to": "a@example.com"}, "not a list"),
        ({"to": "{{ event()['x'] }}"}, "template"),
        ({"to": ["{{ x }}"]}, "template"),
        ({}, "no readable recipients"),
        ({"to": []}, "no readable recipients"),
        ({"to": [5, None]}, "recipient"),
    ],
)
def test_unreadable_recipients_always_leave_a_note(inputs: dict[str, Any], word: str) -> None:
    wf = davis({"e": {"action": "dynatrace.email:send-email", "input": inputs}})
    (plan,) = davis_plans([wf], "P:")
    assert not plan.destinations
    assert any(word in n for n in plan.notes), plan.notes


def test_categories_that_are_not_a_dict_are_noted() -> None:
    (plan,) = davis_plans([davis({"e": email("a@example.com")}, {"categories": "error"})], "P:")
    assert any("categories" in n for n in plan.notes)


def test_a_workflow_with_no_notification_task_is_noted() -> None:
    (plan,) = davis_plans([davis({})], "P:")
    assert any("no notification task" in n for n in plan.notes)


# 7. templated tag values


@pytest.mark.parametrize("bad", ["{{ x }}", "a{%if%}", "${env}", "pre-{{x}}"])
def test_templated_tag_values_are_noted_and_not_mapped(bad: str) -> None:
    wf = davis({"e": email("a@example.com")}, {"entityTags": {"team": bad}})
    (plan,) = davis_plans([wf], "P:")
    assert plan.workflow is None
    assert any("template" in n for n in plan.notes), plan.notes


# 8. tags.NAME


def test_notes_and_report_say_tags_name_not_angle_brackets() -> None:
    wf = davis({"e": email("a@example.com")}, {"entityTags": {"team": "core"}})
    plans = davis_plans([wf], "P:")
    assert all("<" not in n for p in plans for n in p.notes)
    assert "tags.NAME" in render_report(plans)


# 9. names, caps, output folder


def test_long_titles_keep_the_destination_index() -> None:
    wf = davis({"e": email("a@example.com", "b@example.com")}, title="t" * 300)
    (plan,) = davis_plans([wf], "Imported:")
    names = [d.name for d in plan.destinations]
    assert len(set(names)) == 2 and all(len(n) <= 120 for n in names)
    assert names[0].endswith(" email 1") and names[1].endswith(" email 2")


def test_repeated_and_lookalike_titles_never_share_a_name() -> None:
    tag = {"entityTags": {"t": "1"}}
    wfs = [
        davis({"e": email("a@example.com")}, tag, title="w"),
        davis({"e": email("a@example.com")}, tag, title="w"),
        davis({"e": email("a@example.com")}, tag, title="w (2)"),
    ]
    plans = davis_plans(wfs, "P:")
    workflow_names = [p.workflow.name for p in plans if p.workflow]
    destination_names = [d.name for p in plans for d in p.destinations]
    assert len(set(workflow_names)) == 3 and len(set(destination_names)) == 3
    tf = notifications_terraform(plans)
    resources = [ln for ln in tf.splitlines() if ln.startswith("resource ")]
    assert len(resources) == len(set(resources))


def test_destinations_and_manual_entries_are_capped_across_tasks() -> None:
    tasks = {f"e{i}": email(*[f"u{i}_{j}@example.com" for j in range(10)]) for i in range(10)}
    (plan,) = davis_plans([davis(tasks)], "P:")
    assert len(plan.destinations) == 20 and any("more than 20" in n for n in plan.notes)
    manual = {f"s{i}": {"action": "dynatrace.slack:x", "input": {}} for i in range(80)}
    (plan,) = davis_plans([davis(manual)], "P:")
    assert len(plan.manual_destinations) == 50 and any("more than 50" in n for n in plan.notes)


def test_too_many_workflows_in_one_run_are_refused() -> None:
    wfs = [DtWorkflow.model_validate(davis({}).model_dump()) for _ in range(2001)]
    with pytest.raises(ValueError, match="2000"):
        davis_plans(wfs, "P:")


def write_export(folder: Path, tags: bool) -> Path:
    folder.mkdir()
    value = {"entityTags": {"team": "core"}} if tags else {}
    wf = davis({"e": email("a@example.com")}, value, title="w")
    (folder / "wf.json").write_text(wf.model_dump_json())
    return folder


def test_a_non_empty_output_folder_is_refused(tmp_path: Path) -> None:
    export = write_export(tmp_path / "x", tags=True)
    out = tmp_path / "out"
    args = ["convert-notifications", "--source", "dynatrace", "--topics", str(export)]
    first = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert first.exit_code == 0, first.output
    second = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert second.exit_code == 1 and "already has plans" in second.output
    assert len(list((out / "notification-plans").glob("*.json"))) == 1


# 10. precision


def test_a_target_with_many_digits_is_not_rounded_up() -> None:
    crit = [{"target": 99.99999, "warning": 99.999991, "timeframeFrom": "now-7d"}]
    plans = service_level_plans([slo(crit)])
    assert "99.99999" in sl_report(plans) and "| 100 |" not in sl_report(plans)
    assert "target = 99.99999" in service_levels_terraform(plans)


# 11. one shared validator


@pytest.mark.parametrize(
    ("token", "ok"),
    [("dt0s16.abc.def", True), ("", False), ("a b", False), ("a“b", False), ("a\nb", False)],
)
def test_the_shared_token_rule(token: str, ok: bool) -> None:
    assert extract.is_plain_token(token) is ok


@pytest.mark.parametrize(
    ("url", "ok"),
    [
        ("https://abc.apps.dynatrace.com", True),
        ("https://abc.apps.dynatrace.com/", True),
        ("http://abc.apps.dynatrace.com", False),
        ("https://abc.apps.dynatrace.com/x", False),
        ("", False),
    ],
)
def test_the_shared_url_rule(url: str, ok: bool) -> None:
    assert extract.is_env_url(url) is ok


# 12. field names and contradictions


@pytest.mark.parametrize("field", ["a.", "a..b", ".a", "a.b.", "1a"])
def test_malformed_field_names_stay_manual(field: str) -> None:
    with pytest.raises(DqlError):
        parse_filter(f'filter {field} == "x"')


def query(filter_text: str) -> str:
    return f"fetch logs, from:now() - 15m | filter {filter_text} | summarize n = count()"


def test_the_same_field_with_two_values_is_noted() -> None:
    count = parse_dql(query('a == "x" and a == "y"'))
    assert any("two different values" in n for n in count.notes)
    assert classify(next(n for n in count.notes if "two different" in n)) is Category.APPROXIMATION
    assert not parse_dql(query('a == "x" and b == "y"')).notes
    assert not parse_dql(query('a == "x" and a == "x"')).notes


# 5. matchesValue and in notes


@pytest.mark.parametrize(
    "text",
    [
        'matchesValue(a, "x")',
        'matchesValue(a, {"x", "y"})',
        'in(a, "x", "y")',
        'in(a, array("x"))',
    ],
)
def test_membership_forms_carry_a_may_differ_note(text: str) -> None:
    assert MEMBER_NOTE in parse_dql(query(text)).notes
    assert classify(MEMBER_NOTE) is Category.APPROXIMATION


def test_an_equality_filter_has_no_membership_note() -> None:
    assert MEMBER_NOTE not in parse_dql(query('a == "x"')).notes


# 3 and 4. data check wording


def test_the_rendered_table_keeps_the_whole_hint() -> None:
    client = FakeClient(matching=0, all_logs=500)
    check = check_monitor(client, 1, converted(), 24)  # type: ignore[arg-type]
    assert check.status is Status.NO_DATA
    table = render([check], 24, "intro")
    assert "no matching events in 24 hours" in table
    assert "the event did not occur" in table and "New Relic" in table
    assert "none match" not in check.note


def test_a_long_note_is_not_cut_at_120() -> None:
    check = Check("c", Status.NO_DATA, 0, None, "word " * 40)
    assert ("word " * 40).strip() in render([check], 1, "i")


# 6. apply-notifications honours needs_review


def convert_plan(tmp_path: Path, tags: bool) -> Path:
    export = write_export(tmp_path / "x", tags)
    out = tmp_path / "plans"
    res = CliRunner().invoke(
        app,
        [
            "convert-notifications",
            "--source",
            "dynatrace",
            "--topics",
            str(export),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0, res.output
    return out


def test_dry_run_counts_plans_that_need_review(tmp_path: Path) -> None:
    out = convert_plan(tmp_path, tags=True)
    res = CliRunner().invoke(app, ["apply-notifications", "--in", str(out), "--account-id", "7"])
    assert res.exit_code == 0 and "1 plans need review" in res.output


def test_a_plan_without_a_workflow_is_said_to_create_nothing(tmp_path: Path) -> None:
    out = convert_plan(tmp_path, tags=False)
    res = CliRunner().invoke(app, ["apply-notifications", "--in", str(out), "--account-id", "7"])
    assert res.exit_code == 0 and "1 plans have no workflow" in res.output


def test_applying_a_plan_that_needs_review_requires_the_extra_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    out = convert_plan(tmp_path, tags=True)
    args = ["apply-notifications", "--in", str(out), "--account-id", "7", "--apply"]
    res = CliRunner().invoke(app, [*args, "--confirm-account", "7"])
    assert res.exit_code == 1 and "--include-needs-review" in res.output


def test_aws_plans_are_not_affected_by_the_review_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    plans = tmp_path / "notification-plans"
    plans.mkdir()
    aws = Path(__file__).parent / "fixtures" / "notifications-aws" / "topic-0001.json"
    (plans / "topic-0001.json").write_text(aws.read_text())
    assert not NotificationPlan.model_validate_json(aws.read_text()).needs_review
    dry = CliRunner().invoke(
        app, ["apply-notifications", "--in", str(tmp_path), "--account-id", "7"]
    )
    assert dry.exit_code == 0 and "need review" not in dry.output
    live = CliRunner().invoke(
        app,
        ["apply-notifications", "--in", str(tmp_path), "--account-id", "7", "--apply"],
    )
    assert "--include-needs-review" not in live.output


# 13. the hostile value that passes validation


def test_hostile_but_valid_tag_values_reach_terraform_escaped() -> None:
    hostile = 'x"\\ $ é ' + "$"
    wf = davis({"e": email("a@example.com")}, {"entityTags": {"team": hostile}}, title='t"${evil}')
    (plan,) = davis_plans([wf], "P:")
    assert plan.workflow is not None
    tf = notifications_terraform([plan])
    assert 'values    = ["x\\"\\\\ $ é $"]' in tf
    assert 'name                  = "P: t\\"$${evil}' in tf
    assert json.loads(plan.model_dump_json())["workflow"]["predicates"][0]["values"] == [hostile]
