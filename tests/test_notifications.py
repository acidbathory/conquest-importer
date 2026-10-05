import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from typer.testing import CliRunner

from importer.cli import app
from importer.core.apply import NewRelicClient
from importer.core.apply_notifications import apply_notifications
from importer.core.emit.notifications import notifications_terraform, workflow_input
from importer.core.notification_ir import ChannelKind, IRChannel, IRTopic, build_plan
from importer.sources import REGISTRY
from importer.sources.cloudwatch.notifications import routing, translate_topic

FIXTURES = Path(__file__).parent / "fixtures"
TOPIC = "arn:aws:sns:us-east-1:123456789012:ops-alerts"


def sub(protocol: str, endpoint: str, arn: str = "arn:aws:sns:x:1:t:abc") -> dict[str, str]:
    return {"Protocol": protocol, "Endpoint": endpoint, "SubscriptionArn": arn}


def topic_file(*subs: dict[str, str]) -> dict[str, Any]:
    return {"TopicArn": TOPIC, "Subscriptions": list(subs)}


@pytest.mark.parametrize(
    ("subscription", "kind"),
    [
        (sub("email", "ops@example.com"), ChannelKind.EMAIL),
        (sub("email-json", "ops@example.com"), ChannelKind.EMAIL),
        (sub("https", "https://hooks.example.com/x?y=1"), ChannelKind.WEBHOOK),
    ],
)
def test_supported_subscriptions_become_channels(
    subscription: dict[str, str], kind: ChannelKind
) -> None:
    topic = translate_topic(topic_file(subscription))
    assert [c.kind for c in topic.channels] == [kind] and not topic.unsupported
    assert topic.name == "ops-alerts"


@pytest.mark.parametrize(
    ("subscription", "reason"),
    [
        (sub("http", "http://x.example.com"), "plain http"),
        (sub("sms", "+15551234567"), "no New Relic equivalent"),
        (sub("lambda", "arn:aws:lambda:x"), "no New Relic equivalent"),
        (sub("sqs", "arn:aws:sqs:x"), "no New Relic equivalent"),
        (sub("email", "not an address"), "address that is not supported"),
        (sub("https", 'https://x.example.com/"><script>'), "URL that is not supported"),
        (sub("email", "a@example.com", "PendingConfirmation"), "not confirmed"),
        (sub("carrier-pigeon", "coop 7"), "not supported"),
    ],
)
def test_other_subscriptions_are_reported_not_dropped_silently(
    subscription: dict[str, str], reason: str
) -> None:
    topic = translate_topic(topic_file(subscription))
    assert not topic.channels and reason in topic.unsupported[0]


def test_malformed_topic_entries_do_not_crash() -> None:
    topic = translate_topic({"TopicArn": "garbage", "Subscriptions": ["x", 5, None]})
    assert topic.name == "unknown-topic" and len(topic.unsupported) == 3


def test_plan_has_no_workflow_without_channels_or_conditions() -> None:
    topic = IRTopic(
        source_system="cloudwatch",
        source_key=TOPIC,
        name="t",
        channels=[IRChannel(kind=ChannelKind.EMAIL, target="a@example.com")],
    )
    assert build_plan(topic, [], "P").workflow is None
    empty = topic.model_copy(update={"channels": []})
    assert build_plan(empty, ["c1"], "P").workflow is None
    plan = build_plan(topic, ["b", "a", "a"], "P")
    assert plan.workflow is not None and plan.workflow.condition_names == ["a", "b"]


def test_routing_uses_only_sns_topics_and_converted_conditions() -> None:
    monitors = REGISTRY["cloudwatch"].convert(FIXTURES / "cloudwatch")
    names = {m.source_id: f"c-{m.source_id}" for m in monitors[:3]}
    routes = routing(monitors, names)
    assert set(routes) <= {TOPIC}
    assert all(n.startswith("c-") for v in routes.values() for n in v)
    assert len(routes.get(TOPIC, [])) <= 3


