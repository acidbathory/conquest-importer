"""The workflow model: filters, triggers, muting handling, enrichments and New Relic's limits."""

import pytest

from hcl_schema import schema_errors
from importer.core.emit.common import CannotEmit
from importer.core.emit.notifications import notifications_terraform, workflow_input
from importer.core.notification_ir import (
    FILTER_LIMIT,
    MAX_WORKFLOWS,
    ChannelKind,
    Enrichment,
    IRChannel,
    IRTopic,
    NotificationPlan,
    PlannedDestination,
    PlannedPredicate,
    PlannedWorkflow,
    build_plan,
    issue_filter_size,
    plan_violations,
    tag_predicate,
    workflow_violations,
)


def topic(n: int = 1) -> IRTopic:
    channels = [IRChannel(kind=ChannelKind.EMAIL, target=f"a{i}@example.com") for i in range(n)]
    return IRTopic(source_system="cloudwatch", source_key="k", name="t", channels=channels)


def plan_with(**changes: object) -> NotificationPlan:
    plan = build_plan(topic(), ["c1", "c2"], "P:")
    assert plan.workflow is not None
    return plan.model_copy(update={"workflow": plan.workflow.model_copy(update=changes)})


def test_tag_predicate_uses_the_issue_attribute_not_the_alert_event_one() -> None:
    p = tag_predicate("team", ["payments", "core"])
    assert (p.attribute, p.operator, p.values) == (
        "accumulations.tag.team",
        "EXACTLY_MATCHES",
        ["payments", "core"],
    )


@pytest.mark.parametrize("name", ["", "a b", "a'b", "x" * 65, "a\nb", "${x}"])
def test_tag_predicate_refuses_odd_tag_names(name: str) -> None:
    with pytest.raises(ValueError, match="tag name"):
        tag_predicate(name, ["v"])


def test_predicate_operator_must_be_a_known_one() -> None:
    PlannedPredicate(attribute="priority", operator="EQUAL", values=["CRITICAL"])
    with pytest.raises(ValueError, match="operator"):
        PlannedPredicate(attribute="priority", operator="LIKE", values=["x"])


def test_filter_size_limit_is_reported_and_blocks_emission() -> None:
    names = [f"condition-{i:04d}-" + "x" * 100 for i in range(60)]
    plan = build_plan(topic(), names, "P:")
    assert plan.workflow is not None and issue_filter_size(plan.workflow) > FILTER_LIMIT
    problems = workflow_violations(plan)
    assert any(str(FILTER_LIMIT) in p and "issue filter" in p for p in problems)
    with pytest.raises(CannotEmit, match="issue filter"):
        workflow_input(plan, ["1"])
    tf = notifications_terraform([plan])
    assert "# not emitted: workflow" in tf and 'resource "newrelic_workflow"' not in tf
    assert 'resource "newrelic_notification_destination"' in tf  # a person still gets these


def test_a_filter_at_the_limit_is_fine_and_one_more_is_not() -> None:
    empty = build_plan(topic(), ["a"], "P:").workflow
    assert empty is not None
    room = FILTER_LIMIT - issue_filter_size(empty) + 1  # "a" is one character
    fits = build_plan(topic(), ["n" * room], "P:")
    assert fits.workflow is not None and issue_filter_size(fits.workflow) == FILTER_LIMIT
    assert workflow_violations(fits) == []
    over = build_plan(topic(), ["n" * (room + 1)], "P:")
    assert len(workflow_violations(over)) == 1


def test_defaults_leave_the_workflow_as_before() -> None:
    wf = workflow_input(plan_with(), ["7"])
    assert wf["mutingRulesHandling"] == "NOTIFY_ALL_ISSUES"
    assert wf["destinationConfigurations"] == [{"channelId": "7"}]
    assert "enrichments" not in wf and "enrichmentsEnabled" not in wf
    tf = notifications_terraform([plan_with()])
    assert 'muting_rules_handling = "NOTIFY_ALL_ISSUES"' in tf
    assert "notification_triggers" not in tf and "enrichments" not in tf


