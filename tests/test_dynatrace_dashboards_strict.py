"""Round two: nothing is exact unless every clause keeps its meaning and nothing is dropped."""

from pathlib import Path

import pytest

from importer.sources.dynatrace.dashboards import models as m
from importer.sources.dynatrace.dashboards import reasons
from importer.sources.dynatrace.dashboards.dql_rules import analyse
from importer.sources.dynatrace.dashboards.plan_models import TilePlan, Verdict
from importer.sources.dynatrace.dashboards.tiles import classify_tile
from importer.sources.dynatrace.dashboards.variables import classify_variable

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"


def classify(query: str) -> tuple[Verdict, list[str]]:
    a = analyse(query)
    verdict = Verdict.MANUAL if a.blockers else Verdict.DRAFT if a.drafts else Verdict.EXACT
    return verdict, [*a.blockers, *a.drafts]


@pytest.mark.parametrize(
    ("query", "verdict", "reason"),
    [
        ("timeseries avg(cpu), interval: 1m", Verdict.DRAFT, reasons.D_INTERVAL),
        ("timeseries avg(cpu), from: now()-1h", Verdict.DRAFT, reasons.D_TIMEFRAME),
        ("timeseries avg(cpu), to: now()", Verdict.DRAFT, reasons.D_TIMEFRAME),
        ("timeseries avg(cpu), timeframe: 1h", Verdict.DRAFT, reasons.D_TIMEFRAME),
        ("timeseries avg(cpu), bins: 10", Verdict.MANUAL, reasons.PARAMETER),
        ("timeseries avg(cpu), nonempty: true", Verdict.MANUAL, reasons.PARAMETER),
        ("timeseries avg(cpu), union: true", Verdict.MANUAL, reasons.PARAMETER),
        ("timeseries avg(m, rate: 1m)", Verdict.DRAFT, reasons.D_RATE),
        ("timeseries avg(m, rate: weird)", Verdict.DRAFT, reasons.D_OPTION),
        ("timeseries avg(m, scalar: true)", Verdict.MANUAL, reasons.SCALAR),
        ("timeseries sum(m, default: 0)", Verdict.DRAFT, reasons.D_OPTION),
        ("timeseries avg(m, 5)", Verdict.MANUAL, reasons.AGGREGATION),
        ("timeseries percentile(m, 95)", Verdict.EXACT, None),
        ("fetch security.events | summarize count(), by:{host}", Verdict.EXACT, None),
        (
            "fetch security.events | summarize count(), by:{bin(timestamp, 1h)}",
            Verdict.MANUAL,
            reasons.COMPUTED,
        ),
        (
            "fetch security.events | summarize toDouble(sum(x)) / count() * 100",
            Verdict.MANUAL,
            reasons.COMPUTED,
        ),
        ("fetch security.events | summarize p = sum(x) + 1", Verdict.MANUAL, reasons.COMPUTED),
        (
            "fetch security.events | summarize count(), nonempty: true",
            Verdict.MANUAL,
            reasons.PARAMETER,
        ),
        ("fetch security.events | fields a = b + c", Verdict.MANUAL, reasons.COMPUTED),
        ("fetch security.events | fieldsKeep a = b * 100", Verdict.MANUAL, reasons.COMPUTED),
        ("fetch security.events | fieldsRename n = o", Verdict.EXACT, None),
        ("fetch security.events | fields a, `b c`", Verdict.EXACT, None),
        ("timeseries avg(cpu) | sort arrayAvg(x) desc", Verdict.MANUAL, reasons.SORT_EXPR),
        ("timeseries v = avg(cpu) | sort arrayAvg(v) desc", Verdict.DRAFT, reasons.D_SORT_SERIES),
        ("timeseries avg(cpu) | sort a + b", Verdict.MANUAL, reasons.SORT_EXPR),
        ('timeseries avg(cpu) | sort {a, direction: "descending"}', Verdict.EXACT, None),
        ("timeseries avg(cpu) | limit 10 | sort x", Verdict.MANUAL, reasons.REORDER),
        ("timeseries avg(cpu) | limit 10 | filter a == 1", Verdict.MANUAL, reasons.REORDER),
        ("timeseries avg(cpu) | limit 10 | summarize count()", Verdict.MANUAL, reasons.REORDER),
        ("timeseries avg(cpu) | limit 10 | fields a", Verdict.EXACT, None),
        ("timeseries avg(cpu) | sort x | limit 10", Verdict.EXACT, None),
        ('timeseries avg(cpu), filter:{contains(a, "x")}', Verdict.DRAFT, reasons.D_MATCH),
        ('timeseries avg(cpu), filter:{startsWith(a, "x")}', Verdict.DRAFT, reasons.D_MATCH),
        ('timeseries avg(cpu), filter:{endsWith(a, "x")}', Verdict.DRAFT, reasons.D_MATCH),
        ('timeseries avg(cpu), filter:{in(a, "x")}', Verdict.EXACT, None),
        ("fetch security.events | filterOut isNull(a) OR isNull(b)", Verdict.EXACT, None),
        (
            "fetch security.events | filterOut dt.smartscape_source.id == 1",
            Verdict.MANUAL,
            reasons.FILTER,
        ),
        ('timeseries avg(cpu), filter:{name == "dt.smartscape_source.id"}', Verdict.EXACT, None),
        ('timeseries avg(cpu), filter:{name == "$PrimaryTag"}', Verdict.EXACT, None),
        (
            "timeseries avg(cpu), filter:{a == 1 or dt.smartscape_source.id == 2}",
            Verdict.DRAFT,
            reasons.D_ENTITY,
        ),
        (
            "timeseries avg(cpu), filter:{foo(x) or dt.smartscape_source.id == 2}",
            Verdict.MANUAL,
            reasons.FILTER,
        ),
        (
            'timeseries avg(cpu), filter:{$v == "*" or region == $v}',
            Verdict.DRAFT,
            reasons.D_SELECT_ALL,
        ),
        ("|", Verdict.MANUAL, reasons.Q_EMPTY),
        ("// TODO write query", Verdict.MANUAL, reasons.Q_EMPTY),
        ("/* c */", Verdict.MANUAL, reasons.Q_EMPTY),
        ("  \n | | // only separators", Verdict.MANUAL, reasons.Q_EMPTY),
        ("", Verdict.MANUAL, reasons.Q_EMPTY),
    ],
)
def test_strict_query_rules(query: str, verdict: Verdict, reason: str | None) -> None:
    got, found = classify(query)
    assert got is verdict
    assert (reason in found) if reason else not found


