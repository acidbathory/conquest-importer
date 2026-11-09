"""Policy routes to workflows: predicates, destinations per route, muting, limits, undecidable."""

import json
from datetime import date
from typing import Any

from hcl_schema import schema_errors
from importer.core.emit.notifications import notifications_terraform_result
from importer.core.ir import (
    Aggregation,
    Confidence,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NrTarget,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.core.notification_ir import (
    FILTER_LIMIT,
    MAX_WORKFLOWS,
    NotificationPlan,
    issue_filter_size,
    plan_json,
    plan_violations,
)
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.contact_points import read_contact_points
from importer.sources.grafana.notifications.models import NotificationExport
from importer.sources.grafana.notifications.policies import parse_policy_tree
from importer.sources.grafana.notifications.timings import parse_mute_timings
from importer.sources.grafana.notify_convert import convert as cv
from importer.sources.grafana.notify_convert import reasons as C
from importer.sources.grafana.notify_convert.destmap import parse_map

TODAY = date(2026, 11, 20)


def monitor(title: str, n: int, by: list[str] | None = None, key: str = "") -> IRMonitor:
    term = MetricTerm(
        metric="m",
        space_aggregation=Aggregation.AVG,
        nr_target=NrTarget(event="Metric", attribute="m"),
    )
    signal = MetricSignal(terms={"a": term}, aggregation=Aggregation.AVG, group_by=by or [])
    return IRMonitor(
        source_system="grafana",
        source_id=n,
        source_key=key or f"uid-{n}",
        source_type="alert rule",
        name=title,
        signal=Signal(
            kind=SignalKind.METRIC,
            raw_query="q",
            window_seconds=300,
            aggregation_window_seconds=60,
            metric=signal,
        ),
        thresholds=[Threshold(severity=Severity.CRITICAL, operator=Operator.ABOVE, value=5)],
        confidence=Confidence.APPROXIMATE,
    )


def email(name: str, address: str = "a@example.com", **kw: Any) -> dict[str, Any]:
    return {
        "name": name,
        "type": "email",
        "uid": f"u-{name}",
        "settings": {"addresses": address},
        **kw,
    }


def export(
    contact_points: list[dict[str, Any]],
    policy: dict[str, Any] | None,
    timings: list[dict[str, Any]] | None = None,
) -> NotificationExport:
    return NotificationExport(
        contact_points=read_contact_points(contact_points),
        policy=parse_policy_tree(policy) if policy is not None else None,
        mute_timings=parse_mute_timings(timings or []),
    )


def run(
    ex: NotificationExport,
    monitors: list[IRMonitor],
    rules: list[GrafanaRule] | None = None,
    map_text: str | None = None,
) -> cv.Conversion:
    return cv.convert_notifications(
        cv.Inputs(
            export=ex,
            monitors=monitors,
            rules=rules or [],
            dest_map=parse_map(map_text) if map_text else None,
            today=TODAY,
        )
    )


def workflows(c: cv.Conversion) -> list[Any]:
    return [p.workflow for p in c.plans if p.workflow is not None]


def names_of(c: cv.Conversion) -> list[list[str]]:
    return [w.condition_names for w in workflows(c)]


ROOT = {"receiver": "Ops"}


def test_the_root_receiver_gets_every_converted_rule_in_one_workflow() -> None:
    c = run(export([email("Ops")], ROOT), [monitor("A", 1), monitor("B", 2)])
    assert names_of(c) == [["A", "B"]]
    plan = c.plans[0]
    assert len(plan.typed_destinations) == 1 and plan.workflow is not None
    assert plan.workflow.destination_indexes == [0] and plan.workflow.predicates == []
    assert plan_violations(c.plans) == [] and c.violations == []
    assert c.counts.routes == 1 and c.counts.workflows == 1 and c.counts.rules_routed == 2


def test_a_child_route_by_alertname_takes_its_rule_and_the_root_gets_the_rest() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [{"receiver": "Pager", "matchers": ['alertname="B"']}],
    }
    pager = {
        "name": "Pager",
        "type": "pagerduty",
        "uid": "u-pd",
        "settings": {"integrationKey": "x"},
    }
    c = run(export([email("Ops"), pager], policy), [monitor("A", 1), monitor("B", 2)])
    by_receiver = {w.name.split("] ")[0]: w.condition_names for w in workflows(c)}
    assert sorted(names_of(c)) == [["A"], ["B"]]
    assert len(by_receiver) == 2


