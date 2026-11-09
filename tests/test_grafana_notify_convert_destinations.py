"""Contact points to New Relic destinations: one table-driven test per verdict class."""

import json
from typing import Any

import pytest

from importer.core.notification_ir import (
    AuthKind,
    DestinationType,
    SecretRef,
    validate_destination,
)
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import ContactPoint
from importer.sources.grafana.notifications.verdicts import Verdict
from importer.sources.grafana.notify_convert import destinations as dst
from importer.sources.grafana.notify_convert.destmap import MapEntry

PREFIX = "Imported:"


def point(type_: str = "webhook", name: str = "Ops", **kw: Any) -> ContactPoint:
    return ContactPoint(**{"uid": f"uid-{name}", "name": name, "type": type_, **kw})


def convert(cp: ContactPoint, entry: MapEntry | None = None) -> dst.Point:
    return dst.convert_point(cp, dst.SecretNamer(), PREFIX, entry)


# --- email ---


def test_email_becomes_an_email_destination_with_the_addresses_in_the_sensitive_field() -> None:
    cp = point("email", addresses=["a@example.com", "b@example.com"])
    result = convert(cp)
    assert result.destination is not None and result.manual is None
    assert result.destination.type is DestinationType.EMAIL
    assert result.destination.properties == {"email": "a@example.com,b@example.com"}
    assert result.destination.channel_properties == {"subject": "{{ issueTitle }}"}
    assert result.verdict.verdict is Verdict.CONVERTS
    validate_destination(result.destination)
    assert result.secrets == ()


def test_email_addresses_are_validated_with_the_shared_pattern() -> None:
    cp = point("email", addresses=["ok@example.com", "bad address@x", "x@y", "a@b.c" * 2])
    result = convert(cp)
    assert result.destination is not None
    assert result.destination.properties == {"email": "ok@example.com"}
    assert any("3 addresses" in s for s in result.steps)  # a count, never the address


def test_email_over_five_addresses_gets_the_free_plan_note() -> None:
    cp = point("email", addresses=[f"u{i}@example.com" for i in range(7)])
    result = convert(cp)
    assert any("5" in s and "150" in s for s in result.steps)


def test_email_over_150_addresses_is_cut() -> None:
    cp = point("email", addresses=[f"u{i}@example.com" for i in range(200)])
    result = convert(cp)
    assert result.destination is not None
    assert len(str(result.destination.properties["email"]).split(",")) == 150
    assert any("50 addresses" in s for s in result.steps)


def test_email_without_addresses_is_a_manual_entry_that_names_the_map() -> None:
    result = convert(point("email"))
    assert result.destination is None and result.manual is not None
    assert result.manual.nr_type == "EMAIL"
    assert any("destinations map" in s for s in result.manual.steps)


def test_the_map_supplies_the_addresses() -> None:
    result = convert(point("email"), MapEntry(email=["me@example.com"]))
    assert result.destination is not None
    assert result.destination.properties["email"] == "me@example.com"


# --- webhook ---


def test_webhook_has_a_secure_url_from_a_secret_reference_and_the_host_as_prefix() -> None:
    cp = point("webhook", url_host="hooks.example.com", fields_set=["url"])
    result = convert(cp)
    d = result.destination
    assert d is not None and d.type is DestinationType.WEBHOOK
    assert d.secure_url is not None and d.secure_url.prefix == "https://hooks.example.com/"
    assert d.secure_url.suffix.name.startswith(
        "GRAFANA_OPS_"
    ) and d.secure_url.suffix.name.endswith("_URL")
    assert "url" not in d.properties
    assert d.channel_properties["payload"].startswith("{")
    assert result.secrets == (d.secure_url.suffix.name,)
    validate_destination(d)


def test_webhook_without_a_known_host_uses_a_bare_scheme_prefix() -> None:
    result = convert(point("webhook"))
    assert result.destination is not None and result.destination.secure_url is not None
    assert result.destination.secure_url.prefix == "https://"


def test_webhook_credentials_become_references_never_values() -> None:
    cp = point("webhook", fields_set=["url", "authorization_credentials"])
    d = convert(cp).destination
    assert d is not None and d.auth is not None
    assert d.auth.kind is AuthKind.TOKEN and d.auth.prefix == "Bearer"
    assert isinstance(d.auth.token, SecretRef) and d.auth.token.name.endswith("_AUTH_TOKEN")


