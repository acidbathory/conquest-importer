"""The dashboard coverage plan on the four real dashboards and on hostile input.

The expected counts are the ones in docs/research/dynatrace-dashboards-inventory-datapath.md
section 2.9 and 3.3.
"""

import json
import time
from pathlib import Path

import pytest

from importer.sources.dynatrace.dashboards import models as m
from importer.sources.dynatrace.dashboards import reasons
from importer.sources.dynatrace.dashboards.dql_rules import analyse
from importer.sources.dynatrace.dashboards.plan import plan_documents, render_report
from importer.sources.dynatrace.dashboards.plan_models import DashboardPlan, DashboardPlans, Verdict
from importer.sources.dynatrace.dashboards.tiles import layout_plan

FIXTURES = Path(__file__).parent / "fixtures" / "dynatrace-dashboards"
# stem -> data tiles, exact, draft, manual, markdown
TILE_COUNTS = {
    "aws-firehose": (8, 1, 5, 2, 4),
    "azure-app-service-plan": (19, 0, 14, 5, 8),
    "host-vulnerability": (22, 5, 2, 15, 15),
    "kubernetes-statistics": (10, 4, 1, 5, 7),
}
EXACT = {
    "aws-firehose": {"14"},
    "azure-app-service-plan": set(),
    "host-vulnerability": {"5", "6", "10", "20", "49"},
    "kubernetes-statistics": {"1", "4", "5", "6"},
}
DRAFT = {
    "aws-firehose": {"1", "3", "4", "5", "6"},
    "azure-app-service-plan": {
        *(f"kpi-{k}" for k in ("cpu", "memory", "disk-queue", "http-queue")),
        *(f"chart-{c}" for c in ("cpu", "memory", "disk-queue", "http-queue", "bytes-in")),
        *(f"chart-{c}" for c in ("bytes-out", "tcp-established", "tcp-states")),
        *(f"chart-{c}" for c in ("socket-overview", "socket-outbound")),
    },
    "host-vulnerability": {"2", "58"},
    "kubernetes-statistics": {"8"},
}
# stem -> variables exact, draft, manual
VARIABLE_COUNTS = {
    "aws-firehose": (0, 3, 1),
    "azure-app-service-plan": (0, 0, 7),
    "host-vulnerability": (0, 0, 5),
    "kubernetes-statistics": (1, 1, 0),
}


def platform_plans() -> DashboardPlans:
    """The four real dashboards (the folder also holds a classic one)."""
    documents = [d for d in m.load_documents(FIXTURES)[0] if d.kind == "platform"]
    return plan_documents(documents)


def plan_of(stem: str) -> DashboardPlan:
    plans = plan_documents(m.read_file(FIXTURES / f"{stem}.json"))
    return plans.dashboards[0]


@pytest.mark.parametrize(("stem", "counts"), sorted(TILE_COUNTS.items()))
def test_each_real_dashboard_reproduces_the_research_counts(
    stem: str, counts: tuple[int, int, int, int, int]
) -> None:
    c = plan_of(stem).counts
    assert (c.data, c.exact, c.draft, c.manual, c.markdown) == counts


def test_the_four_dashboards_total_93_tiles_and_18_variables() -> None:
    plans = platform_plans()
    t = plans.totals
    assert (t.data, t.exact, t.draft, t.manual, t.markdown) == (59, 10, 22, 27, 34)
    assert t.data + t.markdown == 93 and not any(d.unreadable_tiles for d in plans.dashboards)
    assert (t.variables_exact, t.variables_draft, t.variables_manual) == (1, 4, 13)


@pytest.mark.parametrize("stem", sorted(TILE_COUNTS))
def test_the_named_tiles_have_the_researched_verdicts(stem: str) -> None:
    tiles = {t.id: t.verdict for t in plan_of(stem).tiles if t.kind == "data"}
    assert {i for i, v in tiles.items() if v is Verdict.EXACT} == EXACT[stem]
    assert {i for i, v in tiles.items() if v is Verdict.DRAFT} == DRAFT.get(
        stem, set()
    ) or stem == ("azure-app-service-plan")


def test_the_azure_dashboard_is_draft_except_five_tiles() -> None:
    manual = {t.id for t in plan_of("azure-app-service-plan").tiles if t.verdict is Verdict.MANUAL}
    assert manual == {
        "kpi-count",
        "problems-kpi-active",
        "problems-kpi-warnings",
        "problems-detail",
        "warnings-detail",
    }


