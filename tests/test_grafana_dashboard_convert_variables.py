"""Grafana variables to New Relic variables: kinds, defaults, regex and the use restrictions."""

import pytest

from importer.core.dashboard_ir import ReplacementStrategy, VariableType
from importer.sources.grafana.dashboard_convert.name_map import NameEntry, NameMap
from importer.sources.grafana.dashboard_convert.variables import (
    RawVar,
    Variables,
    convert_variables,
    custom_items,
)
from importer.sources.grafana.dashboards.models import Dashboard, Variable
from importer.sources.grafana.dashboards.plan import plan_dashboard


def build(
    variables: list[Variable], raws: list[RawVar] | None = None, names: NameMap | None = None
) -> Variables:
    dash = Dashboard(uid="u", title="t", variables=variables)
    plans = plan_dashboard(dash).variables
    return convert_variables(dash, plans, raws or [RawVar() for _ in variables], names or NameMap())


def query_var(query: str, **extra: object) -> Variable:
    return Variable(name="pod", kind="query", query=query, **extra)


@pytest.mark.parametrize(
    ("query", "nrql"),
    [
        (
            "label_values(kube_pod_info, pod)",
            "FROM Metric SELECT uniques(pod) WHERE metricName = 'kube_pod_info'",
        ),
        ("label_values(pod)", "FROM Metric SELECT uniques(pod)"),
        (
            'label_values(kube_pod_info{namespace="prod", node!="n1"}, pod)',
            "FROM Metric SELECT uniques(pod) WHERE metricName = 'kube_pod_info' "
            "AND namespace = 'prod' AND node != 'n1'",
        ),
        (
            'label_values({job="api"}, instance)',
            "FROM Metric SELECT uniques(instance) WHERE job = 'api'",
        ),
    ],
)
def test_label_values_becomes_uniques(query: str, nrql: str) -> None:
    out = build([query_var(query)])
    assert [v.nrql for v in out.variables] == [nrql]
    assert out.variables[0].type == VariableType.NRQL


@pytest.mark.parametrize(
    ("regex", "tail"),
    [
        ("/prod-.*/", " AND pod LIKE 'prod-%'"),
        ("/a|b/", " AND pod IN ('a', 'b')"),
        ("/web/", " AND pod = 'web'"),
        ("", ""),
    ],
)
def test_a_plain_variable_regex_filters_the_values(regex: str, tail: str) -> None:
    out = build([query_var("label_values(up, pod)", regex=regex)])
    assert out.variables[0].nrql == "FROM Metric SELECT uniques(pod) WHERE metricName = 'up'" + tail


def test_the_name_map_decides_the_event_of_a_variable_query() -> None:
    names = NameMap(metrics={"up": NameEntry(event="Sample", attribute="up_attr")})
    out = build([query_var("label_values(up, pod)")], names=names)
    assert out.variables[0].nrql == "FROM Sample SELECT uniques(pod) WHERE up_attr IS NOT NULL"


def test_multi_and_include_all_make_a_multi_select_variable_that_selects_all() -> None:
    raw = RawVar(current=["$__all"])
    out = build([query_var("label_values(up, pod)", multi=True, include_all=True)], [raw])
    v = out.variables[0]
    assert v.multi_select and v.default_values == ["*"]
    raw = RawVar(current=["a", "b"])
    out = build([query_var("label_values(up, pod)", multi=True)], [raw])
    assert out.variables[0].default_values == ["a", "b"]
    out = build([query_var("label_values(up, pod)")], [RawVar(current=["a", "b"])])
    assert out.variables[0].default_values == ["a"] and not out.variables[0].multi_select


def test_the_label_is_the_title_and_falls_back_to_the_name() -> None:
    assert (
        build([query_var("label_values(up, pod)")], [RawVar(label="Pod")]).variables[0].title
        == "Pod"
    )
    assert build([query_var("label_values(up, pod)")]).variables[0].title == "pod"