def test_webhook_basic_credentials_become_one_authorization_header_reference() -> None:
    cp = point("webhook", fields_set=["url", "username", "password"])
    d = convert(cp).destination
    assert d is not None and d.auth is not None
    assert d.auth.kind is AuthKind.CUSTOM_HEADER
    assert [h.key for h in d.auth.headers] == ["Authorization"]
    assert d.auth.headers[0].value.name.endswith("_BASIC_AUTH")


def test_webhook_with_the_unsupported_settings_keeps_the_verdict_steps_and_reasons() -> None:
    cp = point(
        "webhook",
        fields_set=["url", "hmacConfig.secret", "headers", "tlsConfig.clientKey"],
        options={"httpMethod": "PUT"},
    )
    result = convert(cp)
    assert R.R_CP_WEBHOOK_SETTINGS in result.reasons
    text = " ".join(result.steps)
    for word in ("HMAC", "TLS", "headers", "POST"):
        assert word in text


def test_the_map_names_the_url_secret() -> None:
    result = convert(point("webhook"), MapEntry(webhook_url="MY_HOOK_URL"))
    d = result.destination
    assert d is not None and d.secure_url is not None
    assert d.secure_url.suffix.name == "MY_HOOK_URL"
    assert result.secrets == ("MY_HOOK_URL",)


# --- the twelve webhook template types ---

VENDORS = [
    "oncall",
    "opsgenie",
    "victorops",
    "discord",
    "googlechat",
    "telegram",
    "webex",
    "sensugo",
    "kafka",
    "dingding",
    "wecom",
    "prometheus-alertmanager",
]


@pytest.mark.parametrize("kind", VENDORS)
def test_each_vendor_type_is_a_webhook_with_its_payload_and_unverified_marks(kind: str) -> None:
    cp = point(kind, fields_set=["url"])  # wecom needs url for robot mode
    result = convert(cp)
    d = result.destination
    assert d is not None, kind
    assert d.type is DestinationType.WEBHOOK
    assert result.verdict.verdict is Verdict.WEBHOOK_TEMPLATE
    assert "{{" in d.channel_properties["payload"]
    assert result.unverified  # no vendor payload was tested
    validate_destination(d)
    assert d.secure_url is not None or "url" in d.properties


def test_opsgenie_authorization_is_a_header_reference_and_the_api_url_is_plain() -> None:
    result = convert(
        point("opsgenie", fields_set=["apiKey", "apiUrl"], url_host="api.eu.opsgenie.com")
    )
    d = result.destination
    assert d is not None and d.auth is not None
    assert d.properties == {"url": "https://api.eu.opsgenie.com/v2/alerts"}
    assert d.auth.kind is AuthKind.CUSTOM_HEADER and d.auth.headers[0].key == "Authorization"
    assert any("GenieKey" in s for s in result.steps)


def test_webex_token_is_a_bearer_token_reference() -> None:
    d = convert(point("webex")).destination
    assert d is not None and d.auth is not None
    assert d.auth.kind is AuthKind.TOKEN and d.auth.prefix == "Bearer"
    assert d.properties == {"url": "https://webexapis.com/v1/messages"}


def test_telegram_has_the_fixed_prefix_and_a_placeholder_step() -> None:
    result = convert(point("telegram"))
    d = result.destination
    assert d is not None and d.secure_url is not None
    assert d.secure_url.prefix == "https://api.telegram.org/"
    assert any("REPLACE_CHAT_ID" in s for s in result.steps)


def test_kafka_has_channel_headers_marked_unverified() -> None:
    result = convert(point("kafka"))
    d = result.destination
    assert d is not None
    assert json.loads(d.channel_properties["headers"]) == {
        "Content-Type": "application/vnd.kafka.json.v2+json"
    }
    assert any("Content-Type" in u for u in result.unverified)


def test_wecom_app_mode_has_no_equivalent() -> None:
    result = convert(point("wecom", fields_set=["corp_id", "agent_id"]))
    assert result.destination is None and result.manual is None
    assert result.verdict.verdict is Verdict.NO_EQUIVALENT


# --- manual step types ---


def test_pagerduty_is_a_service_integration_with_a_key_reference() -> None:
    result = convert(point("pagerduty", fields_set=["integrationKey"]))
    d = result.destination
    assert d is not None and d.type is DestinationType.PAGERDUTY_SERVICE_INTEGRATION
    assert d.auth is not None and d.auth.prefix == "Token token="
    assert d.auth.token is not None and d.auth.token.name.endswith("_PD_KEY")
    assert d.channel_properties == {"summary": "{{ issueTitle }}"}
    assert any("integration" in s for s in result.steps)
    validate_destination(d)