class FakeNR:
    def __init__(self, existing: list[str] | None = None, fail_on: str | None = None) -> None:
        self.existing = existing or []
        self.fail_on = fail_on
        self.calls: list[dict[str, Any]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.calls.append(body)
        q, v = body["query"], body["variables"]
        if "aiWorkflows {" in q and "workflows(filters" in q:
            ents = [{"id": "w0", "name": n} for n in self.existing]
            data = {"actor": {"account": {"aiWorkflows": {"workflows": {"entities": ents}}}}}
            return httpx.Response(200, json={"data": data})
        if self.fail_on and self.fail_on in q:
            return httpx.Response(
                200, json={"data": {self.fail_on: {"error": {"__typename": "X"}}}}
            )
        if "aiNotificationsCreateDestination" in q:
            n = len([c for c in self.calls if "CreateDestination" in c["query"]])
            out = {"destination": {"id": f"d{n}", "name": v["destination"]["name"]}, "error": None}
            return httpx.Response(200, json={"data": {"aiNotificationsCreateDestination": out}})
        if "aiNotificationsCreateChannel" in q:
            n = len([c for c in self.calls if "CreateChannel" in c["query"]])
            out = {"channel": {"id": f"ch{n}", "name": v["channel"]["name"]}, "error": None}
            return httpx.Response(200, json={"data": {"aiNotificationsCreateChannel": out}})
        out = {"workflow": {"id": "wf1", "name": v["data"]["name"]}, "errors": None}
        return httpx.Response(200, json={"data": {"aiWorkflowsCreateWorkflow": out}})


def client_for(fake: FakeNR) -> NewRelicClient:
    return NewRelicClient(SecretStr("k"), transport=httpx.MockTransport(fake), sleep=lambda _: None)


def two_channel_plan() -> Any:
    topic = translate_topic(
        topic_file(sub("email", "a@example.com"), sub("https", "https://h.example.com/x"))
    )
    return build_plan(topic, ["cond-1", "cond-2"], "Imported:")


def test_apply_creates_destination_channel_pairs_then_a_disabled_workflow() -> None:
    fake = FakeNR()
    result = apply_notifications(client_for(fake), 1, [two_channel_plan()])
    assert result.created == ["Imported: ops-alerts"] and not result.failed
    assert [o.kind for o in result.objects] == [
        "destination",
        "channel",
        "destination",
        "channel",
        "workflow",
    ]
    wf = next(
        c
        for c in fake.calls
        if "CreateWorkflow" in c["query"] or "aiWorkflowsCreateWorkflow" in c["query"]
    )
    data = wf["variables"]["data"]
    assert data["workflowEnabled"] is False
    assert [c["channelId"] for c in data["destinationConfigurations"]] == ["ch1", "ch2"]
    assert data["issuesFilter"]["predicates"][0]["values"] == ["cond-1", "cond-2"]


def test_existing_workflow_skips_the_whole_topic() -> None:
    fake = FakeNR(existing=["Imported: ops-alerts"])
    result = apply_notifications(client_for(fake), 1, [two_channel_plan()])
    assert result.skipped_existing == ["Imported: ops-alerts"] and not result.objects
    assert len(fake.calls) == 1


def test_a_failure_midway_still_reports_what_was_created() -> None:
    fake = FakeNR(fail_on="aiNotificationsCreateChannel")
    result = apply_notifications(client_for(fake), 1, [two_channel_plan()])
    assert result.failed and [o.kind for o in result.objects] == ["destination"]


def test_workflow_input_refuses_a_plan_without_a_workflow() -> None:
    topic = IRTopic(source_system="cloudwatch", source_key=TOPIC, name="t", channels=[])
    with pytest.raises(ValueError, match="no workflow"):
        workflow_input(build_plan(topic, ["c"], "P"), [])


def test_terraform_escapes_hostile_names_and_targets() -> None:
    topic = IRTopic(
        source_system="cloudwatch",
        source_key=TOPIC,
        name='x"\n${evil}',
        channels=[IRChannel(kind=ChannelKind.WEBHOOK, target="https://h.example.com/${a}")],
    )
    tf = notifications_terraform([build_plan(topic, ['c"${x}'], "P")])
    assert "${evil}" not in tf.replace("$${evil}", "")
    assert "${a}" not in tf.replace("$${a}", "") and "${x}" not in tf.replace("$${x}", "")
    assert "enabled               = false" in tf


def test_cli_end_to_end_and_terraform_validates(tmp_path: Path) -> None:
    runner = CliRunner()
    alerts, topics, out = tmp_path / "alerts", tmp_path / "topics", tmp_path / "n"
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
    topics.mkdir()
    (topics / "topic-0001.json").write_text(
        json.dumps(
            topic_file(
                sub("email", "ops@example.com"),
                sub("sms", "+15550100"),
                sub("https", "https://h.example.com/x"),
            )
        )
    )
    res = runner.invoke(
        app,
        [
            "convert-notifications",
            "--topics",
            str(topics),
            "--alerts",
            str(alerts),
            "--out",
            str(out),
        ],
    )
    assert res.exit_code == 0 and "planned 1 workflows" in res.output, res.output
    report = (out / "report.md").read_text()
    assert (
        "sms subscriptions have no New Relic equivalent" in report
        and "ops@example.com" not in report
    )
    dry = runner.invoke(app, ["apply-notifications", "--in", str(out), "--account-id", "7"])
    assert dry.exit_code == 0 and "DRY RUN" in dry.output and "1 workflows to create" in dry.output
    if shutil.which("terraform") is None:
        return
    terraform = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=out,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("cannot download the New Relic provider")
    check = subprocess.run(  # noqa: S603
        [terraform, "validate", "-no-color"], cwd=out, capture_output=True, text=True, check=False
    )
    assert check.returncode == 0, check.stdout + check.stderr
