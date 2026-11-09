"""The re-instrumentation and synthetic monitor plans, and the commands that write them."""

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.dynatrace.inventory import models as m
from importer.sources.dynatrace.inventory import reasons as r
from importer.sources.dynatrace.inventory import reinstrument as ri
from importer.sources.dynatrace.inventory import synthetics as sy

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"
DATA = Path(ri.__file__).parent / "data"


def entity_plan() -> ri.ReinstrumentPlan:
    return ri.plan_reinstrumentation(m.load_entities(FIXTURES))


def synthetic_plan() -> sy.SyntheticsPlan:
    monitors = m.load_monitors(FIXTURES)
    return sy.plan_synthetics(monitors.items, m.load_locations(FIXTURES).items, monitors.skipped)


def test_entities_are_counted_per_type_with_their_new_relic_target() -> None:
    rows = {t.type: t for t in entity_plan().types}
    assert rows["HOST"].count == 5 and rows["HOST"].target == "New Relic infrastructure agent"
    assert rows["SERVICE"].count == 10
    assert rows["KUBERNETES_CLUSTER"].target == "New Relic Kubernetes integration"
    assert (
        rows["KUBERNETES_CLUSTER"].status == "DOCS"
        and rows["CLOUD_APPLICATION"].status == "UNVERIFIED"
    )
    assert r.I_TYPE in rows["WEIRD_TYPE"].reasons and rows["WEIRD_TYPE"].kind == "unmapped"
    assert r.I_PROCESS in rows["PROCESS_GROUP_INSTANCE"].reasons


def test_services_are_counted_per_technology_and_unmapped_ones_say_so() -> None:
    rows = {t.technology: t for t in entity_plan().technologies}
    assert rows["Java"].count == 2 and rows["Java"].target == "New Relic APM Java agent"
    assert rows["Java"].reasons == [] and rows["Spring"].reasons == [r.I_ROW]  # inferred row
    assert rows["DotNet"].target == "New Relic APM .NET agent"
    assert r.I_TECH in rows["COBOL runtime"].reasons and rows["COBOL runtime"].target == "none"
    assert entity_plan().services_without_technology == 1


def test_targets_count_agents_once_per_service_and_leave_out_what_has_no_path() -> None:
    targets = {t.target: t.count for t in entity_plan().targets}
    assert targets["New Relic APM Java agent"] == 2  # checkout, and cart with Java and Spring
    assert targets["New Relic APM .NET agent"] == 1  # three .NET names, one service
    assert targets["New Relic infrastructure agent"] == 5
    assert targets["New Relic Kubernetes integration"] == 3
    assert not any("none" in t.lower() and "OneAgent" in t for t in targets)


def test_hosts_are_counted_per_operating_system_and_unusual_ones_are_noted() -> None:
    plan = entity_plan()
    assert plan.hosts_by_os == {"LINUX": 3, "WINDOWS": 1, "AIX": 1}
    assert r.I_OS in plan.notes


def test_a_partial_read_is_stated() -> None:
    assert r.I_PARTIAL in entity_plan().notes  # the API reported 24, 23 were read


def test_what_has_no_migration_path_is_listed() -> None:
    text = " ".join(i["capability"] for i in entity_plan().no_path)
    for word in ("OneAgent", "PurePath", "Davis", "Smartscape"):
        assert word in text


def test_the_report_carries_the_counts_and_the_unverified_rows() -> None:
    report = ri.render_report(entity_plan())
    assert "| HOST | 5 |" in report and "Has no migration path" in report
    assert "UNVERIFIED" in report and "Java frameworks and servers" in report
    assert "## What to do" in report


@pytest.mark.parametrize(
    "file", ["technology_agents.json", "entity_types.json", "synthetic_locations.json"]
)
def test_every_data_row_has_a_status_of_docs_or_unverified(file: str) -> None:
    raw = json.loads((DATA / file).read_text())
    rows = [row for key, value in raw.items() if isinstance(value, list) for row in value]
    assert rows and all(row["status"] in ("DOCS", "UNVERIFIED") for row in rows)
    assert all(row.get("source") or file == "synthetic_locations.json" for row in rows)


def test_hostile_entities_are_counted_without_failing() -> None:
    entities = [
        m.Entity(type="X" * 500, technologies=["\x1b[31mred", "<script>"] * 3, os="a|b"),
        m.Entity(type="SERVICE", technologies=["`" * 10]),
    ]
    plan = ri.plan_reinstrumentation(m.Loaded[m.Entity](items=entities))
    report = ri.render_report(plan)
    assert "\x1b" not in report and "<script>" not in report and "a|b" not in report