@pytest.mark.parametrize(
    ("query", "items"),
    [
        ("a,b,c", [(None, "a"), (None, "b"), (None, "c")]),
        ("Production : prod, Staging : stg", [("Production", "prod"), ("Staging", "stg")]),
        (r"a\,b,c", [(None, "a,b"), (None, "c")]),
        (" a , ,b ", [(None, "a"), (None, "b")]),
        ("a,a", [(None, "a")]),
        ("", []),
    ],
)
def test_custom_variable_items(query: str, items: list[tuple[str | None, str]]) -> None:
    assert [(i.title, i.value) for i in custom_items(query)] == items


def test_a_custom_variable_is_an_enum_with_a_default_in_its_items() -> None:
    var = Variable(name="env", kind="custom", query="prod,stg")
    out = build([var], [RawVar(current=["stg"])])
    v = out.variables[0]
    assert v.type == VariableType.ENUM and v.default_values == ["stg"]
    out = build([var], [RawVar(current=["nope"])])
    assert out.variables[0].default_values == ["prod"]
    multi = Variable(name="env", kind="custom", query="prod,stg", multi=True, include_all=True)
    out = build([multi], [RawVar(current=["$__all"])])
    assert out.variables[0].default_values == ["prod", "stg"] and out.variables[0].multi_select


def test_custom_options_of_a_saved_dashboard_are_used_before_the_query() -> None:
    var = Variable(name="env", kind="custom", query="ignored")
    raw = RawVar(options=[("Prod", "prod"), ("Stage", "stg")], current=["prod"])
    v = build([var], [raw]).variables[0]
    assert [(i.title, i.value) for i in v.items] == [("Prod", "prod"), ("Stage", "stg")]


def test_a_textbox_is_a_string_variable_with_its_default() -> None:
    var = Variable(name="filter", kind="textbox", query="abc")
    v = build([var]).variables[0]
    assert v.type == VariableType.STRING and v.default_values == ["abc"]
    assert build([var], [RawVar(current=["xyz"])]).variables[0].default_values == ["xyz"]


def test_a_constant_is_written_into_queries_and_makes_no_variable() -> None:
    var = Variable(name="env", kind="constant", query="prod")
    out = build([var])
    assert out.variables == [] and out.options == {"env": "prod"} and out.substituted == 1


def test_interval_and_datasource_variables_are_dropped_and_interval_is_remembered() -> None:
    interval = Variable(name="step", kind="interval", query="1m,5m")
    source = Variable(name="ds", kind="datasource", query="prometheus")
    out = build([interval, source])
    assert out.variables == [] and out.dropped == 2 and out.intervals == frozenset({"step"})


def test_variables_the_planner_keeps_manual_are_not_created() -> None:
    adhoc = Variable(name="filters", kind="adhoc")
    nested = Variable(name="b", kind="query", query='label_values(up{a=~"$a"}, b)')
    first = Variable(name="a", kind="custom", query="1,2")
    out = build([first, nested, adhoc])
    assert [v.name for v in out.variables] == ["a"] and out.manual == 2


def test_a_variable_used_as_a_number_gets_the_number_strategy() -> None:
    var = Variable(name="factor", kind="custom", query="1,2")
    out = build([var])
    assert out.variables[0].replacement == ReplacementStrategy.DEFAULT
    marked = out.with_numeric({"factor"})
    assert marked[0].replacement == ReplacementStrategy.NUMBER


def test_hostile_values_cannot_break_the_variable_models() -> None:
    long_value = "x" * 5000
    var = Variable(name="env", kind="custom", query=f"{long_value},ok")
    out = build([var])
    assert [i.value for i in out.variables[0].items] == ["ok"]
    quoted = query_var('label_values(up{job="a\'b"}, pod)')
    assert build([quoted]).variables[0].nrql == (
        "FROM Metric SELECT uniques(pod) WHERE metricName = 'up' AND job = 'a\\'b'"
    )
