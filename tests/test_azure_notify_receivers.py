"""Action group receivers to New Relic destinations: what converts, what stays manual, no leaks."""

from collections import Counter
from pathlib import Path

import pytest

from importer.core.notification_ir import DestinationType, SecretRef, validate_destination
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications.models import ActionGroup, Receiver, load_export
from importer.sources.azure.notifications.receivers import (
    GroupResult,
    Outcome,
    SecretNamer,
    convert_group,
)
from importer.sources.grafana.notify_convert import handlebars

FIX = Path(__file__).parent / "fixtures" / "azure"
PERSONAL = ("1234567890", "0987654321", "johndoe@email.com", "janesmith@email.com")
LEAKS = ("SYNTHETIC-PASSWORD", "SYNTHETIC-TOKEN", "SYNTHETIC-SIGNATURE", "SYNTHETIC-FUNCTION-KEY")


def group(file: str, name: str | None = None) -> ActionGroup:
    export = load_export(FIX / file)
    return next(g for g in export.action_groups if name is None or g.name == name)


def convert(g: ActionGroup) -> GroupResult:
    return convert_group(g, SecretNamer(), "Imported:")


def by_kind(result: GroupResult) -> dict[tuple[str, Outcome], int]:
    return dict(Counter((r.kind, r.outcome) for r in result.results))


@pytest.fixture(scope="module")
def full() -> GroupResult:
    return convert(group("action_groups/2023-01-01__createOrUpdateActionGroup.json"))


def test_every_one_of_the_eleven_kinds_gets_a_decision(full: GroupResult) -> None:
    assert {r.kind for r in full.results} == {
        "emailReceivers",
        "smsReceivers",
        "voiceReceivers",
        "webhookReceivers",
        "itsmReceivers",
        "azureAppPushReceivers",
        "automationRunbookReceivers",
        "logicAppReceivers",
        "azureFunctionReceivers",
        "armRoleReceivers",
        "eventHubReceivers",
    }


def test_outcomes_by_kind_on_the_official_example(full: GroupResult) -> None:
    got = by_kind(full)
    assert got[("emailReceivers", Outcome.CONVERTED)] == 2
    assert got[("webhookReceivers", Outcome.CONVERTED)] == 1
    assert got[("webhookReceivers", Outcome.MANUAL)] == 1  # useAadAuth
    assert got[("logicAppReceivers", Outcome.CONVERTED)] == 1
    # the example's responses carry the placeholders "<httpTriggerUrl>" and "<serviceUri>":
    # a placeholder is never counted as a converted destination
    assert got[("azureFunctionReceivers", Outcome.MANUAL)] == 1
    assert got[("smsReceivers", Outcome.NONE)] == 2
    assert got[("voiceReceivers", Outcome.NONE)] == 1
    assert got[("eventHubReceivers", Outcome.NONE)] == 1
    assert got[("armRoleReceivers", Outcome.NONE)] == 1
    assert got[("azureAppPushReceivers", Outcome.MANUAL)] == 1
    assert got[("itsmReceivers", Outcome.MANUAL)] == 1
    # the runbook url is "<serviceUri>" in the example: there is nothing to build a destination on
    assert got[("automationRunbookReceivers", Outcome.MANUAL)] == 1


def test_one_email_destination_per_group_and_webhooks_one_each(full: GroupResult) -> None:
    kinds = Counter(d.type for d in full.destinations)
    assert kinds[DestinationType.EMAIL] == 1
    assert kinds[DestinationType.WEBHOOK] == 2
    email = next(d for d in full.destinations if d.type == DestinationType.EMAIL)
    assert str(email.properties["email"]).split(",") == ["johndoe@email.com", "janesmith@email.com"]


def test_destinations_validate_against_the_new_relic_schema(full: GroupResult) -> None:
    for dest in full.destinations:
        validate_destination(dest)


def test_logic_app_url_secret_is_a_reference_and_the_prefix_is_the_host(full: GroupResult) -> None:
    logic = next(r for r in full.results if r.kind == "logicAppReceivers")
    dest = logic.destination
    assert dest is not None and dest.secure_url is not None
    assert dest.secure_url.prefix == "https://prod-27.northcentralus.logic.azure.com/"
    assert isinstance(dest.secure_url.suffix, SecretRef)
    assert dest.secure_url.suffix.name in logic.secrets
    assert "REDACTED" not in dest.model_dump_json()


def test_a_url_without_a_token_stays_a_plain_property_and_needs_no_secret(
    full: GroupResult,
) -> None:
    plain = next(r for r in full.results if r.kind == "webhookReceivers" and r.destination)
    assert plain.destination is not None and plain.destination.secure_url is None
    assert plain.secrets == ()
    assert R.R_HTTP_URL in plain.reasons  # the example url is http