# stem by monitor index in the fixture -> target, creatable, notes that must be present
EXPECTED = [
    ("SIMPLE", True, {r.S_REDIRECT, r.S_ALERT}),
    ("CERT_CHECK", True, set()),
    ("SCRIPT_API", False, {r.S_API_SCRIPT}),
    ("SCRIPT_API", False, {r.S_API_SCRIPT}),
    ("SCRIPT_API", False, {r.S_API_SCRIPT}),
    ("SCRIPT_API", False, {r.S_API_SCRIPT, r.S_PRE_POST, r.S_DISABLED}),
    ("BROWSER", True, set()),
    ("STEP_MONITOR", False, {r.S_STEP}),
    ("SCRIPT_BROWSER", False, {r.S_BROWSER_SCRIPT}),
    ("SCRIPT_BROWSER", False, {r.S_BROWSER_SCRIPT}),
    ("SCRIPT_API", False, {r.S_NETWORK}),
    ("SIMPLE", True, {r.S_FREQUENCY}),
]


@pytest.mark.parametrize(("index", "expected"), list(enumerate(EXPECTED)))
def test_each_monitor_gets_the_researched_new_relic_type(
    index: int, expected: tuple[str, bool, set[str]]
) -> None:
    target, creatable, texts = expected
    monitor = synthetic_plan().monitors[index]
    assert (monitor.target, monitor.creatable_from_json) == (target, creatable)
    assert texts <= {*monitor.reasons, *monitor.notes}


def test_frequencies_map_to_periods_and_odd_ones_round_up() -> None:
    plan = synthetic_plan().monitors
    assert [plan[0].period, plan[1].period, plan[11].period] == [
        "EVERY_5_MINUTES",
        "EVERY_HOUR",
        "EVERY_10_MINUTES",
    ]
    assert sy._period(7) == ("EVERY_10_MINUTES", [r.S_FREQUENCY])
    assert sy._period(5000)[0] == "EVERY_DAY"


def test_locations_map_by_city_then_country_and_say_when_they_cannot() -> None:
    plan = synthetic_plan().monitors
    first = plan[0].locations
    assert [(x.new_relic, x.status) for x in first] == [
        ("AWS_US_EAST_1", "DOCS"),
        ("AWS_EU_CENTRAL_1", "UNVERIFIED"),
    ]
    assert plan[5].locations[0].status == "PRIVATE" and plan[5].locations[0].reasons == [
        r.S_PRIVATE
    ]
    assert plan[11].locations[0].status == "UNMAPPED" and plan[11].locations[0].reasons == [
        r.S_LOCATION
    ]
    country = sy.map_location(m.Location(id="G-1", city="Tiny Town", country="DE"))
    assert country.new_relic == "AWS_EU_CENTRAL_1"
    assert sy.map_location(None).status == "UNMAPPED"


def test_devices_follow_the_dynatrace_device_and_orientation() -> None:
    plan = synthetic_plan().monitors
    assert plan[6].devices == ["DESKTOP"] and plan[7].devices == ["MOBILE_PORTRAIT"]
    assert sy.device_for(
        {"configuration": {"device": {"deviceName": "iPad Pro", "orientation": "landscape"}}}
    ) == ["TABLET_LANDSCAPE"]
    assert sy.device_for({}) == ["DESKTOP"] and plan[0].devices == []


def test_availability_thresholds_are_kept_as_numbers_not_text() -> None:
    first = synthetic_plan().monitors[0]
    assert (first.alert_consecutive_runs, first.alert_loading_ms) == (1, 3000)
    assert r.S_ALERT in first.notes


@pytest.mark.parametrize(
    ("script", "kind", "target"),
    [
        (
            {
                "requests": [
                    {
                        "url": "u",
                        "validation": {
                            "rules": [{"type": "patternConstraint", "passIfFound": False}]
                        },
                    }
                ]
            },
            "HTTP",
            "SCRIPT_API",
        ),
        (
            {
                "requests": [
                    {
                        "url": "u",
                        "validation": {
                            "rules": [{"type": "patternConstraint", "passIfFound": True}] * 2
                        },
                    }
                ]
            },
            "HTTP",
            "SCRIPT_API",
        ),
        (
            {"requests": [{"url": "u", "validation": {"rules": [{"type": "regexConstraint"}]}}]},
            "HTTP",
            "SCRIPT_API",
        ),
        ({"requests": [{"url": "u", "method": "HEAD"}]}, "HTTP", "SIMPLE"),
        ({"requests": [{"url": "u", "method": "PUT"}]}, "HTTP", "SCRIPT_API"),
        ({"requests": []}, "HTTP", "NONE"),
        ({}, "HTTP", "NONE"),
        ({"events": []}, "BROWSER", "NONE"),
        (
            {"type": "availability", "events": [{"type": "navigate"}, {"type": "click"}]},
            "BROWSER",
            "STEP_MONITOR",
        ),
        (
            {"type": "clickpath", "events": [{"type": "navigate"}, {"type": "javascript"}]},
            "BROWSER",
            "SCRIPT_BROWSER",
        ),
        ({}, "WEIRD", "NONE"),
    ],
)
def test_the_decision_table_edges(script: dict[str, Any], kind: str, target: str) -> None:
    monitor = m.Monitor(ref="r", type=kind, script=script)
    assert sy.plan_monitor(monitor, {}).target == target