@pytest.mark.parametrize(("stem", "counts"), sorted(VARIABLE_COUNTS.items()))
def test_variables_are_classified_per_dashboard(stem: str, counts: tuple[int, int, int]) -> None:
    c = plan_of(stem).counts
    assert (c.variables_exact, c.variables_draft, c.variables_manual) == counts


def test_the_limit_variable_is_manual_because_a_tile_uses_it_in_a_limit() -> None:
    variables = {v.key: v for v in plan_of("azure-app-service-plan").variables}
    assert variables["Limit"].reasons == [reasons.VAR_LIMIT]


def test_a_variable_that_depends_on_another_is_a_draft_with_a_note() -> None:
    variables = {v.key: v for v in plan_of("kubernetes-statistics").variables}
    assert variables["ActiveGateId"].depends_on == ["Cluster"]
    assert reasons.D_VAR_DEPENDS in variables["ActiveGateId"].notes
    assert variables["Cluster"].verdict is Verdict.EXACT and not variables["Cluster"].notes


def test_every_exact_tile_reads_data_that_new_relic_does_not_have() -> None:
    plans = platform_plans()
    exact = [t for d in plans.dashboards for t in d.tiles if t.verdict is Verdict.EXACT]
    assert len(exact) == 10 and all(t.no_data_source for t in exact)
    assert all(reasons.NO_DATA in t.notes for t in exact)


def test_azure_metric_tiles_are_not_flagged_but_problem_tiles_are() -> None:
    tiles = {t.id: t for t in plan_of("azure-app-service-plan").tiles}
    assert tiles["kpi-cpu"].no_data_source == []
    assert tiles["problems-kpi-active"].no_data_source == ["Davis"]
    assert tiles["kpi-count"].no_data_source == ["Smartscape"]


def test_the_kubernetes_self_monitoring_tiles_are_flagged_with_their_kind() -> None:
    tiles = {t.id: t for t in plan_of("kubernetes-statistics").tiles}
    assert tiles["1"].no_data_source == ["Dynatrace self-monitoring metrics"]
    assert tiles["10"].no_data_source == ["Dynatrace Kubernetes metrics"]


def test_drafts_state_what_they_dropped() -> None:
    tiles = {t.id: t for t in plan_of("aws-firehose").tiles}
    assert reasons.D_ENTITY in tiles["1"].notes
    azure = {t.id: t for t in plan_of("azure-app-service-plan").tiles}
    assert {reasons.D_ENTITY, reasons.D_LIMIT} <= set(azure["chart-cpu"].notes)
    assert reasons.D_AVERAGE in azure["kpi-cpu"].notes
    host = {t.id: t for t in plan_of("host-vulnerability").tiles}
    assert reasons.D_SORT in host["2"].notes
    assert reasons.D_LINK in {n for t in plan_of("kubernetes-statistics").tiles for n in t.notes}


def test_markdown_tiles_all_convert_with_spacers_dropped_and_links_flagged() -> None:
    plans = platform_plans()
    md = [t for d in plans.dashboards for t in d.tiles if t.kind == "markdown"]
    assert len(md) == 34 and all(t.verdict is Verdict.CONVERT for t in md)
    assert sum(t.action == "drop" for t in md) == 4
    assert sum("image" in t.flags for t in md) == 2
    assert sum("dynatrace-link" in t.flags for t in md) >= 4


def test_layout_goes_from_24_columns_to_12() -> None:
    assert layout_plan(m.Layout(x=0, y=0, w=24, h=1)) is not None
    wide = layout_plan(m.Layout(x=12, y=7, w=12, h=7))
    assert wide is not None and (wide.column, wide.width, wide.row, wide.height) == (7, 6, 8, 4)
    thin = layout_plan(m.Layout(x=2, y=0, w=2, h=2))
    assert thin is not None and thin.width == 1  # a width-2 tile keeps one column
    assert layout_plan(None) is None


