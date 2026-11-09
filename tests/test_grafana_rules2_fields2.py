"""Round 2: the runbook link and the origin note refuse what a reviewer found they accepted."""

from typing import Any

import pytest

from importer.sources.grafana.rules2 import fields

TOKEN36 = "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8"  # 36 letters and digits
assert len(TOKEN36) == 36


@pytest.mark.parametrize(
    "url",
    [
        f"https://example.com/x?k={TOKEN36}",
        f"https://example.com/ghp_{TOKEN36}",
        f"https://example.com/x/{TOKEN36}/y",
        f"https://example.com/x?s={TOKEN36}",
        "https://example.com/x?code=1",
        "https://example.com/x?jwt=abc",
        "https://example.com/x?pwd=abc",
        "https://example.com/x?pass=abc",
        "https://example.com/x?bearer=abc",
        "https://example.com/x?session=abc",
        "https://example.com/x?cookie=abc",
        "https://example.com/x?cred=abc",
        "https://example.com/x?signature=abc",
        "https://example.com/x;token=abc",
        "https://example.com/token/abc",
        "https://example.com/auth/abc",
        "https://example.com:99999/x",
        "https://example.com:0/x",
        "https://example.com:abc/x",
        "https://example.com/x\x00y",
        "https://example.com/x‮y",
        "https://example.com/x​y",
        "https://example.com/x\x7fy",
        "https://user@example.com/x",
        "https://:secret@example.com/x",
        "https://user:secret@example.com/x",
        "https://example.com/1234abcd-5678-ef90-1234-abcdef123456",
    ],
)
def test_a_runbook_link_with_a_credential_shaped_part_is_refused(url: str) -> None:
    assert fields.runbook({"runbook_url": url}) == (None, True)


@pytest.mark.parametrize(
    "url",
    [
        "https://runbooks.example.com/payments/high-error-rate",
        "https://wiki.example.com/design/author-notes",
        "https://example.com:8443/runbooks/cpu?service=payments&env=prod",
    ],
)
def test_ordinary_runbook_links_are_kept(url: str) -> None:
    assert fields.runbook({"runbook_url": url}) == (url, False)


@pytest.mark.parametrize(
    "labels",
    [
        {"contact": "admin:hunter2"},
        {"owner": "AKIAIOSFODNN7EXAMPLE"},
        {"team": "AKIAIOSFODNN7EXAMPLE"},
        {"team": "1234abcd-5678-ef90-1234-abcdef123456"},
        {"team": "ab12cd34-ef56-ab12-cd34-ef56ab12cd34x"},
        {"dsn": "payments"},
        {"bearer": "payments"},
        {"auth": "payments"},
        {"pwd": "payments"},
        {"owner": "payments"},
    ],
)
def test_a_label_off_the_allow_list_or_with_a_secret_value_is_never_shown(
    labels: dict[str, Any],
) -> None:
    note = fields.origin_note("", "", labels)
    assert note is not None and "1 more not shown" in note and "labels:" not in note
    for value in labels.values():
        assert value not in note


def test_allow_listed_labels_with_plain_values_are_shown_and_the_rest_counted() -> None:
    labels = {"severity": "page", "team": "payments", "owner": "bob", "contact": "x@y.z"}
    assert fields.origin_note("", "", labels) == (
        "grafana origin: (not shown); labels: severity=page, team=payments, 2 more not shown"
    )


def test_a_folder_that_looks_like_a_token_is_not_shown() -> None:
    assert fields.origin_note("ab12cd34-ef56-ab12-cd34-ef56ab12cd34x", "g", {}) == (
        "grafana origin: group g"
    )