def test_a_query_label_becomes_a_tag_predicate_with_the_condition_name_filter_kept() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [{"receiver": "Pager", "matchers": ['service="api"']}],
    }
    pager = email("Pager", "p@example.com")
    c = run(export([email("Ops"), pager], policy), [monitor("A", 1, by=["service"])])
    wf = {tuple(p.attribute for p in w.predicates): w for w in workflows(c)}
    tagged = wf[("accumulations.conditionName", "accumulations.tag.service")]
    assert [(p.operator, p.values) for p in tagged.predicates] == [
        ("EXACTLY_MATCHES", ["A"]),
        ("EXACTLY_MATCHES", ["api"]),
    ]
    other = next(w for w in workflows(c) if w is not tagged)
    assert [(p.attribute, p.operator, p.values) for p in other.predicates] == [
        ("accumulations.conditionName", "EXACTLY_MATCHES", ["A"]),
        ("accumulations.tag.service", "DOES_NOT_EXACTLY_MATCH", ["api"]),
    ]


def test_a_static_label_is_undecidable_without_the_rules_and_decided_with_them() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [{"receiver": "Pager", "matchers": ['severity="critical"']}],
    }
    ex = export([email("Ops"), email("Pager", "p@example.com")], policy)
    blind = run(ex, [monitor("A", 1)])
    assert {u.reason for u in blind.undecidable} == {R.R_POLICY_UNDECIDABLE, R.R_POLICY_ORDER}
    assert blind.plans == [] or all(p.workflow is None for p in blind.plans)
    rule = GrafanaRule(title="A", uid="uid-1", folder="f", labels={"severity": "critical"})
    known = run(ex, [monitor("A", 1)], rules=[rule])
    assert known.undecidable == []
    assert [w.destination_indexes for w in workflows(known)] == [[0]]
    assert known.plans[0].typed_destinations[0].name.startswith("Imported: Pager")


def test_simplified_routing_skips_the_tree() -> None:
    policy = {"receiver": "Ops"}
    rule = GrafanaRule(title="A", uid="uid-1", notification_settings={"receiver": "Other"})
    ex = export([email("Ops"), email("Other", "o@example.com")], policy)
    c = run(ex, [monitor("A", 1)], rules=[rule])
    assert "Other" in c.plans[0].typed_destinations[0].name


def test_a_receiver_that_is_not_in_the_export_is_reported_and_creates_nothing() -> None:
    c = run(export([email("Ops")], {"receiver": "Gone"}), [monitor("A", 1)])
    assert c.plans == [] and R.R_POLICY_NO_RECEIVER not in c.reasons
    assert C.R_UNKNOWN_RECEIVER in c.reasons
    assert c.counts.rules_without_destination == 1


def test_no_policy_in_the_export_reports_every_rule_unrouted() -> None:
    c = run(export([email("Ops")], None), [monitor("A", 1)])
    assert c.plans == [] and c.unrouted == ["A"]


def test_rules_that_did_not_convert_are_counted_and_not_routed() -> None:
    manual = monitor("M", 2)
    manual.thresholds = []  # no threshold: not emittable
    c = run(export([email("Ops")], ROOT), [monitor("A", 1), manual])
    assert names_of(c) == [["A"]] and c.counts.rules_not_converted == 1


# --- destinations per route ---


def test_a_contact_point_used_by_two_routes_gets_its_own_destination_in_each() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [{"receiver": "Ops", "matchers": ['alertname="B"'], "mute_time_intervals": []}],
    }
    # two routes with the same receiver but different filters: B first, then the rest
    c = run(export([email("Ops")], policy), [monitor("A", 1), monitor("B", 2)])
    assert len(c.plans) == 1  # same receiver, same predicates, same mute set: one route
    policy2 = {
        "receiver": "Ops",
        "routes": [
            {"receiver": "Ops", "matchers": ['alertname="B"'], "mute_time_intervals": ["Nights"]}
        ],
    }
    timing = [{"name": "Nights", "time_intervals": [{"weekdays": ["saturday"]}]}]
    c2 = run(export([email("Ops")], policy2, timing), [monitor("A", 1), monitor("B", 2)])
    assert len(c2.plans) == 2
    dest_names = [d.name for p in c2.plans for d in p.typed_destinations]
    assert len(set(dest_names)) == len(dest_names) == 2
    topics = [p.topic for p in c2.plans]
    assert len(set(topics)) == 2 and plan_violations(c2.plans) == []


def test_several_integrations_with_one_name_are_all_attached_to_the_route() -> None:
    cps = [
        {**email("Ops"), "uid": "u1"},
        {
            "name": "Ops",
            "type": "webhook",
            "uid": "u2",
            "settings": {"url": "https://h.example.com/p"},
        },
    ]
    c = run(export(cps, ROOT), [monitor("A", 1)])
    assert len(c.plans[0].typed_destinations) == 2
    assert c.plans[0].workflow is not None
    assert c.plans[0].workflow.destination_indexes == [0, 1]