def test_phone_numbers_and_app_accounts_are_never_stored(full: GroupResult) -> None:
    text = repr(full)
    for number in ("1234567890", "0987654321"):
        assert number not in text
    sms = [r for r in full.results if r.kind == "smsReceivers"]
    assert len({r.logical for r in sms}) == 2
    assert all(r.logical.startswith("AZURE_") and "_SMS" in r.logical for r in sms)
    assert all(SecretRef(name=r.logical) for r in sms)
    push = next(r for r in full.results if r.kind == "azureAppPushReceivers")
    assert push.manual is not None and "johndoe" not in repr(push)


def test_every_manual_receiver_has_steps_and_a_reason(full: GroupResult) -> None:
    for r in full.results:
        if r.outcome in (Outcome.MANUAL, Outcome.NONE):
            assert r.manual is not None and r.manual.steps and r.reasons, r.kind


def test_sms_and_voice_name_the_workarounds() -> None:
    result = convert(group("action_groups/2023-01-01__createOrUpdateActionGroup.json"))
    sms = next(r for r in result.results if r.kind == "smsReceivers")
    assert sms.manual is not None
    text = " ".join(sms.manual.steps)
    assert "PagerDuty" in text and "Opsgenie" in text and "SMS gateway" in text


def test_secret_urls_in_the_hostile_fixture_never_reach_the_destinations() -> None:
    result = convert(group("synthetic/SYNTHETIC__action_group_secrets_in_uris.json"))
    dumped = " ".join(d.model_dump_json() for d in result.destinations)
    dumped += " ".join(repr(r) for r in result.results)
    for leak in LEAKS:
        assert leak not in dumped, leak
    got = by_kind(result)
    # credentials inside the url and the Entra webhook need a person
    assert got[("webhookReceivers", Outcome.MANUAL)] == 2
    assert got[("logicAppReceivers", Outcome.CONVERTED)] == 1
    assert got[("azureFunctionReceivers", Outcome.CONVERTED)] == 1
    assert got[("automationRunbookReceivers", Outcome.CONVERTED)] == 1
    for dest in result.destinations:
        assert dest.secure_url is not None


def test_unicode_empty_and_disabled_receivers() -> None:
    result = convert(group("synthetic/SYNTHETIC__action_group_unicode_and_empty.json"))
    got = by_kind(result)
    assert got[("emailReceivers", Outcome.SKIPPED)] == 1  # status Disabled
    assert got[("emailReceivers", Outcome.MANUAL)] == 1  # the empty address
    assert got[("eventHubReceivers", Outcome.NONE)] == 1
    assert result.destinations == []
    assert not result.enabled


def test_unknown_receiver_types_are_listed_and_counted() -> None:
    result = convert(group("synthetic/SYNTHETIC__action_group_unknown_receiver_types.json"))
    assert result.unknown_kinds == 3
    assert R.R_UNKNOWN_RECEIVER in result.reasons
    got = by_kind(result)
    assert got[("webhookReceivers", Outcome.MANUAL)] == 1  # no uri
    assert got[("smsReceivers", Outcome.NONE)] == 1


def test_secret_names_are_logical_unique_and_distinct_per_group() -> None:
    namer = SecretNamer()
    a = ActionGroup(
        id="/subscriptions/s/resourceGroups/a/providers/microsoft.insights/actionGroups/x",
        name="Ops",
    )
    b = ActionGroup(
        id="/subscriptions/s/resourceGroups/b/providers/microsoft.insights/actionGroups/x",
        name="Ops",
    )
    wh = Receiver(kind="logicAppReceivers", name="l", callbackUrl="https://h.example/p?sig=abc")
    a.receivers, b.receivers = [wh], [wh]
    names = [convert_group(g, namer, "Imported:").results[0].secrets[0] for g in (a, b)]
    assert len(set(names)) == 2
    assert all(n.startswith("AZURE_OPS_") for n in names)


def test_payload_template_is_valid_handlebars_and_valid_json() -> None:
    import json

    from importer.sources.azure.notifications.payload import TEMPLATE

    assert handlebars.check(TEMPLATE) == [] and handlebars.json_position_problems(TEMPLATE) == []
    data = {
        "issueId": "1",
        "issueTitle": "t",
        "priority": "CRITICAL",
        "state": "CLOSED",
        "issueCreatedAtUtc": "a",
        "issueClosedAtUtc": "b",
        "issuePageUrl": "u",
    }
    body = json.loads(handlebars.render(TEMPLATE, data))
    assert body["data"]["essentials"]["monitorCondition"] == "Resolved"
    assert body["data"]["essentials"]["severity"] == "Sev1"
    assert body["schemaId"] == "azureMonitorCommonAlertSchema"


def test_webhook_destinations_carry_the_template_and_the_reason(full: GroupResult) -> None:
    wh = next(r for r in full.results if r.kind == "webhookReceivers" and r.destination)
    assert wh.destination is not None and "schemaId" in wh.destination.channel_properties["payload"]
    assert R.R_COMMON_SCHEMA in wh.reasons