def test_the_map_names_the_pagerduty_key() -> None:
    result = convert(point("pagerduty"), MapEntry(pagerduty_key="TEAM_PD_KEY"))
    d = result.destination
    assert d is not None and d.auth is not None and d.auth.token == SecretRef(name="TEAM_PD_KEY")


@pytest.mark.parametrize(
    ("kind", "nr_type", "fragment"),
    [
        ("slack", "SLACK", "OAuth"),
        ("teams", "MICROSOFT_TEAMS", "security code"),
        ("jira", "JIRA", "project"),
        ("sns", "EVENT_BRIDGE", "EventBridge"),
        ("pushover", "", "Pushover"),
    ],
)
def test_manual_types_are_listed_with_their_human_steps(
    kind: str, nr_type: str, fragment: str
) -> None:
    result = convert(point(kind, name="Pager Team"))
    assert result.destination is None and result.manual is not None
    assert result.manual.nr_type == nr_type
    assert result.manual.label == "Pager Team"
    assert fragment in " ".join(result.manual.steps)
    assert result.verdict.verdict is Verdict.MANUAL_STEP


def test_teams_steps_include_the_app_install_the_code_and_the_ids() -> None:
    result = convert(point("teams"))
    assert result.manual is not None
    text = " ".join(result.manual.steps)
    for word in ("Install the New Relic app", "security code", "team id", "channel id"):
        assert word in text


@pytest.mark.parametrize("kind", ["threema", "LINE", "mqtt", "wechat", "something-new"])
def test_no_equivalent_types_have_no_destination_and_a_reason(kind: str) -> None:
    result = convert(point(kind))
    assert result.destination is None and result.manual is None
    assert result.verdict.verdict is Verdict.NO_EQUIVALENT
    assert result.verdict.manual_steps and result.reasons


# --- skip, mismatch, flags ---


def test_skip_removes_the_destination_and_says_so() -> None:
    result = convert(point("email", addresses=["a@example.com"]), MapEntry(skip=True))
    assert result.skipped and result.destination is None and result.manual is None


@pytest.mark.parametrize(
    ("kind", "entry"),
    [
        ("slack", MapEntry(email=["a@example.com"])),
        ("email", MapEntry(webhook_url="SOME_URL")),
        ("webhook", MapEntry(pagerduty_key="SOME_KEY")),
        ("pagerduty", MapEntry(webhook_url="SOME_URL")),
    ],
)
def test_a_map_entry_that_does_not_fit_the_type_is_ignored_and_reported(
    kind: str, entry: MapEntry
) -> None:
    cp = point(kind, addresses=["a@example.com"] if kind == "email" else [])
    plain = convert(cp)
    result = convert(cp, entry)
    assert result.map_mismatch and not plain.map_mismatch
    assert (result.destination is None) == (plain.destination is None)


def test_disable_resolve_message_is_carried() -> None:
    assert convert(point("webhook", disable_resolve_message=True)).disable_resolve


# --- names of secrets ---


def test_secret_names_are_logical_unique_and_never_shaped_like_keys() -> None:
    namer = dst.SecretNamer()
    names = {namer.ref(point(name=f"hook {i}"), "URL").name for i in range(300)} | {
        namer.ref(point(name="x" * 500 + "AKIA" * 10), "URL").name
    }
    assert len(names) == 301
    for name in names:
        SecretRef(name=name)  # validates the shape
        assert len(name) <= 40


def test_two_contact_points_with_one_name_get_different_secrets() -> None:
    namer = dst.SecretNamer()
    a = namer.ref(point(name="same", uid=""), "URL")
    b = namer.ref(point(name="same", uid=""), "URL")
    assert a != b


def test_a_name_made_of_symbols_still_gives_a_valid_secret_name() -> None:
    ref = dst.SecretNamer().ref(point(name="🔥 | `x` \n"), "URL")
    assert ref.name.startswith("GRAFANA_")


# --- no name, address or secret reaches steps, reasons or notes ---


@pytest.mark.parametrize("kind", ["email", "webhook", "pagerduty", "slack", "teams", *VENDORS])
def test_steps_and_reasons_carry_no_name_or_address(kind: str) -> None:
    cp = point(
        kind,
        name="HOSTILE-name `x` | <b>",
        addresses=["EMAILADDR@example.com"],
        url_host="hooks.example.com",
        fields_set=["url", "headers", "password"],
    )
    result = convert(cp)
    text = " ".join([*result.steps, *result.reasons, *result.unverified])
    if result.manual:
        text += " ".join(result.manual.steps)
    assert "HOSTILE" not in text and "EMAILADDR" not in text and "example.com" not in text