def test_the_select_all_variable_is_remembered() -> None:
    a = analyse('timeseries avg(cpu), filter:{$v == "*" or region == $v}')
    assert a.sentinel_variables == ["v"] and a.variables == ["v"]


@pytest.mark.parametrize(
    ("metric", "label"),
    [
        ("dt.host.cpu.usage", "Dynatrace OneAgent host metrics"),
        ("dt.service.request.count", "Dynatrace OneAgent service metrics"),
        ("dt.process.cpu.usage", "Dynatrace OneAgent process metrics"),
        ("dt.cloud.aws.x", "Dynatrace cloud metrics"),
        ("dt.sfm.x", "Dynatrace self-monitoring metrics"),
    ],
)
def test_dynatrace_only_metric_prefixes_are_flagged(metric: str, label: str) -> None:
    assert analyse(f"timeseries avg({metric})").dynatrace_only == [label]


@pytest.mark.parametrize(
    "metric", ["cloud.aws.firehose.IncomingRecords", "cloud.azure.x.y", "my.metric"]
)
def test_cloud_integration_metrics_are_not_flagged(metric: str) -> None:
    assert analyse(f"timeseries avg({metric})").dynatrace_only == []


def test_an_entity_fetch_is_flagged_and_manual() -> None:
    a = analyse("fetch dt.entity.host | fields id")
    assert a.dynatrace_only == ["Dynatrace entities"] and reasons.SRC_OTHER in a.blockers


def tile_plan(extra: dict[str, object]) -> TilePlan:
    raw = {"type": "data", "query": "timeseries avg(cpu)", "visualization": "lineChart", **extra}
    parsed = m.parse_tile("t", raw, None)
    assert isinstance(parsed, m.Tile)
    return classify_tile(parsed)


def test_a_tile_timeframe_that_is_on_makes_a_draft() -> None:
    plan = tile_plan({"timeframe": {"tileTimeframeEnabled": True}})
    assert plan.verdict is Verdict.DRAFT and reasons.D_TILE_TIMEFRAME in plan.notes
    assert tile_plan({"timeframe": {"tileTimeframeEnabled": False}}).verdict is Verdict.EXACT


def test_a_segment_filter_makes_a_draft() -> None:
    plan = tile_plan({"segments": {"tileSegmentsEnabled": True}})
    assert plan.verdict is Verdict.DRAFT and reasons.D_SEGMENTS in plan.notes
    assert tile_plan({"segments": {"tileSegmentsEnabled": False}}).verdict is Verdict.EXACT


def test_display_settings_are_noted_but_do_not_change_the_verdict() -> None:
    plan = tile_plan({"visualizationSettings": {"thresholds": [{"id": 1}]}})
    assert plan.verdict is Verdict.EXACT and reasons.N_SETTINGS in plan.notes
    assert reasons.N_SETTINGS not in tile_plan({}).notes
    assert reasons.N_SETTINGS in tile_plan({"visualizationSettings": {"coloring": {"x": 1}}}).notes


def test_the_select_all_default_and_multiple_selection_are_read() -> None:
    (doc,) = m.read_file(FIXTURES / "kubernetes-statistics.json")
    assert doc.platform is not None
    cluster = next(v for v in doc.platform.variables if v.key == "Cluster")
    plan = classify_variable(cluster, set())
    assert plan.select_all and plan.multiple
    assert not classify_variable(m.Variable(key="v", type="text", default="10"), set()).select_all
    assert classify_variable(m.Variable(key="v", type="text", default="*"), set()).select_all


@pytest.mark.parametrize(
    "text",
    [
        "",
        "|",
        "// c",
        'data record(a="*") | append [ ]',
        'data record(a="*") | append [ // c ]',
        'data record(a="*") | append [|]',
    ],
)
def test_a_variable_query_with_nothing_in_it_is_manual_not_a_crash(text: str) -> None:
    plan = classify_variable(m.Variable(key="v", type="query", input=text), set())
    assert plan.verdict is Verdict.MANUAL and plan.reasons == [reasons.VAR_SHAPE]


def test_many_operands_collect_variables_in_linear_time() -> None:
    import time

    query = "timeseries avg(cpu), filter:{" + " and ".join(f"a == $v{i}" for i in range(4000)) + "}"
    started = time.monotonic()
    a = analyse(query)
    assert time.monotonic() - started < 2.0 and len(a.variables) == 4000
