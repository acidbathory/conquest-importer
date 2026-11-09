"""The New Relic destination and channel model: per-type properties, auth, secrets, human steps."""

import pytest

from hcl_schema import resource_count, schema_errors
from importer.core.emit.common import CannotEmit
from importer.core.emit.notifications import (
    SECRET_PLACEHOLDER,
    notifications_terraform,
    nr_channel_input,
    nr_destination_input,
)
from importer.core.notification_ir import (
    SPECS,
    AuthKind,
    DestinationAuth,
    DestinationError,
    DestinationType,
    HeaderAuth,
    NotificationPlan,
    NrDestination,
    PlannedWorkflow,
    SecretRef,
    SecureUrl,
    needs_human_step,
    spec_for,
    validate_destination,
)

TOKEN = SecretRef(name="PD_KEY")
BASIC = DestinationAuth(
    kind=AuthKind.BASIC, user="me@example.com", password=SecretRef(name="BASIC_PASSWORD")
)


def dest(kind: DestinationType, **kw: object) -> NrDestination:
    return NrDestination.model_validate({"type": kind, "name": f"d {kind.value}", **kw})


GOOD: dict[DestinationType, NrDestination] = {
    DestinationType.WEBHOOK: dest(
        DestinationType.WEBHOOK,
        properties={"url": "https://h.example.com/x"},
        auth=DestinationAuth(
            kind=AuthKind.TOKEN, prefix="Bearer", token=SecretRef(name="WEBHOOK_TOKEN")
        ),
        channel_properties={"payload": '{"a": "{{ issueId }}"}'},
    ),
    DestinationType.EMAIL: dest(
        DestinationType.EMAIL,
        properties={"email": "a@example.com"},
        channel_properties={"subject": "{{ issueTitle }}"},
    ),
    DestinationType.SERVICE_NOW: dest(
        DestinationType.SERVICE_NOW,
        properties={"url": "https://x.service-now.com"},
        auth=BASIC,
        channel_properties={"description": "d", "short_description": "s"},
    ),
    DestinationType.SERVICE_NOW_APP: dest(
        DestinationType.SERVICE_NOW_APP,
        properties={"url": "https://x.service-now.com"},
        auth=BASIC,
    ),
    DestinationType.PAGERDUTY_ACCOUNT_INTEGRATION: dest(
        DestinationType.PAGERDUTY_ACCOUNT_INTEGRATION,
        auth=DestinationAuth(kind=AuthKind.TOKEN, prefix="Token token=", token=TOKEN),
        channel_properties={"summary": "{{ issueTitle }}", "service": "PABC123", "email": "a@b.co"},
    ),
    DestinationType.PAGERDUTY_SERVICE_INTEGRATION: dest(
        DestinationType.PAGERDUTY_SERVICE_INTEGRATION,
        auth=DestinationAuth(kind=AuthKind.TOKEN, prefix="Token token=", token=TOKEN),
        channel_properties={"summary": "{{ issueTitle }}"},
    ),
    DestinationType.JIRA: dest(
        DestinationType.JIRA,
        properties={"url": "https://x.atlassian.net"},
        auth=BASIC,
        channel_properties={"project": "10001", "issuetype": "10002"},
    ),
    DestinationType.MOBILE_PUSH: dest(DestinationType.MOBILE_PUSH, properties={"userId": "123"}),
    DestinationType.EVENT_BRIDGE: dest(
        DestinationType.EVENT_BRIDGE,
        properties={"AWSAccountId": "123456789012", "AWSRegion": "us-east-1"},
        channel_properties={"eventSource": "aws.partner/newrelic.com/1/x", "eventContent": "{}"},
    ),
    DestinationType.MICROSOFT_TEAMS: dest(
        DestinationType.MICROSOFT_TEAMS,
        properties={"securityCode": SecretRef(name="TEAMS_CODE")},
        channel_properties={"teamId": "t1", "channelId": "19:abc@thread.tacv2"},
    ),
    DestinationType.WORKFLOW_AUTOMATION: dest(
        DestinationType.WORKFLOW_AUTOMATION,
        auth=DestinationAuth(
            kind=AuthKind.CUSTOM_HEADER,
            headers=[HeaderAuth(key="Api-Key", value=SecretRef(name="NR_KEY"))],
        ),
        channel_properties={"workflowAutomation": "ref-1"},
    ),
}
NOT_CREATABLE = (
    DestinationType.SLACK,
    DestinationType.SLACK_COLLABORATION,
    DestinationType.SLACK_LEGACY,
)


