"""Rule fields: origin (folder, group, labels), runbook link, no-data state, paused."""

import importlib
from typing import Any

import pytest

from importer.core.emit.common import emittable_window
from importer.core.ir import Confidence
from importer.sources.grafana.rules2 import fields
from test_grafana import make, one

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")


def origin(**kw: Any) -> str | None:
    return fields.origin_note(kw.get("folder", ""), kw.get("group", ""), kw.get("labels", {}))


def test_folder_group_and_labels_make_one_note() -> None:
    note = origin(folder="ops", group="cpu", labels={"team": "payments", "severity": "critical"})
    assert note == "grafana origin: folder ops, group cpu; labels: severity=critical, team=payments"


def test_a_rule_with_none_of_them_has_no_note() -> None:
    assert origin() is None


@pytest.mark.parametrize(
    "labels",
    [
        {"api_token": "abc"},
        {"password": "x"},
        {"x-secret": "y"},
        {"unlisted": "v"},
        {"team": "a" * 64},
        {"team": "0123456789abcdef0123456789abcdef"},
        {"team": "<script>alert(1)</script>"},
        {"team": "a\nb"},
        {"team": "a b"},
        {"team": 5},
        {"team": None},
        {"bad key": "v"},
        {"": "v"},
    ],
)
def test_a_label_that_is_long_odd_or_secret_looking_is_counted_not_shown(
    labels: dict[str, Any],
) -> None:
    note = origin(labels=labels)
    assert note is not None and "1 more not shown" in note
    for value in labels.values():
        assert not isinstance(value, str) or len(value) < 3 or value not in note


def test_an_unsafe_folder_is_not_shown() -> None:
    note = origin(folder="a folder with spaces", group="g")
    assert note == "grafana origin: group g"
    assert origin(folder="x y") is None


@pytest.mark.parametrize(
    ("url", "kept"),
    [
        ("https://runbooks.example.com/payments", True),
        ("https://runbooks.example.com/p?service=payments", True),
        ("http://runbooks.example.com/payments", False),
        ("javascript:alert(1)", False),
        ("https://user:pw@example.com/x", False),
        ("https://example.com/x?token=abc", False),
        ("https://example.com/x?api_key=abc", False),
        ("https://example.com/x#frag", False),
        ("https://example.com/has space", False),
        ("https://", False),
        ("https://[::1", False),
        ("https://example.com/" + "a" * 2100, False),
    ],
)
def test_a_runbook_link_is_passed_on_only_when_it_is_a_plain_https_address(
    url: str, kept: bool
) -> None:
    link, refused = fields.runbook({"runbook_url": url})
    assert (link == url, refused) == (kept, not kept)


@pytest.mark.parametrize("value", [None, "", "  ", 5, ["https://x.com"]])
def test_no_runbook_is_neither_kept_nor_refused(value: Any) -> None:
    assert fields.runbook({"runbook_url": value}) == (None, False)
    assert fields.runbook({}) == (None, False)


# in the translated rule


def test_a_converted_rule_carries_its_origin_and_its_runbook_link() -> None:
    rule = make(
        "avg(cpu)",
        folder="ops",
        group="cpu",
        labels={"team": "payments"},
        annotations={"runbook_url": "https://runbooks.example.com/cpu", "summary": "CPU high"},
        reducer="mean",
    )
    m = one(rule)
    assert m.confidence is Confidence.APPROXIMATE
    assert "grafana origin: folder ops, group cpu; labels: team=payments" in m.reasons
    assert m.emit is not None and m.emit.runbook_url == "https://runbooks.example.com/cpu"
    assert m.notification.message_template == "CPU high"
    assert emittable_window(m) > 0  # the link passes the emitter's own check


def test_a_refused_runbook_link_is_said_and_not_carried() -> None:
    m = one(make("avg(cpu)", annotations={"runbook_url": "http://x.example.com/a"}))
    assert m.emit is None and fields.N_RUNBOOK_IGNORED in m.reasons


def test_a_rule_with_no_runbook_has_no_emit_options() -> None:
    assert one(make("avg(cpu)")).emit is None


def test_the_origin_note_comes_before_the_routing_note() -> None:
    m = one(make("avg(cpu)", labels={"team": "a"}))
    assert m.reasons.index(next(r for r in m.reasons if r.startswith("grafana origin:"))) < (
        m.reasons.index(t.N_ROUTING)
    )


# no data


def test_no_data_ok_is_noted() -> None:
    m = one(make("avg(cpu)", no_data_state="OK"))
    assert t.N_NODATA_OK in m.reasons and not m.no_data.notify


@pytest.mark.parametrize("state", ["NoData", "KeepLast"])
def test_the_other_states_do_not_say_ok(state: str) -> None:
    assert t.N_NODATA_OK not in one(make("avg(cpu)", no_data_state=state)).reasons


def test_no_data_alerting_is_loss_of_signal_for_the_pending_period() -> None:
    m = one(make("avg(cpu)", no_data_state="Alerting", for_=600))
    assert m.no_data.notify and m.no_data.timeout_minutes == 10


def test_a_paused_rule_is_created_disabled_and_says_so() -> None:
    m = one(make("avg(cpu)", is_paused=True))
    assert m.unmapped["enabled"] is False and t.N_PAUSED in m.reasons
