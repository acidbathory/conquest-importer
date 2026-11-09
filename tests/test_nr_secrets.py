"""Secret references, generated variable names, and what Terraform needs declared."""

import re

import pytest

from hcl_schema import fmt_errors, schema_errors, undeclared_variables
from importer.core.emit.notifications import (
    notifications_terraform,
    notifications_terraform_result,
    nr_destination_input,
)
from importer.core.notification_ir import (
    AuthKind,
    ChannelKind,
    DestinationAuth,
    DestinationType,
    HeaderAuth,
    IRChannel,
    IRTopic,
    NotificationPlan,
    NrDestination,
    PlannedWorkflow,
    SecretRef,
    build_plan,
)

PD = NrDestination(
    type=DestinationType.PAGERDUTY_SERVICE_INTEGRATION,
    name="pd",
    auth=DestinationAuth(
        kind=AuthKind.TOKEN, prefix="Token token=", token=SecretRef(name="PD_KEY")
    ),
    channel_properties={"summary": "{{ issueTitle }}"},
)
LEGACY = NrDestination(type=DestinationType.SLACK_LEGACY, name="legacy")
RESERVED = [
    "COUNT",
    "DEPENDS_ON",
    "FOR_EACH",
    "LIFECYCLE",
    "LOCALS",
    "PROVIDERS",
    "PROVIDER",
    "SOURCE",
    "VERSION",
]


def plan_of(*dests: NrDestination, topic: str = "t") -> NotificationPlan:
    wf = PlannedWorkflow(
        name=f"wf {topic}", condition_names=["c"], destination_indexes=list(range(len(dests)))
    )
    return NotificationPlan(
        topic=topic, destinations=[], typed_destinations=list(dests), workflow=wf
    )


def test_a_secret_of_an_emitted_destination_is_declared_when_another_is_skipped() -> None:
    tf = notifications_terraform([plan_of(PD, LEGACY)])
    assert "# not emitted: destination 2" in tf
    assert "var.nr_secret_pd_key" in tf and 'variable "nr_secret_pd_key"' in tf
    assert undeclared_variables(tf) == [] and schema_errors(tf) == []


def test_every_variable_reference_is_declared_for_every_kind_of_plan() -> None:
    plans = [plan_of(PD), plan_of(LEGACY, topic="u"), plan_of(PD, LEGACY, topic="v")]
    assert schema_errors(notifications_terraform(plans)) == []


@pytest.mark.parametrize("word", RESERVED)
def test_reserved_words_never_become_bare_variable_names(word: str) -> None:
    try:
        ref = SecretRef(name=word)
    except ValueError:
        return  # too short or without an underscore: refused as a logical name
    dest = PD.model_copy(
        update={"auth": DestinationAuth(kind=AuthKind.TOKEN, prefix="Token token=", token=ref)}
    )
    tf = notifications_terraform([plan_of(dest)])
    assert f"var.nr_secret_{word.lower()}" in tf and f"var.{word.lower()}" not in tf
    assert undeclared_variables(tf) == []


def test_generated_variable_names_do_not_collide() -> None:
    names = ["DEPENDS_ON", "FOR_EACH", "A_B", "A_B_C", "AB_C", "PD_KEY"]
    dests = [
        PD.model_copy(
            update={
                "auth": DestinationAuth(
                    kind=AuthKind.TOKEN, prefix="Token token=", token=SecretRef(name=n)
                )
            }
        )
        for n in names
    ]
    tf = notifications_terraform([plan_of(*dests)])
    declared = re.findall(r'^variable "([^"]+)"', tf, re.MULTILINE)
    assert len(declared) == len(set(declared)) == len(names)
    assert all(d.startswith("nr_secret_") for d in declared)


@pytest.mark.parametrize(
    "name",
    [
        "sk-live-abc123",
        "hunter2 real",
        "AKIAIOSFODNN7EXAMPLE",
        "AKIA_IOSFODNN7EXAMPLE",
        "GHP_ABCDEFGHIJ",
        "GLSA_ABCDEFGHIJ",
        "NRAK_ABCDEFGHIJ",
        "DT0C01_ABCDEFGH",
        "COUNT",
        "AB",
        "A_",
        "NOUNDERSCOREHERE",
        "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_ABCDEFGHIJ",
        "lower_case",
        "KEY_" + "A" * 40,
    ],
)
def test_secret_names_must_be_logical_names_not_values(name: str) -> None:
    with pytest.raises(ValueError, match="secret name"):
        SecretRef(name=name)


@pytest.mark.parametrize("name", ["PD_KEY", "TEAMS_CODE", "A_B", "NR_USER_API_KEY"])
def test_logical_secret_names_are_accepted(name: str) -> None:
    assert SecretRef(name=name).name == name


def teams(**props: object) -> dict[str, object]:
    return {
        "type": DestinationType.MICROSOFT_TEAMS,
        "name": "t",
        "properties": props,
        "channel_properties": {"teamId": "t", "channelId": "c"},
    }


