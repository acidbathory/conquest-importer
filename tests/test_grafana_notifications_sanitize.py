"""Typed options, no free-text labels, and re-validation of every contact point shape."""

import json
from typing import Any

import pytest

from importer.sources.grafana.notifications import schema
from importer.sources.grafana.notifications.contact_points import (
    read_contact_points,
    sanitize_contact_point,
)

SECRET = "SECRETVALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests

# every secure (type, field) pair of the table, pinned so a flipped flag fails here
SECURE = {
    "prometheus-alertmanager": {"basicAuthPassword"},
    "dingding": {"url"},
    "discord": {"url"},
    "googlechat": {"url"},
    "jira": {"user", "password", "api_token"},
    "kafka": {"password"},
    "LINE": {"token"},
    "mqtt": {
        "password", "tlsConfig.caCertificate", "tlsConfig.clientCertificate", "tlsConfig.clientKey",
    },
    "oncall": {"password", "authorization_credentials"},
    "opsgenie": {"apiKey"},
    "pagerduty": {"integrationKey"},
    "pushover": {"apiToken", "userKey"},
    "sensugo": {"apikey"},
    "slack": {"token", "url"},
    "sns": {"sigv4.access_key", "sigv4.secret_key"},
    "telegram": {"bottoken"},
    "threema": {"api_secret"},
    "victorops": {"url"},
    "webex": {"bot_token"},
    "webhook": {
        "password", "authorization_credentials", "tlsConfig.caCertificate",
        "tlsConfig.clientCertificate", "tlsConfig.clientKey", "hmacConfig.secret",
    },
    "wecom": {"url", "secret"},
}  # fmt: skip


def test_the_secure_flags_of_the_table_are_pinned() -> None:
    found = {t: set(schema.secure_fields(t)) for t in schema.type_names()}
    assert {t: s for t, s in found.items() if s} == SECURE


def test_every_option_field_has_a_type_rule() -> None:
    for type_name, spec in schema.load_schema().items():
        for f in spec.fields:
            if f.kind == "option":
                assert f.values or f.type or f.pattern, (type_name, f.name)


def test_there_is_no_free_text_label_kind_any_more() -> None:
    kinds = {f.kind for spec in schema.load_schema().values() for f in spec.fields}
    assert "label" not in kinds


@pytest.mark.parametrize(
    ("type_name", "field", "value", "kept"),
    [
        ("webhook", "httpMethod", "PUT", True),
        ("webhook", "httpMethod", "SECRET3", False),
        ("webhook", "httpMethod", "put", False),
        ("webhook", "maxAlerts", 10, True),
        ("webhook", "maxAlerts", "10", True),
        ("webhook", "maxAlerts", "1e9", False),
        ("webhook", "maxAlerts", 10**9, False),
        ("webhook", "maxAlerts", True, False),
        ("webhook", "tlsConfig", {"insecureSkipVerify": True}, True),
        ("pushover", "retry", 60, True),
        ("pushover", "retry", 5, False),
        ("pushover", "retry", "hunter2", False),
        ("pushover", "priority", -1, True),
        ("pushover", "sound", "bike", False),
        ("pagerduty", "severity", "critical", True),
        ("pagerduty", "severity", "hunter2", False),
        ("slack", "recipient", "#alerts", False),
        ("slack", "mentionChannel", "here", True),
        ("jira", "project", "OPS", False),
        ("jira", "issue_type", "hunter2", False),
        ("jira", "reopen_duration", "30m", True),
        ("jira", "reopen_duration", "sk-live-1234567890", False),
        ("sns", "sigv4", {"region": "eu-west-1"}, True),
        ("sns", "sigv4", {"region": "hunter2"}, False),
        ("telegram", "parse_mode", "HTML", True),
        ("mqtt", "qos", 2, True),
        ("mqtt", "qos", 3, False),
    ],
)
def test_option_values_are_typed_and_the_rest_is_dropped_with_a_note(
    type_name: str, field: str, value: Any, kept: bool
) -> None:
    cp = sanitize_contact_point({"name": "n", "type": type_name, "settings": {field: value}})
    leaf = field
    if isinstance(value, dict):
        sub, value = next(iter(value.items()))
        leaf = f"{field}.{sub}"
    shown = "true" if value is True else "false" if value is False else str(value)
    assert (cp.options.get(leaf) == shown) is kept
    spec = schema.field_spec(type_name, leaf)
    assert (leaf in cp.dropped_options) is (not kept and spec is not None and spec.kind == "option")


