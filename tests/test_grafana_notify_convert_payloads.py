"""The shipped webhook payload templates: real variables, valid JSON, marked when unverified."""

import json
import re
from typing import Any

import pytest

from importer.sources.grafana.notify_convert import handlebars as hb
from importer.sources.grafana.notify_convert import payloads as pl

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


def sample(state: str, title: str) -> dict[str, Any]:
    return {
        "issueId": "abc-123",
        "issueTitle": title,
        "state": state,
        "stateText": "closed" if state == "CLOSED" else "active",
        "priority": "CRITICAL",
        "priorityText": "Critical",
        "issuePageUrl": "https://one.newrelic.com/alerts/issue/abc?x=1&y=2",
        "issueClosedAtUtc": "2026-11-09T10:00:00Z",
    }


HOSTILE = ["plain", 'quote " and \\ backslash', "line\nbreak\ttab", '"}, {"injected": "x', "{{x}}"]


def test_the_twelve_vendor_types_and_the_generic_body_are_shipped() -> None:
    assert sorted(pl.all_templates()) == sorted([*VENDORS, "generic"])


@pytest.mark.parametrize("kind", [*VENDORS, "generic"])
def test_a_template_uses_only_real_variables_and_json_helpers(kind: str) -> None:
    body = pl.template_for(kind).body
    assert hb.check(body) == []
    assert hb.json_position_problems(body) == []


@pytest.mark.parametrize("state", ["ACTIVATED", "CLOSED"])
@pytest.mark.parametrize("title", HOSTILE)
@pytest.mark.parametrize("kind", [*VENDORS, "generic"])
def test_a_template_renders_valid_json_even_with_hostile_values(
    kind: str, title: str, state: str
) -> None:
    rendered = hb.render(pl.template_for(kind).body, sample(state, title))
    document = json.loads(rendered)  # raises on invalid JSON
    # the hostile text came out as data (a string value), not as structure
    assert any(title in text for text in strings(document))


def strings(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    items = node.values() if isinstance(node, dict) else node if isinstance(node, list) else []
    return [s for item in items for s in strings(item)]


def test_the_state_changes_what_is_sent() -> None:
    body = pl.template_for("victorops").body
    assert json.loads(hb.render(body, sample("CLOSED", "t")))["message_type"] == "RECOVERY"
    assert json.loads(hb.render(body, sample("ACTIVATED", "t")))["message_type"] == "CRITICAL"
    am = pl.template_for("prometheus-alertmanager").body
    assert "endsAt" in json.loads(hb.render(am, sample("CLOSED", "t")))[0]
    assert "endsAt" not in json.loads(hb.render(am, sample("ACTIVATED", "t")))[0]


@pytest.mark.parametrize("priority", ["CRITICAL", "HIGH", "MEDIUM", "LOW"])
def test_opsgenie_priority_covers_every_new_relic_priority(priority: str) -> None:
    data = sample("ACTIVATED", "t") | {"priority": priority}
    out = json.loads(hb.render(pl.template_for("opsgenie").body, data))
    assert (
        out["priority"] == {"CRITICAL": "P1", "HIGH": "P2", "MEDIUM": "P3", "LOW": "P4"}[priority]
    )


@pytest.mark.parametrize("kind", [*VENDORS, "generic"])
def test_placeholders_listed_are_exactly_those_in_the_body(kind: str) -> None:
    template = pl.template_for(kind)
    in_body = set(re.findall(r"REPLACE_[A-Z_]+", template.body))
    assert in_body == set(template.placeholders)


@pytest.mark.parametrize("kind", VENDORS)
def test_every_vendor_template_is_marked_unverified_with_its_reasons_or_none_needed(
    kind: str,
) -> None:
    template = pl.template_for(kind)
    assert template.status == "unverified"  # none was tested against the real service


def test_the_generic_body_is_documented_and_has_no_secret_or_placeholder() -> None:
    generic = pl.template_for("generic")
    assert generic.status == "documented" and not generic.placeholders and not generic.unverified


@pytest.mark.parametrize("kind", VENDORS)
def test_vendor_bodies_hold_no_secret_shaped_text(kind: str) -> None:
    from importer.core.notification_ir import looks_like_secret

    assert not looks_like_secret(pl.template_for(kind).body, strict=False)


def test_a_template_with_an_invented_variable_is_refused() -> None:
    raw: dict[str, dict[str, Any]] = {
        "x": {
            "status": "unverified",
            "summary": "s",
            "url": None,
            "auth": None,
            "channel_headers": None,
            "placeholders": {},
            "unverified": [],
            "body": ['{"a": {{json notAVariable}}}'],
        }
    }
    with pytest.raises(ValueError, match="unknown variable"):
        pl.parse_templates(raw)


def test_a_template_that_prints_a_plain_variable_into_json_is_refused() -> None:
    raw: dict[str, dict[str, Any]] = {
        "x": {
            "status": "unverified",
            "summary": "s",
            "url": None,
            "auth": None,
            "channel_headers": None,
            "placeholders": {},
            "unverified": [],
            "body": ['{"a": "{{issueTitle}}"}'],
        }
    }
    with pytest.raises(ValueError, match="plain variable"):
        pl.parse_templates(raw)


def test_urls_and_auth_of_the_vendor_types() -> None:
    assert pl.template_for("opsgenie").auth is not None
    assert pl.template_for("opsgenie").auth.scheme == "GenieKey"  # type: ignore[union-attr]
    assert pl.template_for("webex").auth.kind == "token"  # type: ignore[union-attr]
    assert pl.template_for("discord").url.prefix == "https://discord.com/api/webhooks/"  # type: ignore[union-attr]
    assert pl.template_for("kafka").channel_headers == {
        "Content-Type": "application/vnd.kafka.json.v2+json"
    }
