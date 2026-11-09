"""Round two for synthetics, the re-instrumentation plan and markdown flags."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.dynatrace.dashboards import models as dm
from importer.sources.dynatrace.dashboards import reasons as dr
from importer.sources.dynatrace.dashboards.dql_rules import analyse
from importer.sources.dynatrace.dashboards.dql_scan import MAX_QUERY
from importer.sources.dynatrace.dashboards.markdown_tiles import read_markdown
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report
from importer.sources.dynatrace.dashboards.tiles import WIDGETS, layout_plan
from importer.sources.dynatrace.inventory import extract as ix
from importer.sources.dynatrace.inventory import models as im
from importer.sources.dynatrace.inventory import reasons as r
from importer.sources.dynatrace.inventory import reinstrument as ri
from importer.sources.dynatrace.inventory import synthetics as sy

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"


def http(request: dict[str, Any], **monitor: Any) -> sy.MonitorPlan:
    script = {
        "version": "1.0",
        "requests": [{"url": "https://x.invalid/", "method": "GET", **request}],
    }
    return sy.plan_monitor(im.Monitor(ref="r", type="HTTP", script=script, **monitor), {})


@pytest.mark.parametrize(
    "request_fields",
    [
        {"authentication": {"type": "BASIC"}},
        {"authentication": "<omitted>"},
        {"clientCertificate": "x"},
        {
            "validation": {
                "rules": [{"type": "patternConstraint", "passIfFound": True, "regex": True}]
            }
        },
        {"validation": {"rules": [{"type": "regexConstraint", "passIfFound": True}]}},
    ],
)
def test_authentication_certificates_and_regex_rules_need_a_script(
    request_fields: dict[str, Any],
) -> None:
    plan = http(request_fields)
    assert (plan.target, plan.creatable_from_json) == ("SCRIPT_API", False)
    assert plan.reasons == [r.S_AUTH]


def test_a_get_with_the_default_status_rule_is_a_simple_monitor() -> None:
    default = {"type": "httpStatusesList", "passIfFound": False, "value": ">=400"}
    plan = http({"validation": {"rules": [default]}})
    assert (plan.target, plan.creatable_from_json) == ("SIMPLE", True)
    spaced = {**default, "value": ">= 400"}
    assert http({"validation": {"rules": [spaced]}}).target == "SIMPLE"
    with_text = http(
        {"validation": {"rules": [default, {"type": "patternConstraint", "passIfFound": True}]}}
    )
    assert with_text.target == "SIMPLE"


@pytest.mark.parametrize(
    "rule",
    [
        {"type": "httpStatusesList", "passIfFound": True, "value": ">=400"},
        {"type": "httpStatusesList", "passIfFound": False, "value": ">=500"},
        {"type": "patternConstraint", "passIfFound": False},
    ],
)
def test_other_rules_still_need_a_script(rule: dict[str, Any]) -> None:
    assert http({"validation": {"rules": [rule]}}).target == "SCRIPT_API"


@pytest.mark.parametrize("value", [0, -5, "5", True, None, 5.5])
def test_a_frequency_that_is_not_a_positive_whole_number_is_missing(value: object) -> None:
    monitor = im.parse_monitor("r", {"type": "HTTP", "frequencyMin": value})
    assert monitor is not None and monitor.frequency_min is None
    assert sy.plan_monitor(monitor, {}).period == ""


def test_country_codes_compare_in_either_case_and_guesses_carry_a_reason() -> None:
    lower = sy.map_location(im.Location(id="G-1", city="frankfurt", country="de"))
    assert (lower.new_relic, lower.status, lower.reasons) == ("AWS_EU_CENTRAL_1", "DOCS", [])
    city = sy.map_location(im.Location(id="G-1", city="Ashburn", country="us"))
    assert (city.status, city.reasons) == ("UNVERIFIED", [r.S_LOCATION_ROW])
    fallback = sy.map_location(im.Location(id="G-1", city="Tiny Town", country="de"))
    assert (fallback.new_relic, fallback.status) == ("AWS_EU_CENTRAL_1", "UNVERIFIED")
    assert fallback.reasons == [r.S_LOCATION_ROW]
    assert sy.map_location(im.Location(id="G-1", city="x", country="xx")).status == "UNMAPPED"


def test_the_location_table_marks_only_frankfurt_as_documented() -> None:
    table = json.loads((Path(ri.__file__).parent / "data" / "synthetic_locations.json").read_text())
    documented = {row["city"] for row in table["cities"] if row["status"] == "DOCS"}
    assert documented == {"Frankfurt"}
    assert all(row["status"] == "UNVERIFIED" for row in table["countries"])


def plan_of(*technologies: list[str], types: tuple[str, ...] = ()) -> ri.ReinstrumentPlan:
    entities = [im.Entity(type="SERVICE", technologies=t) for t in technologies]
    entities += [im.Entity(type=t) for t in types]
    return ri.plan_reinstrumentation(im.Loaded[im.Entity](items=entities))


def test_a_target_is_unverified_when_any_row_naming_it_is() -> None:
    plan = plan_of(
        ["Java"], ["Spring"], ["DotNet"], types=("HOST", "KUBERNETES_CLUSTER", "CLOUD_APPLICATION")
    )
    status = {t.target: t.status for t in plan.targets}
    assert status["New Relic APM Java agent"] == "UNVERIFIED"  # the frameworks row is
    assert status["New Relic APM .NET agent"] == "UNVERIFIED"  # the IIS row is
    assert status["New Relic infrastructure agent"] == "DOCS"
    assert status["New Relic Kubernetes integration"] == "UNVERIFIED"  # cloud applications are


@pytest.mark.parametrize(
    ("spelled", "target"),
    [
        ("APACHE_TOMCAT", "New Relic APM Java agent"),
        ("ASP_DOTNET", "New Relic APM .NET agent"),
        ("IIS_APP_POOL", "New Relic APM .NET agent"),
        ("NODE_JS", "New Relic APM Node.js agent"),
        ("Apache-Tomcat", "New Relic APM Java agent"),
    ],
)
def test_other_spellings_are_mapped_and_marked_unverified(spelled: str, target: str) -> None:
    (row,) = plan_of([spelled]).technologies
    assert row.target == target and row.status == "UNVERIFIED"
    assert r.I_SPELLING in row.reasons


def test_the_spelling_as_written_in_the_table_is_not_marked() -> None:
    for written in ("Python", "PYTHON", " python "):  # case and edge spaces do not matter
        (row,) = plan_of([written]).technologies
        assert row.status == "DOCS" and row.reasons == []


def test_one_service_counts_a_technology_once_however_it_is_spelled() -> None:
    plan = plan_of(["Java", "JAVA", "java"], ["Java"])
    (row,) = plan.technologies
    assert row.count == 2 and row.technology == "Java"
    assert {t.target: t.count for t in plan.targets}["New Relic APM Java agent"] == 2


def test_azure_is_documented_only_for_app_service_plans() -> None:
    assert ri.target_for_type("AZURE_MICROSOFT_WEB_SERVERFARMS") is not None
    assert ri.target_for_type("AZURE_MICROSOFT_WEB_SERVERFARMS").status == "DOCS"  # type: ignore[union-attr]
    other = ri.target_for_type("AZURE_MICROSOFT_SQL_SERVERS")
    assert other is not None and other.status == "UNVERIFIED"
    assert ri.target_for_type("AWS_LAMBDA_FUNCTION").status == "UNVERIFIED"  # type: ignore[union-attr]


def test_the_entity_table_has_no_row_the_extractor_never_asks_for() -> None:
    table = json.loads((Path(ri.__file__).parent / "data" / "entity_types.json").read_text())
    exact_types = {row["type"] for row in table["types"] if not row["type"].startswith("AZURE_")}
    assert exact_types <= set(ix.ENTITY_TYPES)
    assert "CLOUD_APPLICATION" in ix.ENTITY_TYPES
    for gone in ("PROCESS_GROUP_INSTANCE", "SYNTHETIC_TEST", "HTTP_CHECK"):
        assert gone not in exact_types


def test_unverified_rows_add_a_note_to_their_type_and_technology_rows() -> None:
    plan = plan_of(["Spring"], types=("CLOUD_APPLICATION",))
    assert r.I_ROW in plan.technologies[0].reasons
    assert r.I_ROW in next(t for t in plan.types if t.type == "CLOUD_APPLICATION").reasons


def test_hostile_types_and_technologies_cannot_break_the_report() -> None:
    plan = plan_of(["a|b<script>x</script>"], types=("X|Y<img src=x onerror=1>",))
    report = ri.render_report(plan)
    for raw in ("a|b", "X|Y", "<script>", "<img"):
        assert raw not in report


@pytest.mark.parametrize(
    "text",
    [
        "[a](javascript:alert(1))",
        "[a](java\tscript:alert(1))",
        "[a](  javascript:x)",
        "[a](JaVaScRiPt:x)",
        "[a](&#106;avascript:x)",
        "[a](&#x6A;avascript:x)",
        "[a](jav&Tab;ascript:x)",
        "[a](data:text/html;base64,AAAA)",
        "[a](vbscript:x)",
        "[ref]: javascript:alert(1)",
        "<javascript:alert(1)>",
        '<a href="javascript:x">y</a>',
        "<img src='data:image/svg+xml,x'>",
    ],
)
def test_unsafe_links_are_flagged_in_every_spelling(text: str) -> None:
    assert "unsafe-link" in read_markdown(text)[2]


@pytest.mark.parametrize(
    "text",
    [
        '<a href="https://x.invalid">y</a>',
        "<script>alert(1)</script>",
        "<iframe src=x></iframe>",
        "<IMG src=x>",
        "< a href=x>",
        "<svg onload=x>",
    ],
)
def test_raw_html_is_flagged(text: str) -> None:
    assert "html" in read_markdown(text)[2]


@pytest.mark.parametrize(
    "text",
    [
        "[x](https://abc.live.dynatrace.com/ui)",
        "[x](https://abc.sprint.dynatracelabs.com/)",
        "[x](https://abc.apps.dynatrace.com/ui/apps/x)",
        "[x](https://dt-url.net/abc)",
        "[x](/ui/apps/dynatrace.clouds)",
        "[x]: https://abc.live.dynatrace.com/x",
        '<a href="https://dt-url.net/q">y</a>',
    ],
)
def test_dynatrace_links_are_flagged(text: str) -> None:
    assert "dynatrace-link" in read_markdown(text)[2]


@pytest.mark.parametrize(
    "text",
    [
        "plain text",
        "[docs](https://docs.dynatrace.com/docs/x)",
        "[aws](https://docs.aws.amazon.com/x)",
        "# Heading\n\n- item",
        "[mail](mailto:a@b.invalid)",
    ],
)
def test_clean_markdown_has_no_flags(text: str) -> None:
    assert read_markdown(text)[2] == []


def test_images_from_the_dynatrace_cdn_are_flagged_and_the_report_says_what_the_check_means() -> (
    None
):
    assert "image" in read_markdown("![](https://dt-cdn.net/images/x.png)")[2]
    assert "image" not in read_markdown("![](https://example.invalid/x.png)")[2]
    doc = dm.parse_document(
        "d", {"tiles": {"1": {"type": "markdown", "content": "<script>x</script>"}}}
    )
    report = render_report(plan_documents([doc]))
    assert "unsafe-link or html flag: 1 of 1" in report and "not a proof" in report


def test_markdown_scanning_stays_fast_on_hostile_text() -> None:
    import time

    started = time.monotonic()
    for text in ("](" * 40_000, "<a:" * 30_000, "![" * 50_000, "[a]: " * 20_000, "&#" * 40_000):
        read_markdown(text)
    assert time.monotonic() - started < 3.0


def test_the_query_size_limit_is_exact() -> None:
    padding = " " * (MAX_QUERY - len("timeseries avg(m)"))
    assert dr.Q_UNREADABLE not in analyse("timeseries avg(m)" + padding).blockers
    assert dr.Q_UNREADABLE in analyse("timeseries avg(m)" + padding + " ").blockers


@pytest.mark.parametrize(
    ("x", "w", "y", "h", "expected"),
    [
        (0, 24, 0, 1, (1, 12, 1, 1)),
        (12, 12, 7, 7, (7, 6, 8, 4)),
        (5, 5, 3, 3, (4, 2, 4, 2)),
        (1, 1, 0, 2, (2, 1, 1, 1)),
        (2, 2, 0, 5, (2, 1, 1, 3)),
        (3, 4, 0, 4, (3, 2, 1, 2)),
    ],
)
def test_the_layout_rounding_is_half_up_and_keeps_one_column(
    x: int, w: int, y: int, h: int, expected: tuple[int, int, int, int]
) -> None:
    plan = layout_plan(dm.Layout(x=x, y=y, w=w, h=h))
    assert plan is not None and (plan.column, plan.width, plan.row, plan.height) == expected


@pytest.mark.parametrize(
    ("visualization", "widget"),
    [
        ("lineChart", "viz.line"),
        ("areaChart", "viz.area"),
        ("singleValue", "viz.billboard"),
        ("table", "viz.table"),
        ("donutChart", "viz.pie"),
        ("pieChart", "viz.pie"),
        ("categoricalBarChart", "viz.bar"),
    ],
)
def test_each_supported_visualization_has_its_widget(visualization: str, widget: str) -> None:
    assert WIDGETS[visualization] == widget
