"""apply_notifications: checks before the first write, secrets, needs_review, redaction."""

import json
from typing import Any

import httpx
from pydantic import SecretStr

from importer.core.apply import NewRelicClient
from importer.core.apply_notifications import apply_notifications
from importer.core.notification_ir import (
    AuthKind,
    ChannelKind,
    DestinationAuth,
    DestinationType,
    IRChannel,
    IRTopic,
    NotificationPlan,
    NrDestination,
    PlannedWorkflow,
    SecretRef,
    build_plan,
)


class Recorder:
    """A fake New Relic that records every request and can fail chosen mutations."""

    def __init__(self, fail_destination: str | None = None) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.fail_destination = fail_destination

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append(body)
        query = body["query"]
        if "workflows(filters" in query:
            data: dict[str, Any] = {
                "actor": {"account": {"aiWorkflows": {"workflows": {"entities": []}}}}
            }
            return httpx.Response(200, json={"data": data})
        if "aiNotificationsCreateDestination" in query:
            if self.fail_destination:
                return httpx.Response(500, text=f"boom {self.fail_destination}")
            out = {"destination": {"id": "d1", "name": "n"}, "error": None}
            return httpx.Response(200, json={"data": {"aiNotificationsCreateDestination": out}})
        if "aiNotificationsCreateChannel" in query:
            out = {"channel": {"id": "c1", "name": "n"}, "error": None}
            return httpx.Response(200, json={"data": {"aiNotificationsCreateChannel": out}})
        out = {"workflow": {"id": "w1", "name": "w"}, "errors": None}
        return httpx.Response(200, json={"data": {"aiWorkflowsCreateWorkflow": out}})

    def writes(self) -> list[str]:
        return [b["query"] for b in self.bodies if "mutation" in b["query"]]


def client(fake: Recorder) -> NewRelicClient:
    return NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(fake), sleep=lambda _: None)


def teams_plan(name: str = "teams") -> NotificationPlan:
    dest = NrDestination(
        type=DestinationType.MICROSOFT_TEAMS,
        name=name,
        properties={"securityCode": SecretRef(name="TEAMS_CODE")},
        channel_properties={"teamId": "t", "channelId": "c"},
    )
    wf = PlannedWorkflow(name=f"wf {name}", condition_names=["c"], destination_indexes=[0])
    return NotificationPlan(topic=name, destinations=[], typed_destinations=[dest], workflow=wf)


def email_plan(name: str = "mail", **flags: Any) -> NotificationPlan:
    topic = IRTopic(
        source_system="cloudwatch",
        source_key="k",
        name=name,
        channels=[IRChannel(kind=ChannelKind.EMAIL, target="a@example.com")],
    )
    plan = build_plan(topic, ["c"], "P:")
    return plan.model_copy(update=flags)


def test_a_missing_secret_fails_before_the_first_write() -> None:
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, [teams_plan()], secrets={})
    assert fake.writes() == [] and not result.created and not result.objects
    ((name, reason),) = result.failed
    assert name == "wf teams" and "TEAMS_CODE" in reason


def test_secrets_none_is_treated_as_no_secrets() -> None:
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, [teams_plan()])
    assert fake.writes() == [] and len(result.failed) == 1


def test_a_supplied_secret_reaches_the_destination_and_is_a_write() -> None:
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, [teams_plan()], secrets={"TEAMS_CODE": "abc123xyz"})
    assert result.created == ["wf teams"] and not result.failed
    create = next(b for b in fake.bodies if "aiNotificationsCreateDestination" in b["query"])
    assert create["variables"]["destination"]["properties"] == [
        {"key": "securityCode", "value": "abc123xyz"}
    ]


def test_a_destination_create_is_a_write_and_is_never_retried() -> None:
    fake = Recorder(fail_destination="x")
    with client(fake) as c:
        result = apply_notifications(c, 1, [email_plan()])
    destination_calls = [b for b in fake.bodies if "aiNotificationsCreateDestination" in b["query"]]
    assert len(destination_calls) == 1 and result.failed  # default client retries reads 3 times


def test_an_over_limit_plan_is_refused_before_any_write() -> None:
    plan = email_plan()
    assert plan.workflow is not None
    big = plan.workflow.model_copy(
        update={"condition_names": [f"c{i:04d}" + "x" * 90 for i in range(60)]}
    )
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, [plan.model_copy(update={"workflow": big})])
    assert fake.writes() == [] and "issue filter" in result.failed[0][1]


def test_needs_review_plans_are_skipped_and_listed_unless_asked() -> None:
    plans = [email_plan("plain"), email_plan("lambda-too", needs_review=True)]
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, plans)
    assert result.created == ["P: plain"] and result.skipped_review == ["P: lambda-too"]
    fake = Recorder()
    with client(fake) as c:
        result = apply_notifications(c, 1, plans, include_needs_review=True)
    assert sorted(result.created) == ["P: lambda-too", "P: plain"] and not result.skipped_review


def test_a_secret_is_removed_from_api_errors() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        query = json.loads(request.content)["query"]
        if "workflows(filters" in query:
            data: dict[str, Any] = {
                "actor": {"account": {"aiWorkflows": {"workflows": {"entities": []}}}}
            }
            return httpx.Response(200, json={"data": data})
        return httpx.Response(400, text="rejected code hunter2-secret-value")

    c = NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(handler), sleep=lambda _: None)
    with c:
        result = apply_notifications(
            c, 1, [teams_plan()], secrets={"TEAMS_CODE": "hunter2-secret-value"}
        )
    ((_, reason),) = result.failed
    assert "hunter2-secret-value" not in reason and "REDACTED" in reason


def test_basic_auth_secret_is_sent_in_the_auth_block() -> None:
    dest = NrDestination(
        type=DestinationType.SERVICE_NOW,
        name="snow",
        properties={"url": "https://x.service-now.com"},
        auth=DestinationAuth(
            kind=AuthKind.BASIC, user="me@example.com", password=SecretRef(name="SNOW_PASSWORD")
        ),
        channel_properties={"description": "d"},
    )
    wf = PlannedWorkflow(name="w", condition_names=["c"], destination_indexes=[0])
    plan = NotificationPlan(topic="s", destinations=[], typed_destinations=[dest], workflow=wf)
    fake = Recorder()
    with client(fake) as c:
        apply_notifications(c, 1, [plan], secrets={"SNOW_PASSWORD": "pw-value-1"})
    create = next(b for b in fake.bodies if "aiNotificationsCreateDestination" in b["query"])
    assert create["variables"]["destination"]["auth"]["basic"]["password"] == "pw-value-1"  # noqa: S105
