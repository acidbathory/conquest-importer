"""Secrets are dropped when an Azure resource is read; only a logical SecretRef name stays."""

import json
from typing import Any

from importer.sources.azure.sanitize import sanitize_resource

GROUP: dict[str, Any] = {
    "id": "/subscriptions/s/resourceGroups/rg/providers/microsoft.insights/actionGroups/ag1",
    "name": "ag1",
    "type": "Microsoft.Insights/ActionGroups",
    "properties": {
        "groupShortName": "ag",
        "enabled": True,
        "emailReceivers": [{"name": "ops", "emailAddress": "ops@example.com"}],
        "smsReceivers": [{"name": "s", "countryCode": "1", "phoneNumber": "5551230000"}],
        "webhookReceivers": [
            {
                "name": "hook one",
                "serviceUri": "https://hooks.example.com/services/T0/B0/SECRETPART?code=abc",
                "useAadAuth": True,
                "objectId": "11111111-2222-3333-4444-555555555555",
                "identifierUri": "api://secure-app",
                "tenantId": "t",
            }
        ],
        "logicAppReceivers": [
            {
                "name": "la",
                "resourceId": "/x/y",
                "callbackUrl": "https://prod.logic.azure.com/w?sig=ZZ",
            }
        ],
        "azureFunctionReceivers": [
            {"name": "fn", "httpTriggerUrl": "https://f.azurewebsites.net/api/x?code=KEY"}
        ],
        "automationRunbookReceivers": [
            {"name": "rb", "serviceUri": "https://s.example.com/webhooks?token=T"}
        ],
        "itsmReceivers": [
            {
                "name": "it",
                "ticketConfiguration": '{"PayloadRevision":0,"password":"p"}',
                "connectionId": "c",
            }
        ],
    },
}


def dump(value: object) -> str:
    return json.dumps(value)


def test_secret_fields_are_replaced_by_a_reference_and_the_host_is_kept() -> None:
    clean, dropped = sanitize_resource(GROUP, "actionGroups")
    text = dump(clean)
    for secret in (
        "SECRETPART",
        "code=abc",
        "sig=ZZ",
        "KEY",
        "token=T",
        "api://secure-app",
        "11111111-2222-3333-4444-555555555555",
        '\\"password\\"',
        "5551230000",
    ):
        assert secret not in text, secret
    hook = clean["properties"]["webhookReceivers"][0]
    assert hook["serviceUri"] == ""
    assert hook["serviceUriSecretRef"].startswith("secretref:azure/actionGroups/ag1/")
    assert hook["serviceUriHost"] == "hooks.example.com"
    assert hook["useAadAuth"] is True
    assert dropped >= 8


def test_what_conversion_needs_is_kept() -> None:
    clean, _ = sanitize_resource(GROUP, "actionGroups")
    props = clean["properties"]
    assert props["emailReceivers"][0]["emailAddress"] == "ops@example.com"
    assert props["smsReceivers"][0]["countryCode"] == "1"
    assert props["logicAppReceivers"][0]["resourceId"] == "/x/y"
    assert clean["id"] == GROUP["id"] and props["groupShortName"] == "ag"


def test_the_input_is_not_changed() -> None:
    before = dump(GROUP)
    sanitize_resource(GROUP, "actionGroups")
    assert dump(GROUP) == before


def test_free_form_property_maps_keep_their_keys_but_not_their_values() -> None:
    rule = {
        "name": "r",
        "properties": {
            "actions": [
                {"actionGroupId": "/a/b", "webHookProperties": {"auth": "Bearer abc123", "k": "v"}}
            ],
            "customProperties": {"x": "tokenvalue"},
        },
    }
    clean, dropped = sanitize_resource(rule, "metricAlerts")
    props = clean["properties"]
    hook = props["actions"][0]["webHookProperties"]
    assert set(hook) == {"auth", "k"} and all(v.startswith("secretref:") for v in hook.values())
    assert props["actions"][0]["actionGroupId"] == "/a/b"
    assert all(v.startswith("secretref:") for v in props["customProperties"].values())
    assert dropped == 3


def test_credentials_in_any_string_are_dropped() -> None:
    rule = {
        "name": "r",
        "properties": {
            "description": "see https://user:pw@host.example.com/x",
            "query": "T | where A == 'ok'",
        },
    }
    clean, _ = sanitize_resource(rule, "scheduledQueryRules")
    assert "user:pw" not in dump(clean) and clean["properties"]["query"] == "T | where A == 'ok'"


def test_password_like_keys_anywhere_are_dropped() -> None:
    clean, dropped = sanitize_resource(
        {"name": "r", "properties": {"nested": {"clientSecret": "s", "Password": "p", "keep": 1}}},
        "metricAlerts",
    )
    assert clean["properties"]["nested"] == {
        "clientSecretSecretRef": "secretref:azure/metricAlerts/r/properties/nested/clientSecret",
        "PasswordSecretRef": "secretref:azure/metricAlerts/r/properties/nested/Password",
        "keep": 1,
    }
    assert dropped == 2


def test_hostile_depth_and_types_do_not_fail() -> None:
    deep: Any = "x"
    for _ in range(50):
        deep = {"a": [deep]}
    clean, _ = sanitize_resource({"name": None, "properties": deep}, "metricAlerts")
    assert clean["name"] is None