def test_resolve_messages_off_for_some_destinations_split_the_route_in_two_workflows() -> None:
    cps = [
        {**email("Ops"), "uid": "u1"},
        {**email("Ops", "b@example.com"), "uid": "u2", "disableResolveMessage": True},
    ]
    c = run(export(cps, ROOT), [monitor("A", 1)])
    flows = workflows(c)
    assert len(flows) == 2
    triggers = sorted(tuple(w.notification_triggers) for w in flows)
    assert triggers[0] == ()  # the default triggers, which include closed
    assert "CLOSED" not in triggers[1] and "ACTIVATED" in triggers[1]
    assert len({w.name for w in flows}) == 2


def test_manual_destinations_ride_on_the_plan_and_a_manual_only_route_has_no_workflow() -> None:
    slack = {
        "name": "Chat",
        "type": "slack",
        "uid": "u-s",
        "settings": {"recipient": "#x", "token": "[REDACTED]"},
    }
    c = run(export([slack], {"receiver": "Chat"}), [monitor("A", 1)])
    plan = c.plans[0]
    assert plan.workflow is None and plan.typed_destinations == []
    assert plan.manual_destinations[0].nr_type == "SLACK" and plan.needs_review
    assert C.R_NO_DESTINATION in c.reasons
    mixed = run(
        export([slack, email("Ops")], {"receiver": "Chat", "routes": []}), [monitor("A", 1)]
    )
    assert mixed.plans[0].workflow is None


def test_a_route_with_email_and_slack_keeps_the_email_workflow_and_lists_slack() -> None:
    cps = [
        email("Both"),
        {"name": "Both", "type": "slack", "uid": "u2", "settings": {"token": "[REDACTED]"}},
    ]
    c = run(export(cps, {"receiver": "Both"}), [monitor("A", 1)])
    plan = c.plans[0]
    assert plan.workflow is not None and len(plan.typed_destinations) == 1
    assert [m.nr_type for m in plan.manual_destinations] == ["SLACK"]


def test_skip_in_the_map_leaves_a_route_with_nothing_to_create() -> None:
    c = run(
        export([email("Ops")], ROOT),
        [monitor("A", 1)],
        map_text=json.dumps({"version": 1, "contact_points": {"Ops": {"skip": True}}}),
    )
    assert workflows(c) == [] and c.counts.skipped_points == 1
    assert C.R_NO_DESTINATION in c.reasons


def test_the_map_gives_an_email_contact_point_its_addresses() -> None:
    bare = {"name": "Ops", "type": "email", "uid": "u", "settings": {}}
    text = json.dumps({"version": 1, "contact_points": {"Ops": {"email": ["me@example.com"]}}})
    c = run(export([bare], ROOT), [monitor("A", 1)], map_text=text)
    assert c.plans[0].typed_destinations[0].properties == {"email": "me@example.com"}


# --- muting ---


NIGHTS = [
    {
        "name": "Nights",
        "time_intervals": [
            {"times": [{"start_time": "22:00", "end_time": "24:00"}], "weekdays": ["monday:friday"]}
        ],
    }
]


def timed_policy(mute: list[str]) -> dict[str, Any]:
    return {
        "receiver": "Ops",
        "routes": [
            {"receiver": "Night", "matchers": ['alertname="A"'], "mute_time_intervals": mute}
        ],
    }


def test_a_mute_timing_makes_a_muting_rule_and_the_workflow_strict() -> None:
    ex = export([email("Ops"), email("Night", "n@example.com")], timed_policy(["Nights"]), NIGHTS)
    c = run(ex, [monitor("A", 1), monitor("B", 2)])
    assert len(c.muting_rules) == 1
    assert c.muting_rules[0].condition.conditions[0].values == ["A"]
    strict = next(w for w in workflows(c) if w.condition_names == ["A"])
    other = next(w for w in workflows(c) if w.condition_names == ["B"])
    assert strict.muting_rules_handling == "DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES"
    assert other.muting_rules_handling == ""  # unchanged: NOTIFY_ALL_ISSUES by default
    assert C.R_MUTE_SCOPE in c.reasons


def test_an_unknown_mute_timing_is_reported_and_the_workflow_stays_unmuted() -> None:
    ex = export([email("Ops"), email("Night", "n@example.com")], timed_policy(["Gone"]), NIGHTS)
    c = run(ex, [monitor("A", 1)])
    assert c.muting_rules == [] and C.R_UNKNOWN_TIMING in c.reasons
    assert all(w.muting_rules_handling == "" for w in workflows(c))