def test_a_monitor_name_is_data_not_text_in_a_reason(tmp_path: Path) -> None:
    marker = "ZZMARKERZZ"
    monitor = m.Monitor(ref="r", name=f"| {marker} | <b>", type="HTTP", script={})
    plan = sy.SyntheticsPlan(monitors=[sy.plan_monitor(monitor, {})])
    assert all(marker not in x for p in plan.monitors for x in [*p.reasons, *p.notes])
    report = sy.render_report(plan)
    assert "| ZZMARKERZZ |" not in report and "<b>" not in report


def run(*args: str) -> Any:
    return CliRunner().invoke(app, list(args))


def test_the_reinstrumentation_command_writes_a_report_and_a_plan(tmp_path: Path) -> None:
    out = tmp_path / "o"
    result = run("plan-dynatrace-reinstrumentation", "--in", str(FIXTURES), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Planned 23 entities" in result.output
    plan = ri.ReinstrumentPlan.model_validate_json((out / "plan.json").read_text())
    assert plan.entities_read == 23 and (out / "report.md").is_file()


def test_the_synthetics_command_writes_a_report_and_a_plan(tmp_path: Path) -> None:
    out = tmp_path / "o"
    result = run("plan-dynatrace-synthetics", "--in", str(FIXTURES), "--out", str(out))
    assert result.exit_code == 0, result.output
    assert "Planned 12 monitors; 4 could be created" in result.output
    plan = sy.SyntheticsPlan.model_validate_json((out / "plan.json").read_text())
    assert len(plan.monitors) == 12 and plan.skipped == 2


@pytest.mark.parametrize(
    "command", ["plan-dynatrace-reinstrumentation", "plan-dynatrace-synthetics"]
)
def test_the_commands_refuse_empty_input_and_a_used_folder(command: str, tmp_path: Path) -> None:
    (tmp_path / "empty").mkdir()
    assert (
        run(command, "--in", str(tmp_path / "empty"), "--out", str(tmp_path / "a")).exit_code == 1
    )
    (tmp_path / "used").mkdir()
    (tmp_path / "used" / "x").write_text("1")
    result = run(command, "--in", str(FIXTURES), "--out", str(tmp_path / "used"))
    assert result.exit_code == 1 and "already has files" in result.output


def test_slos_are_counted_by_shape_and_by_whether_the_window_fits() -> None:
    plan = ri.plan_reinstrumentation(m.load_entities(FIXTURES), m.load_slos(FIXTURES))
    assert plan.slos == ri.SloSummary(
        custom=2, template=1, classic=1, rolling_window_fits=2, other_window=1
    )  # now-7d and -1w fit; now-30d does not; the template has no window
    report = ri.render_report(plan)
    assert "Window fits a New Relic rolling window (1, 7 or 28 days): 2" in report


@pytest.mark.parametrize(
    ("frame", "fits"),
    [("now-1d", True), ("-28d", True), ("-4w", True), ("-1w", True), ("now-30d", False),
     ("-2w", False), ("-1d/d", False), ("-1h", False), ("now-7d to now", False)],
)  # fmt: skip
def test_slo_windows_fit_only_when_they_are_one_seven_or_twenty_eight_days(
    frame: str, fits: bool
) -> None:
    loaded = m.Loaded[m.Slo](items=[m.Slo(kind="custom", timeframe=frame)])
    summary = ri.summarize_slos(loaded)
    assert (summary.rolling_window_fits, summary.other_window) == ((1, 0) if fits else (0, 1))


def test_a_folder_without_slos_gives_no_slo_section() -> None:
    plan = ri.plan_reinstrumentation(m.load_entities(FIXTURES), m.Loaded[m.Slo]())
    assert plan.slos is None and "## SLOs" not in ri.render_report(plan)