def test_plaintext_for_a_secret_property_is_refused_at_construction() -> None:
    for value in ("123456", "s3cret-value", "hunter2 real"):
        with pytest.raises(ValueError, match="secret reference") as info:
            NrDestination.model_validate(teams(securityCode=value))
        assert value not in str(info.value)  # the error never echoes the input


@pytest.mark.parametrize(
    "build",
    [
        lambda: SecretRef(name="sk-live-abc123"),
        lambda: DestinationAuth(kind=AuthKind.BASIC, user="u", password="hunter2 real"),  # noqa: S106
        lambda: HeaderAuth(key="Api-Key", value="AKIAIOSFODNN7EXAMPLE"),
        lambda: NrDestination.model_validate(teams(securityCode="123456")),
        lambda: NrDestination(
            type=DestinationType.EMAIL, name="n", properties={"email": "sk-live-abc123"}
        ),
    ],
)
def test_errors_do_not_echo_the_input(build: object) -> None:
    with pytest.raises(ValueError) as info:  # noqa: PT011 - any ValueError, checked by text
        build()  # type: ignore[operator]
    text = str(info.value)
    for probe in ("sk-live-abc123", "hunter2 real", "AKIAIOSFODNN7EXAMPLE", "123456"):
        assert probe not in text


@pytest.mark.parametrize(
    "change",
    [
        {
            "auth": DestinationAuth(
                kind=AuthKind.BASIC,
                user="AKIAIOSFODNN7EXAMPLE",
                password=SecretRef(name="JIRA_TOKEN"),
            )
        },
        {"channel_properties": {"payload": '{"k": "sk-live-abc123def456"}'}},
        {
            "channel_properties": {
                "payload": "{}",
                "headers": '{"Authorization": "Bearer abcdefghijkl"}',
            }
        },
        {"channel_properties": {"payload": "x" * 40}},
    ],
)
def test_secret_looking_values_are_refused_outside_secret_fields(change: dict[str, object]) -> None:
    base: dict[str, object] = {
        "type": DestinationType.WEBHOOK,
        "name": "w",
        "properties": {"url": "https://h.example.com/x"},
        "channel_properties": {"payload": "{}"},
    }
    with pytest.raises(ValueError, match="looks like a secret"):
        NrDestination.model_validate({**base, **change})


def test_plain_headers_and_payloads_are_fine() -> None:
    NrDestination(
        type=DestinationType.WEBHOOK,
        name="w",
        properties={"url": "https://h.example.com/x"},
        channel_properties={
            "payload": '{"id": {{ json issueId }}}',
            "headers": '{"X-Team": "payments"}',
        },
    )


def test_a_typed_email_must_be_an_address() -> None:
    def email(value: str) -> NrDestination:
        return NrDestination(type=DestinationType.EMAIL, name="n", properties={"email": value})

    email("a@example.com")
    email("a@example.com,b@example.org")
    for bad in ("not an address", "a@b", "a@example.com,", "a@example.com, <x>"):
        with pytest.raises(ValueError, match="email"):
            email(bad)


def test_graphql_secrets_still_resolve_after_the_stricter_names() -> None:
    inp = nr_destination_input(PD, {"PD_KEY": "k"})
    assert inp["auth"]["token"] == {"prefix": "Token token=", "token": "k"}


# --- duplicate addresses -------------------------------------------------------------------


def aws_plan(source_key: str) -> NotificationPlan:
    topic = IRTopic(
        source_system="cloudwatch",
        source_key=source_key,
        name="alerts",
        channels=[IRChannel(kind=ChannelKind.EMAIL, target="a@example.com")],
    )
    return build_plan(topic, ["c"], "P:")


def test_two_topics_with_one_name_get_distinct_resource_addresses() -> None:
    plans = [
        aws_plan("arn:aws:sns:us-east-1:123456789012:alerts"),
        aws_plan("arn:aws:sns:eu-west-1:123456789012:alerts"),
    ]
    assert plans[0].topic == plans[1].topic
    result = notifications_terraform_result(plans)
    addresses = re.findall(r'^resource "(\w+)" "([^"]+)"', result.text, re.MULTILINE)
    assert len(addresses) == 6 and len(set(addresses)) == 6
    assert any("same topic" in v for v in result.violations)
    assert result.text == notifications_terraform(plans)


def test_non_colliding_plans_keep_their_addresses() -> None:
    a = aws_plan("arn:aws:sns:us-east-1:123456789012:alerts")
    result = notifications_terraform_result([a])
    assert result.violations == [] and result.text == notifications_terraform([a])


# --- fmt alignment of the new constructs ---------------------------------------------------


def test_generated_blocks_are_aligned_like_terraform_fmt() -> None:
    wf = plan_of(PD)
    assert wf.workflow is not None
    wf = wf.model_copy(
        update={"workflow": wf.workflow.model_copy(update={"notification_triggers": ["CLOSED"]})}
    )
    assert fmt_errors(notifications_terraform([wf])) == []