def test_muting_handling_option() -> None:
    plan = plan_with(muting_rules_handling="DONT_NOTIFY_FULLY_MUTED_ISSUES")
    assert workflow_input(plan, ["7"])["mutingRulesHandling"] == "DONT_NOTIFY_FULLY_MUTED_ISSUES"
    assert 'muting_rules_handling = "DONT_NOTIFY_FULLY_MUTED_ISSUES"' in notifications_terraform(
        [plan]
    )
    with pytest.raises(ValueError, match="muting"):
        PlannedWorkflow(
            name="w", condition_names=[], destination_indexes=[0], muting_rules_handling="MAYBE"
        )


def test_notification_triggers() -> None:
    plan = plan_with(notification_triggers=["ACTIVATED", "CLOSED"])
    data = workflow_input(plan, ["7"])
    assert data["destinationConfigurations"] == [
        {"channelId": "7", "notificationTriggers": ["ACTIVATED", "CLOSED"]}
    ]
    tf = notifications_terraform([plan])
    assert 'notification_triggers = ["ACTIVATED", "CLOSED"]' in tf
    assert schema_errors(tf) == []
    with pytest.raises(ValueError, match="trigger"):
        PlannedWorkflow(
            name="w", condition_names=[], destination_indexes=[0], notification_triggers=["NOPE"]
        )


def test_enrichments_are_emitted_and_capped_at_two() -> None:
    two = [Enrichment(name="a", query="SELECT count(*) FROM Log"), Enrichment(name="b", query="q")]
    plan = plan_with(enrichments=two)
    data = workflow_input(plan, ["7"])
    assert data["enrichmentsEnabled"] is True
    assert data["enrichments"] == {
        "nrql": [
            {"name": "a", "configuration": [{"query": "SELECT count(*) FROM Log"}]},
            {"name": "b", "configuration": [{"query": "q"}]},
        ]
    }
    tf = notifications_terraform([plan])
    assert "enrichments {" in tf and schema_errors(tf) == []
    three = plan_with(enrichments=[*two, Enrichment(name="c", query="q")])
    assert any("enrichments" in p for p in workflow_violations(three))
    with pytest.raises(CannotEmit, match="enrichments"):
        workflow_input(three, ["7"])
    dup = plan_with(enrichments=[two[0], two[0]])
    assert any("unique" in p for p in workflow_violations(dup))


def test_enrichment_query_is_escaped_in_terraform() -> None:
    plan = plan_with(enrichments=[Enrichment(name='n"', query='SELECT "${x}"')])
    tf = notifications_terraform([plan])
    assert "${x}" not in tf.replace("$${x}", "")


def test_one_workflow_per_channel_is_checked() -> None:
    twice = plan_with(destination_indexes=[0, 0])
    assert any("more than once" in p for p in workflow_violations(twice))
    outside = plan_with(destination_indexes=[3])
    assert any("does not exist" in p for p in workflow_violations(outside))
    with pytest.raises(CannotEmit):
        workflow_input(twice, ["7"])


def test_a_channel_cannot_serve_two_workflows() -> None:
    dest = PlannedDestination(kind=ChannelKind.EMAIL, name="n", target="a@example.com")
    wf = PlannedWorkflow(name="w", condition_names=["c"], destination_indexes=[0])
    a = NotificationPlan(topic="a", destinations=[dest], workflow=wf)
    b = NotificationPlan(topic="a", destinations=[dest], workflow=wf)  # same topic, same objects
    assert any("same topic" in p for p in plan_violations([a, b]))


def test_account_workflow_limit_is_reported() -> None:
    dest = PlannedDestination(kind=ChannelKind.EMAIL, name="n", target="a@example.com")
    wf = PlannedWorkflow(name="w", condition_names=["c"], destination_indexes=[0])
    plans = [
        NotificationPlan(
            topic=f"t{i}", destinations=[dest], workflow=wf.model_copy(update={"name": f"w{i}"})
        )
        for i in range(MAX_WORKFLOWS + 1)
    ]
    problems = plan_violations(plans)
    assert any(str(MAX_WORKFLOWS) in p for p in problems)
    assert plan_violations(plans[:MAX_WORKFLOWS]) == []