def test_a_timing_that_cannot_be_converted_is_reported_with_its_reason() -> None:
    timings = [{"name": "Last", "time_intervals": [{"days_of_month": ["-1"]}]}]
    ex = export([email("Ops"), email("Night", "n@example.com")], timed_policy(["Last"]), timings)
    c = run(ex, [monitor("A", 1)])
    assert c.muting_rules == [] and R.R_MUTE_DAYS in c.reasons
    assert all(w.muting_rules_handling == "" for w in workflows(c))


def test_an_active_time_interval_is_reported_not_converted() -> None:
    policy = {
        "receiver": "Ops",
        "routes": [
            {"receiver": "Ops", "matchers": ['alertname="A"'], "active_time_intervals": ["Nights"]}
        ],
    }
    c = run(export([email("Ops")], policy, NIGHTS), [monitor("A", 1)])
    assert R.R_MUTE_ACTIVE in c.reasons and c.muting_rules == []


def test_mute_timings_no_route_uses_are_listed() -> None:
    c = run(export([email("Ops")], ROOT, NIGHTS), [monitor("A", 1)])
    assert c.unused_mute_timings == 1 and C.R_ORPHAN_MUTE in c.reasons


# --- limits ---


def test_a_long_condition_list_is_split_over_workflows_that_fit_the_filter_limit() -> None:
    monitors = [monitor(f"Condition number {i:05d} " + "x" * 60, i) for i in range(1, 301)]
    c = run(export([email("Ops")], ROOT), monitors)
    flows = workflows(c)
    assert len(flows) > 1
    assert all(issue_filter_size(w) <= FILTER_LIMIT for w in flows)
    assert sorted(n for w in flows for n in w.condition_names) == sorted(m.name for m in monitors)
    assert plan_violations(c.plans) == [] and len({w.name for w in flows}) == len(flows)
    assert len({p.topic for p in c.plans}) == len(c.plans)


def test_the_split_also_holds_with_tag_predicates() -> None:
    policy = {"receiver": "Ops", "routes": [{"receiver": "Ops", "matchers": ['service="api"']}]}
    monitors = [
        monitor(f"Condition number {i:05d} " + "y" * 60, i, by=["service"]) for i in range(1, 201)
    ]
    c = run(export([email("Ops")], policy), monitors)
    assert all(issue_filter_size(w) <= FILTER_LIMIT for w in workflows(c))
    assert all(w.predicates[0].attribute == "accumulations.conditionName" for w in workflows(c))


def test_more_workflows_than_an_account_allows_is_reported_in_the_violations() -> None:
    n = MAX_WORKFLOWS + 1
    routes = [{"receiver": f"R{i}", "matchers": [f'alertname="r{i}"']} for i in range(n)]
    cps = [email(f"R{i}") for i in range(n)]
    monitors = [monitor(f"r{i}", i + 1) for i in range(n)]
    c = run(export(cps, {"receiver": "R0", "routes": routes}), monitors)
    assert len(workflows(c)) == n
    assert any("allows 1000" in v for v in c.violations)


# --- output ---


def test_the_plans_survive_a_round_trip_and_the_terraform_is_valid() -> None:
    cps = [
        email("Ops"),
        {
            "name": "Hook",
            "type": "webhook",
            "uid": "u2",
            "settings": {"url": "https://h.example.com/p"},
        },
        {"name": "Pager", "type": "pagerduty", "uid": "u3", "settings": {"integrationKey": "k"}},
        {"name": "Disc", "type": "discord", "uid": "u4", "settings": {"url": "https://d/x"}},
    ]
    policy = {
        "receiver": "Ops",
        "routes": [
            {"receiver": "Hook", "matchers": ['alertname="B"']},
            {"receiver": "Pager", "matchers": ['alertname="C"']},
            {"receiver": "Disc", "matchers": ['alertname="D"']},
        ],
    }
    mons = [monitor(n, i) for i, n in enumerate("ABCD", start=1)]
    c = run(export(cps, policy), mons)
    for plan in c.plans:
        again = NotificationPlan.model_validate_json(plan_json(plan))
        assert again == plan
    result = notifications_terraform_result(c.plans)
    assert result.violations == [] and schema_errors(result.text) == []
    assert "nr_secret_grafana_" in result.text
    for ref in {s for p in c.points for s in p.secrets}:
        assert f'variable "nr_secret_{ref.lower()}"' in result.text


def test_no_name_or_address_reaches_a_note_a_reason_or_a_step() -> None:
    hostile = "HOSTILE `x` | <b>\n"
    cps = [email(hostile, "EMAILADDR@example.com")]
    c = run(export(cps, {"receiver": hostile}), [monitor("A", 1)])
    text = json.dumps([c.reasons, [p.notes for p in c.plans], [list(pt.steps) for pt in c.points]])
    assert "HOSTILE" not in text and "EMAILADDR" not in text