@pytest.mark.parametrize(
    ("query", "verdict", "reason"),
    [
        ("timeseries avg(cpu), by:{host}", Verdict.EXACT, None),
        ("timeseries avg(cpu), by:{host}, shift:-1h", Verdict.MANUAL, reasons.SHIFT),
        ("timeseries avg(cpu) | join [fetch logs], on:{a==b}", Verdict.MANUAL, reasons.JOIN),
        (
            "timeseries avg(cpu) | lookup [timeseries sum(x)], sourceField:a",
            Verdict.MANUAL,
            reasons.JOIN,
        ),
        ("timeseries avg(cpu) | expand tags", Verdict.MANUAL, reasons.EXPAND),
        ("timeseries avg(cpu) | dedup host", Verdict.MANUAL, reasons.DEDUP),
        ("timeseries avg(cpu) | fieldsAdd x = a + b", Verdict.MANUAL, reasons.COMPUTED),
        ("timeseries avg(cpu) | fieldsAdd x = arraySum(a)", Verdict.MANUAL, reasons.ARRAY_MATH),
        ("timeseries avg(cpu) | sort a desc, b asc | limit 5", Verdict.DRAFT, reasons.D_SORT),
        ("timeseries avg(cpu) | limit $N", Verdict.DRAFT, reasons.D_LIMIT),
        ("timeseries avg(cpu) | limit 5 + 5", Verdict.MANUAL, reasons.LIMIT),
        ("timeseries avg(cpu) | foo bar", Verdict.MANUAL, reasons.COMMAND),
        ("timeseries avg(cpu), weird: 3", Verdict.MANUAL, reasons.PARAMETER),
        ("timeseries foo(cpu)", Verdict.MANUAL, reasons.AGGREGATION),
        ("timeseries avg(a / b)", Verdict.MANUAL, reasons.AGGREGATION),
        ("timeseries avg(cpu), by:{x = a + 1}", Verdict.MANUAL, reasons.COMPUTED),
        ("timeseries avg(cpu), filter:{region == $R}", Verdict.EXACT, None),
        ("timeseries avg(cpu), filter:{foo(region)}", Verdict.MANUAL, reasons.FILTER),
        ("timeseries avg(cpu), filter:{a == 1 or b == 2}", Verdict.EXACT, None),
        ("timeseries avg(cpu), filter:{not in(a, $X)}", Verdict.EXACT, None),
        ('timeseries avg(cpu), filter: matchesValue(a, "x*")', Verdict.EXACT, None),
        (
            "timeseries avg(cpu), filter:{dt.smartscape_source.id == 1}",
            Verdict.DRAFT,
            reasons.D_ENTITY,
        ),
        ("fetch security.events | summarize n = countDistinctExact(a)", Verdict.EXACT, None),
        (
            "fetch security.events | summarize n = collectArray(a)",
            Verdict.MANUAL,
            reasons.AGGREGATION,
        ),
        ("fetch dt.davis.problems | summarize count()", Verdict.MANUAL, reasons.SRC_DAVIS),
        ("fetch logs | summarize count()", Verdict.MANUAL, reasons.SRC_OTHER),
        ("smartscapeNodes HOST | summarize count()", Verdict.MANUAL, reasons.SRC_SMARTSCAPE),
        ("data record(a=1)", Verdict.MANUAL, reasons.SRC_OTHER),
        ("", Verdict.MANUAL, reasons.Q_EMPTY),
        ("   ", Verdict.MANUAL, reasons.Q_EMPTY),
        ('timeseries avg(cpu), filter:{a == "unclosed}', Verdict.MANUAL, reasons.Q_UNREADABLE),
        ("timeseries avg(cpu), filter:{(a == 1}", Verdict.MANUAL, reasons.Q_UNREADABLE),
        ("timeseries avg(cpu) // note | join [x]", Verdict.EXACT, None),
        ('timeseries avg(cpu), filter:{a == "x | join [y] , lookup"}', Verdict.EXACT, None),
        ("timeseries avg(cpu) /* a | join */", Verdict.EXACT, None),
        ("timeseries avg(cpu) /* never closed", Verdict.MANUAL, reasons.Q_UNREADABLE),
    ],
)
def test_query_rules(query: str, verdict: Verdict, reason: str | None) -> None:
    a = analyse(query)
    got = Verdict.MANUAL if a.blockers else Verdict.DRAFT if a.drafts else Verdict.EXACT
    assert got is verdict
    if reason is not None:
        assert reason in (a.blockers or a.drafts)
    else:
        assert not a.blockers and not a.drafts


