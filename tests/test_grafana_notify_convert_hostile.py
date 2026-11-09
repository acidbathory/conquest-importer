"""Hostile and extreme input: names that look like markup or Terraform, every field of every type,
and a large estate. Nothing may raise, leak, or break an output format."""

import itertools
import time
from datetime import date
from typing import Any

import pytest

from hcl_schema import schema_errors
from importer.core.emit.muting import muting_terraform
from importer.core.emit.notifications import notifications_terraform_result
from importer.core.notification_ir import DestinationType, plan_json, validate_destination
from importer.sources.grafana.notifications.contact_points import read_contact_points
from importer.sources.grafana.notifications.models import ContactPoint, NotificationExport
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.schema import load_schema
from importer.sources.grafana.notifications.timings import parse_mute_timings
from importer.sources.grafana.notify_convert import convert as cv
from importer.sources.grafana.notify_convert import destinations as dst
from importer.sources.grafana.notify_convert.destmap import MapEntry
from importer.sources.grafana.notify_convert.report import render_report
from test_grafana_notify_convert_routes import monitor

TODAY = date(2026, 11, 20)
HOSTILE = [
    'x" ${var.y} %{if true}',
    "<script>alert(1)</script> | a | b",
    "`backtick` [link](http://x) \n newline \r\n",
    "‮ rtl \x1b[31m escape \x00 nul",
    "a" * 5000,
    "{{ .Labels.password }} {{ issueTitle }}",
    "../../etc/passwd",
    "'; DROP TABLE x; --",
]


def run(export: NotificationExport, monitors: list[Any]) -> cv.Conversion:
    return cv.convert_notifications(
        cv.Inputs(export=export, monitors=monitors, rules=[], today=TODAY)
    )


@pytest.mark.parametrize("name", HOSTILE, ids=range(len(HOSTILE)))
def test_a_hostile_name_everywhere_does_not_break_any_output(name: str) -> None:
    cps = [
        {"name": name, "type": "email", "uid": "u1", "settings": {"addresses": "a@example.com"}},
        {"name": name, "type": "slack", "uid": "u2", "settings": {"token": "[REDACTED]"}},
        {"name": name + "h", "type": "discord", "uid": "u3", "settings": {"url": "[REDACTED]"}},
    ]
    policy = {
        "receiver": name,
        "routes": [
            {"receiver": name + "h", "matchers": ['alertname="B"'], "mute_time_intervals": [name]}
        ],
    }
    timings = [{"name": name, "time_intervals": [{"weekdays": ["monday"]}]}]
    export = NotificationExport(
        contact_points=read_contact_points(cps),
        policy=parse_policy_tree(policy),
        mute_timings=parse_mute_timings(timings),
    )
    c = run(export, [monitor("A", 1), monitor("B " + name, 2), monitor(name, 3)])
    tf = notifications_terraform_result(c.plans).text + muting_terraform(c.muting_rules)
    # hcl_string writes ${ as $${ and %{ as %%{; the checker reads var.y inside a string as a
    # reference, so the escaped text is defused for it after the escapes are asserted
    assert "${var.y}" not in tf.replace("$${var.y}", "")
    assert "%{if" not in tf.replace("%%{if", "")
    assert schema_errors(tf.replace("var.y", "var_y")) == []
    report = render_report(c)
    for fragment in ("<script>", "`backtick`", "[link](", "\x1b", "\x00", "‮"):
        assert fragment not in report
    for plan in c.plans:
        assert len(plan.workflow.name if plan.workflow else "x") <= 120
        plan_json(plan)  # serialisable
        for d in plan.typed_destinations:
            assert "\n" not in d.name and len(d.name) <= 120
    for rule in c.muting_rules:
        assert "\n" not in rule.name and len(rule.name) <= 255
    for line in report.splitlines():
        if line.startswith("|"):
            assert line.count("|") >= 3  # a table row stays a row


# --- every field of every type ---


def every_type_cases() -> list[tuple[str, tuple[str, ...]]]:
    cases: list[tuple[str, tuple[str, ...]]] = []
    for type_name, spec in load_schema().items():
        names = tuple(f.name for f in spec.fields)
        cases += [(type_name, ()), (type_name, names)]
        cases += [(type_name, (n,)) for n in names]
    return cases


@pytest.mark.parametrize(("type_name", "fields"), every_type_cases())
def test_every_type_with_any_set_of_fields_converts_without_error(
    type_name: str, fields: tuple[str, ...]
) -> None:
    cp = ContactPoint(
        uid="u",
        name="n",
        type=type_name,
        fields_set=list(fields),
        url_host="hooks.example.com" if "url" in fields else "",
        addresses=["a@example.com"] if type_name == "email" else [],
        templates={"title": "custom"},
    )
    point = dst.convert_point(cp, dst.SecretNamer(), "Imported:", None)
    assert not (point.destination and point.manual)
    if point.destination is not None:
        validate_destination(point.destination)
        assert point.destination.type in DestinationType
    for text in (*point.steps, *point.reasons, *point.unverified):
        assert "hooks.example.com" not in text and "a@example.com" not in text


def test_every_type_with_every_map_entry_shape_never_raises() -> None:
    entries = [
        None,
        MapEntry(skip=True),
        MapEntry(email=["me@example.com"]),
        MapEntry(webhook_url="SOME_URL"),
        MapEntry(pagerduty_key="SOME_KEY"),
    ]
    for (type_name, _), entry in itertools.product(every_type_cases()[::7], entries):
        cp = ContactPoint(uid="u", name="n", type=type_name)
        dst.convert_point(cp, dst.SecretNamer(), "Imported:", entry)


# --- a large estate ---


def test_a_large_estate_converts_in_bounded_time() -> None:
    cps = [
        {
            "name": f"cp{i}",
            "type": "email",
            "uid": f"u{i}",
            "settings": {"addresses": "a@example.com"},
        }
        for i in range(300)
    ]
    routes = [
        {"receiver": f"cp{i}", "matchers": [f'team="t{i % 25}"', f'alertname=~"rule {i}.*"']}
        for i in range(300)
    ]
    export = NotificationExport(
        contact_points=read_contact_points(cps),
        policy=parse_policy_tree({"receiver": "cp0", "routes": routes}),
    )
    monitors = [monitor(f"rule {i}", i + 1) for i in range(500)]
    start = time.monotonic()
    c = run(export, monitors)
    assert time.monotonic() - start < 20
    assert c.counts.contact_points == 300 and c.counts.rules_routed <= 500
    assert c.violations == []


def test_the_secret_names_of_a_thousand_contact_points_are_distinct() -> None:
    namer = dst.SecretNamer()
    seen = {
        namer.ref(ContactPoint(uid="", name="same", type="webhook"), "URL").name
        for _ in range(1000)
    }
    assert len(seen) == 1000