def plan_of(*dests: NrDestination) -> NotificationPlan:
    wf = PlannedWorkflow(
        name="wf", condition_names=["c"], destination_indexes=list(range(len(dests)))
    )
    return NotificationPlan(topic="t", destinations=[], typed_destinations=list(dests), workflow=wf)


def test_every_destination_type_has_a_spec_and_the_channel_renames() -> None:
    assert set(SPECS) == set(DestinationType) and len(DestinationType) == 14
    assert spec_for(DestinationType.SERVICE_NOW).channel_type == "SERVICENOW_INCIDENTS"
    assert spec_for(DestinationType.JIRA).channel_type == "JIRA_CLASSIC"
    assert spec_for(DestinationType.EMAIL).channel_type == "EMAIL"
    assert spec_for(DestinationType.SLACK_COLLABORATION).products == ("DISCUSSIONS",)


@pytest.mark.parametrize("kind", sorted(GOOD, key=str))
def test_valid_destinations_pass_validation(kind: DestinationType) -> None:
    validate_destination(GOOD[kind])


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"properties": {"url": "https://h.example.com", "colour": "red"}}, "unknown property"),
        ({"properties": {}}, "requires property url"),
        ({"channel_properties": {"payload": "{}", "method": "PUT"}}, "unknown channel property"),
        ({"channel_properties": {}}, "requires channel property payload"),
        ({"auth": DestinationAuth(kind=AuthKind.OAUTH)}, "does not accept auth"),
    ],
)
def test_webhook_refuses_unknown_or_missing_parts(change: dict[str, object], message: str) -> None:
    base = GOOD[DestinationType.WEBHOOK].model_dump()
    base.update({"properties": {"url": "https://h.example.com"}, **change})
    with pytest.raises(DestinationError, match=message):
        validate_destination(NrDestination.model_validate(base))


def test_a_secret_field_must_hold_a_reference_never_a_value() -> None:
    fields = {
        "type": DestinationType.MICROSOFT_TEAMS,
        "name": "t",
        "properties": {"securityCode": "s3cret-value"},
        "channel_properties": {"teamId": "t", "channelId": "c"},
    }
    with pytest.raises(ValueError, match="secret reference"):
        NrDestination.model_validate(fields)  # refused when it is built
    bad = NrDestination.model_construct(**fields)  # type: ignore[arg-type]
    with pytest.raises(DestinationError, match="must be a secret reference"):
        validate_destination(bad)  # and again when it is emitted
    with pytest.raises(ValueError, match="secret name"):
        SecretRef(name="lower case value with spaces")


def test_a_reference_in_a_plain_field_is_refused() -> None:
    bad = dest(
        DestinationType.EMAIL,
        properties={"email": SecretRef(name="SOME_NAME")},
        channel_properties={"subject": "s"},
    )
    with pytest.raises(DestinationError, match="is not a secret"):
        validate_destination(bad)


def test_human_steps_are_data() -> None:
    assert needs_human_step(DestinationType.SLACK)
    assert needs_human_step(DestinationType.MICROSOFT_TEAMS)
    assert needs_human_step(DestinationType.SERVICE_NOW_APP)
    assert needs_human_step(DestinationType.EVENT_BRIDGE)
    assert not needs_human_step(DestinationType.EMAIL)
    teams = " ".join(spec_for(DestinationType.MICROSOFT_TEAMS).human_steps)
    assert "security code" in teams and "app" in teams.lower()
    assert any("OAuth" in s for s in spec_for(DestinationType.SLACK).human_steps)


@pytest.mark.parametrize("kind", sorted(GOOD, key=str))
def test_terraform_matches_the_provider_schema_and_holds_no_secret_value(
    kind: DestinationType,
) -> None:
    tf = notifications_terraform([plan_of(GOOD[kind])])
    assert schema_errors(tf) == []
    assert resource_count(tf) == 3  # destination, channel, workflow


