"""The contact point verdict table: what each Grafana integration becomes in New Relic."""

from collections import Counter

import pytest

from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import ContactPoint
from importer.sources.grafana.notifications.schema import type_names
from importer.sources.grafana.notifications.verdicts import (
    Verdict,
    contact_point_verdict,
    verdict_table,
)

C, W, M, N = (
    Verdict.CONVERTS,
    Verdict.WEBHOOK_TEMPLATE,
    Verdict.MANUAL_STEP,
    Verdict.NO_EQUIVALENT,
)
EXPECTED = {
    "email": (C, "EMAIL"),
    "webhook": (C, "WEBHOOK"),
    "oncall": (W, "WEBHOOK"),
    "prometheus-alertmanager": (W, "WEBHOOK"),
    "slack": (M, "SLACK"),
    "pagerduty": (M, "PAGERDUTY_SERVICE_INTEGRATION"),
    "teams": (M, "MICROSOFT_TEAMS"),
    "jira": (M, "JIRA"),
    "opsgenie": (W, "WEBHOOK"),
    "victorops": (W, "WEBHOOK"),
    "discord": (W, "WEBHOOK"),
    "googlechat": (W, "WEBHOOK"),
    "telegram": (W, "WEBHOOK"),
    "webex": (W, "WEBHOOK"),
    "sensugo": (W, "WEBHOOK"),
    "kafka": (W, "WEBHOOK"),
    "wecom": (W, "WEBHOOK"),
    "dingding": (W, "WEBHOOK"),
    "pushover": (M, "none"),
    "sns": (M, "EVENT_BRIDGE"),
    "threema": (N, "none"),
    "LINE": (N, "none"),
    "mqtt": (N, "none"),
    "wechat": (N, "none"),
}


def cp(type_name: str, *fields: str, **kwargs: object) -> ContactPoint:
    return ContactPoint(name="n", type=type_name, fields_set=list(fields), **kwargs)


def test_every_schema_type_has_exactly_one_verdict_row() -> None:
    assert sorted(verdict_table()) == sorted(type_names()) == sorted(EXPECTED)


def test_the_verdict_counts_of_the_table() -> None:
    counts = Counter(row.verdict for row in verdict_table().values())
    assert counts == {C: 2, W: 12, M: 6, N: 4}
    assert sum(counts.values()) == 24


@pytest.mark.parametrize(("type_name", "expected"), sorted(EXPECTED.items()))
def test_the_verdict_and_target_of_each_type(type_name: str, expected: tuple[Verdict, str]) -> None:
    found = contact_point_verdict(cp(type_name, "url"))
    assert (found.verdict, found.target) == expected


@pytest.mark.parametrize("type_name", sorted(EXPECTED))
def test_everything_but_a_plain_conversion_has_manual_steps_and_a_reason(type_name: str) -> None:
    found = contact_point_verdict(cp(type_name, "url"))
    if found.verdict is C:
        assert found.reasons == () or found.reasons == (R.R_CP_WEBHOOK_SETTINGS,)
    else:
        assert found.manual_steps and found.reasons


def test_email_converts_with_no_steps() -> None:
    found = contact_point_verdict(cp("email", "addresses", addresses=["a@example.com"]))
    assert found.verdict is C and found.manual_steps == () and found.reasons == ()


def test_a_plain_webhook_converts_with_the_payload_warning() -> None:
    found = contact_point_verdict(cp("webhook", "url"))
    assert found.verdict is C and found.target == "WEBHOOK"
    assert any("not the Grafana alerts[] body" in s for s in found.manual_steps)
    assert found.reasons == ()


@pytest.mark.parametrize(
    ("field", "fragment"),
    [
        ("password", "basic or bearer"),
        ("authorization_credentials", "basic or bearer"),
        ("hmacConfig.secret", "HMAC"),
        ("tlsConfig.clientKey", "TLS"),
        ("headers", "secrets"),
        ("payload.template", "Go template"),
    ],
)
def test_webhook_settings_that_cannot_convert_add_a_step_and_a_reason(
    field: str, fragment: str
) -> None:
    found = contact_point_verdict(cp("webhook", "url", field))
    assert found.verdict is C
    assert any(fragment in s for s in found.manual_steps)
    assert found.reasons == (R.R_CP_WEBHOOK_SETTINGS,)


def test_a_webhook_http_method_other_than_post_is_noted() -> None:
    put = contact_point_verdict(cp("webhook", "url", "httpMethod", options={"httpMethod": "PUT"}))
    post = contact_point_verdict(cp("webhook", "url", "httpMethod", options={"httpMethod": "POST"}))
    assert any("always use POST" in s for s in put.manual_steps)
    assert not any("always use POST" in s for s in post.manual_steps)
    assert put.reasons == (R.R_CP_WEBHOOK_SETTINGS,) and post.reasons == ()


def test_wecom_robot_mode_is_a_webhook_template_and_app_mode_has_no_equivalent() -> None:
    robot = contact_point_verdict(cp("wecom", "url"))
    app = contact_point_verdict(cp("wecom", "corp_id", "secret", "agent_id"))
    assert (robot.verdict, robot.target) == (W, "WEBHOOK")
    assert (app.verdict, app.target) == (N, "none")
    assert "access token" in app.manual_steps[0]


def test_an_unknown_type_is_reported_as_unknown_not_guessed() -> None:
    found = contact_point_verdict(cp("somethingnew", "url"))
    assert found.verdict is N and found.reasons == (R.R_CP_UNKNOWN,)


def test_the_reason_follows_the_verdict() -> None:
    by_type = {t: contact_point_verdict(cp(t, "url")).reasons for t in EXPECTED}
    assert by_type["pagerduty"] == (R.R_CP_MANUAL,)
    assert by_type["discord"] == (R.R_CP_WEBHOOK_TEMPLATE,)
    assert by_type["mqtt"] == (R.R_CP_NONE,)


def test_a_custom_template_field_adds_its_reason_and_a_step() -> None:
    found = contact_point_verdict(cp("slack", "title", templates={"title": "custom"}))
    assert R.R_CUSTOM_TEMPLATE in found.reasons
    default = contact_point_verdict(cp("slack", "title", templates={"title": "default"}))
    assert R.R_CUSTOM_TEMPLATE not in default.reasons


def test_names_and_hostile_text_never_reach_steps_or_reasons() -> None:
    hostile = ContactPoint(
        name="SECRET-NAME`; drop |", type="slack", labels={"recipient": "SECRET-LABEL"}
    )
    found = contact_point_verdict(hostile)
    text = " ".join([*found.manual_steps, *found.reasons, found.target])
    assert "SECRET" not in text


def test_every_reason_the_verdicts_use_is_a_fixed_sentence() -> None:
    used = {r for t in EXPECTED for r in contact_point_verdict(cp(t, "url")).reasons}
    assert used <= {
        R.R_CP_NONE,
        R.R_CP_MANUAL,
        R.R_CP_WEBHOOK_TEMPLATE,
        R.R_CP_WEBHOOK_SETTINGS,
    }