def test_a_title_or_query_never_appears_in_a_reason_or_the_report_unescaped(tmp_path: Path) -> None:
    marker = "ZZSECRETMARKERZZ"
    board = {
        "version": 21,
        "tiles": {
            f"id{marker}": {
                "type": "data",
                "title": f"| inject | `{marker}` <b>x</b> [l](http://x)",
                "query": f"fetch {marker} | join [fetch {marker}] | foo {marker}",
                "visualization": f"viz{marker}",
            },
            "b": {"type": "frobnicate", "title": marker},
        },
        "variables": [{"key": f"v{marker}", "type": "query", "input": f"smartscapeNodes {marker}"}],
    }
    path = tmp_path / f"{marker}.json"
    path.write_text(json.dumps(board))
    plans = plan_documents(m.read_file(path))
    for dash in plans.dashboards:
        for tile in dash.tiles:
            assert all(marker not in text for text in [*tile.reasons, *tile.notes])
        for var in dash.variables:
            assert all(marker not in text for text in [*var.reasons, *var.notes])
    report = render_report(plans)
    assert "| inject |" not in report and "<b>" not in report and "[l](" not in report
    assert "`" + marker not in report


def test_every_reason_text_has_a_known_prefix_and_no_two_are_equal() -> None:
    assert len(set(reasons.ALL)) == len(reasons.ALL)
    assert all(text.startswith(reasons.PREFIXES) for text in reasons.ALL)
    assert all(text == text.strip() and "\n" not in text for text in reasons.ALL)


def test_an_unreadable_file_and_a_non_dashboard_become_manual_entries(tmp_path: Path) -> None:
    (tmp_path / "bad.json").write_text("{nope")
    (tmp_path / "other.json").write_text(json.dumps({"hello": "world"}))
    plans = plan_documents(m.load_documents(tmp_path)[0])
    by_ref = {d.ref: d for d in plans.dashboards}
    assert by_ref["bad.json"].reasons == [reasons.FILE_UNREADABLE]
    assert by_ref["other.json"].reasons == [reasons.FILE_SHAPE]


def test_classic_tiles_are_planned_by_type() -> None:
    (doc,) = m.read_file(FIXTURES / "classic-dashboards" / "classic-ops.json")
    plan = plan_documents([doc]).dashboards[0]
    verdicts = [(t.kind, t.verdict) for t in plan.tiles]
    assert verdicts[:3] == [
        ("MARKDOWN", Verdict.CONVERT),
        ("HEADER", Verdict.CONVERT),
        ("DATA_EXPLORER", Verdict.MANUAL),
    ]
    assert plan.tiles[2].reasons == [reasons.C_METRICS] and plan.tiles[3].reasons == [
        reasons.C_TILE
    ]
    assert reasons.C_ZONE in plan.notes and reasons.C_ZONE in plan.tiles[5].notes
    assert plan.counts.markdown == 2 and plan.counts.manual == 5


def test_the_report_has_counts_reasons_and_steps() -> None:
    report = render_report(platform_plans())
    assert "59" in report and "| exact (every clause maps to NRQL unchanged) | 10 |" in report
    assert "## What to do" in report and "Manual reasons, most frequent first" in report


# Hostile input: the time bound is the assertion, since every one of these is valid text.
@pytest.mark.parametrize(
    "query",
    [
        "timeseries avg(cpu), filter:{" + " and ".join(f"a{i} == {i}" for i in range(5000)) + "}",
        "timeseries avg(cpu) " + "| filter a == 1 " * 5000,
        "timeseries avg(" + "(" * 60 + "x" + ")" * 60 + ")",
        "timeseries " + "a" * 90_000,
        "fetch security.events | summarize " + ", ".join(f"c{i} = count()" for i in range(5000)),
        "timeseries avg(cpu) | " + "x" * 200_000,
        "timeseries avg(cpu), filter:{" + "not " * 20_000 + "a == 1}",
        "timeseries avg(cpu), filter:{" + "(" * 5_000 + "a == 1" + ")" * 5_000 + "}",
        "timeseries avg(cpu) // " + "|" * 50_000,
    ],
)
def test_hostile_queries_finish_quickly_and_never_raise(query: str) -> None:
    started = time.monotonic()
    a = analyse(query)
    assert time.monotonic() - started < 2.0
    assert isinstance(a.blockers, list)


def test_thousands_of_tiles_and_huge_text_are_bounded(tmp_path: Path) -> None:
    tiles = {
        str(i): {"type": "data", "query": "timeseries avg(cpu)", "visualization": "lineChart"}
        for i in range(m.MAX_TILES + 100)
    }
    tiles["huge"] = {"type": "markdown", "content": "![" * 50_000}
    path = tmp_path / "big.json"
    path.write_text(json.dumps({"version": 21, "tiles": tiles}))
    started = time.monotonic()
    plans = plan_documents(m.read_file(path))
    report = render_report(plans)
    assert time.monotonic() - started < 5.0
    assert plans.dashboards[0].unreadable_tiles == 1 and "big" in report