def test_secrets_become_sensitive_variables() -> None:
    tf = notifications_terraform([plan_of(GOOD[DestinationType.MICROSOFT_TEAMS])])
    assert "value = var.nr_secret_teams_code" in tf
    assert 'variable "nr_secret_teams_code"' in tf and "sensitive = true" in tf


def test_terraform_auth_blocks() -> None:
    tf = notifications_terraform([plan_of(GOOD[DestinationType.SERVICE_NOW])])
    assert "auth_basic {" in tf and "password = var.nr_secret_basic_password" in tf
    tf = notifications_terraform([plan_of(GOOD[DestinationType.WORKFLOW_AUTOMATION])])
    assert "auth_custom_header {" in tf and 'key   = "Api-Key"' in tf
    tf = notifications_terraform([plan_of(GOOD[DestinationType.PAGERDUTY_SERVICE_INTEGRATION])])
    assert "auth_token {" in tf and 'prefix = "Token token="' in tf


def test_secure_url_block_is_terraform_only() -> None:
    d = dest(
        DestinationType.WEBHOOK,
        secure_url=SecureUrl(prefix="https://h.example.com/", suffix=SecretRef(name="HOOK_SUFFIX")),
        channel_properties={"payload": "{}"},
    )
    tf = notifications_terraform([plan_of(d)])
    assert "secure_url {" in tf and schema_errors(tf) == []
    with pytest.raises(CannotEmit, match="secure_url"):
        nr_destination_input(d, {"HOOK_SUFFIX": "x"})


@pytest.mark.parametrize("kind", NOT_CREATABLE)
def test_oauth_and_legacy_types_cannot_be_created(kind: DestinationType) -> None:
    d = dest(kind, channel_properties={"channelId": "C1"} if kind == DestinationType.SLACK else {})
    with pytest.raises(CannotEmit):
        nr_destination_input(d, {})
    tf = notifications_terraform([plan_of(d)])
    assert "newrelic_notification_destination" not in tf and "# not emitted" in tf


def test_terraform_escapes_hostile_names_and_values() -> None:
    d = dest(
        DestinationType.EMAIL,
        name='x"\n${evil}',
        properties={"email": "a@example.com"},
        channel_properties={"subject": 'S" ${x} %{y}'},
    )
    tf = notifications_terraform([plan_of(d)])
    assert "${evil}" not in tf.replace("$${evil}", "")
    assert "${x}" not in tf.replace("$${x}", "") and "%{y}" not in tf.replace("%%{y}", "")


def test_graphql_inputs_use_placeholders_or_given_secrets() -> None:
    d = GOOD[DestinationType.MICROSOFT_TEAMS]
    preview = nr_destination_input(d, None)
    assert preview["properties"] == [{"key": "securityCode", "value": SECRET_PLACEHOLDER}]
    real = nr_destination_input(d, {"TEAMS_CODE": "abc"})
    assert real["properties"] == [{"key": "securityCode", "value": "abc"}]
    with pytest.raises(CannotEmit, match="TEAMS_CODE"):
        nr_destination_input(d, {})
    channel = nr_channel_input(d, "42")
    assert channel["type"] == "MICROSOFT_TEAMS" and channel["destinationId"] == "42"
    assert {p["key"] for p in channel["properties"]} == {"teamId", "channelId"}


def test_graphql_auth_shapes() -> None:
    inp = nr_destination_input(GOOD[DestinationType.SERVICE_NOW], {"BASIC_PASSWORD": "pw"})
    assert inp["auth"] == {
        "authType": "BASIC",
        "basic": {"user": "me@example.com", "password": "pw"},
    }
    inp = nr_destination_input(GOOD[DestinationType.WORKFLOW_AUTOMATION], {"NR_KEY": "k"})
    assert inp["auth"]["customHeaders"] == [{"key": "Api-Key", "value": "k"}]
    chan = nr_channel_input(GOOD[DestinationType.SERVICE_NOW], "1")
    assert chan["type"] == "SERVICENOW_INCIDENTS"