def test_no_free_text_survives_in_any_option_or_label_field() -> None:
    settings = {
        "recipient": "hunter2",
        "project": "hunter2",
        "issue_type": "hunter2",
        "sound": "hunter2",
        "retry": "hunter2",
        "httpMethod": "SECRET3",
    }
    for type_name in ("slack", "jira", "pushover", "webhook"):
        cp = sanitize_contact_point({"name": "n", "type": type_name, "settings": settings})
        text = cp.model_dump_json()
        assert "hunter2" not in text and "SECRET3" not in text


def test_every_option_field_rejects_a_twenty_character_secret() -> None:
    for type_name, spec in schema.load_schema().items():
        for f in spec.fields:
            if f.kind != "option":
                continue
            leaf = f.name
            settings: dict[str, Any] = {}
            node = settings
            *parents, last = leaf.split(".")
            for p in parents:
                node = node.setdefault(p, {})
            node[last] = "S3CR3Tvalue_abcdefgh1"
            cp = sanitize_contact_point({"name": "n", "type": type_name, "settings": settings})
            assert "S3CR3T" not in cp.model_dump_json(), (type_name, f.name)


HOSTILE_SANITISED = {
    "name": "hostile",
    "type": "webhook",
    "uid": "u1",
    "fields_set": ["token=SECRETVALUE1234567890abcdef", "password", "ok_key"],
    "redacted": ["password", "not-in-fields-set"],
    "options": {"a": "sk-live-SECRETVALUE", "httpMethod": "SECRETVALUE", "maxAlerts": "5"},
    "labels": {"password": "hunter2"},
    "url_host": "hooks.example.com?k=SECRETURL",
    "templates": {"x": "SECRETVALUE", "title": "custom"},
    "other_fields": -5,
    "addresses": ["a@example.com"],
    "extra": {"password": "hunter2"},
}


def test_a_sanitised_shaped_item_is_validated_again_field_by_field() -> None:
    (cp,) = read_contact_points([HOSTILE_SANITISED])
    text = cp.model_dump_json()
    for leak in ("SECRETVALUE", "SECRETURL", "hunter2", "sk-live", "a@example.com"):
        assert leak not in text, leak
    assert cp.fields_set == ["ok_key", "password"]
    assert cp.redacted == ["password"]
    assert cp.options == {"maxAlerts": "5"}
    assert cp.url_host == "" and cp.templates == {"title": "custom"} and cp.other_fields >= 1


def test_mixed_lists_keep_the_clean_items_of_each_shape() -> None:
    raw = {"name": "raw", "type": "webhook", "settings": {"httpMethod": "PUT", "password": SECRET}}
    clean = sanitize_contact_point(
        {
            "name": "clean",
            "type": "webhook",
            "settings": {"url": "https://h.example.com/x", "httpMethod": "PUT"},
        }
    )
    found = read_contact_points([raw, json.loads(clean.model_dump_json()), HOSTILE_SANITISED])
    by_name = {c.name: c for c in found}
    assert by_name["raw"].options == {"httpMethod": "PUT"}
    assert by_name["clean"].url_host == "h.example.com" and by_name["clean"].options == {
        "httpMethod": "PUT"
    }
    assert SECRET not in json.dumps([c.model_dump() for c in found])


def test_an_item_with_a_bad_type_or_name_is_contained() -> None:
    (cp,) = read_contact_points([{"name": 5, "type": "../x", "fields_set": ["a"]}])
    assert cp.type == "other" and cp.name == ""


def test_a_name_or_uid_that_looks_sensitive_is_replaced_by_a_stable_reference() -> None:
    a = sanitize_contact_point(
        {"name": "oncall@example.com", "uid": "tok=abcdef1234", "type": "email"}
    )
    b = sanitize_contact_point({"name": "oncall@example.com", "type": "email"})
    assert "@" not in a.name and a.name.startswith("redacted-") and a.name == b.name
    assert "=" not in a.uid
    plain = sanitize_contact_point({"name": "team-slack", "type": "slack"})
    assert plain.name == "team-slack"


def test_a_setting_key_that_looks_like_a_secret_is_counted_not_listed() -> None:
    cp = sanitize_contact_point(
        {"name": "n", "type": "webhook", "settings": {"0123456789abcdef0123456789": "x", "ok": "y"}}
    )
    assert cp.fields_set == ["ok"] and cp.other_fields == 1


def test_at_most_two_hundred_setting_names_are_kept() -> None:
    cp = sanitize_contact_point(
        {"name": "n", "type": "webhook", "settings": {f"k{i}": "v" for i in range(300)}}
    )
    assert len(cp.fields_set) == 200
