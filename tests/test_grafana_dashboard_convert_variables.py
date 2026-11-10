"""Grafana variables to New Relic variables: kinds, defaults, regex and the use restrictions."""

import pytest

from importer.core.dashboard_ir import ReplacementStrategy, VariableType
from importer.sources.grafana.dashboard_convert.name_map import NameEntry, NameMap
from importer.sources.grafana.dashboard_convert.panels import Context, convert_panel
from importer.sources.grafana.dashboard_convert.variables import (
    RawVar,
    Variables,
    convert_variables,
    custom_items,
)
from importer.sources.grafana.dashboards.models import Dashboard, DsRef, Panel, Target, Variable
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
    nested = Variable(name="b", kind="query", query='label_values(up{a=~"$filters"}, b)')
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


def test_refresh_on_load_means_the_variable_ignores_the_time_picker() -> None:
    on_load = build([query_var("label_values(up, pod)")], [RawVar(refresh=1)])
    on_range = build([query_var("label_values(up, pod)")], [RawVar(refresh=2)])
    assert on_load.variables[0].ignore_time_range is True
    assert on_range.variables[0].ignore_time_range is False


def test_a_variable_the_converter_cannot_write_is_counted_and_its_panels_say_so() -> None:
    ds = DsRef(type="prometheus", uid="p")
    var = Variable(name="pod", kind="query", query='label_values(up{job=~"(?i:a)b"}, pod)', ds=ds)
    panel = Panel(
        id=1,
        type="timeseries",
        title="t",
        ds=ds,
        targets=[Target(ref_id="A", expr='up{pod="$pod"}')],
    )
    dash = Dashboard(uid="u", title="t", variables=[var], panels=[panel])
    plan = plan_dashboard(dash)
    assert plan.variables[0].status == "converts" and plan.panels[0].tier is not None
    found = convert_variables(dash, plan.variables, [RawVar()], NameMap())
    assert found.variables == [] and found.manual == 1
    draft = convert_panel(panel, plan.panels[0], None, Context(dash, NameMap(), found))
    assert draft.kind.value == "unsupported"
    assert draft.reasons[0].endswith("did not become a New Relic variable")


# --- chained variables, query_result, metrics(), __name__ ---


def named(name: str, query: str, **extra: object) -> Variable:
    return Variable(name=name, kind="query", query=query, **extra)


def nrql_of(out: Variables) -> dict[str, str | None]:
    return {v.name: v.nrql for v in out.variables}


def test_a_chain_of_variables_uses_in_filters_and_lists_parents_first() -> None:
    chain = [
        named("node", 'label_values(node_uname_info{job="$job", nodename="$nodename"}, instance)'),
        named("nodename", 'label_values(node_uname_info{job="$job"}, nodename)'),
        named("job", "label_values(node_uname_info, job)"),
    ]
    out = build(chain)
    assert [v.name for v in out.variables] == ["job", "nodename", "node"]
    assert nrql_of(out) == {
        "job": "FROM Metric SELECT uniques(job) WHERE metricName = 'node_uname_info'",
        "nodename": "FROM Metric SELECT uniques(nodename) WHERE metricName = 'node_uname_info' "
        "AND job IN ({{job}})",
        "node": "FROM Metric SELECT uniques(instance) WHERE metricName = 'node_uname_info' "
        "AND job IN ({{job}}) AND nodename IN ({{nodename}})",
    }
    assert out.nested == 2 and out.manual == 0


def test_a_multi_value_parent_is_matched_with_in() -> None:
    parent = named("ns", "label_values(kube_pod_info, namespace)", multi=True, include_all=True)
    child = named("pod", 'label_values(kube_pod_info{namespace=~"$ns"}, pod)')
    out = build([parent, child])
    assert out.variables[0].multi_select and out.variables[0].default_values == ["*"]
    assert nrql_of(out)["pod"] == (
        "FROM Metric SELECT uniques(pod) WHERE metricName = 'kube_pod_info' "
        "AND namespace IN ({{ns}})"
    )


def test_a_constant_parent_is_written_into_the_child_query() -> None:
    const = Variable(name="env", kind="constant", query="prod")
    child = named("pod", 'label_values(up{env="$env"}, pod)')
    assert nrql_of(build([const, child]))["pod"].endswith("AND env = 'prod'")  # type: ignore[union-attr]


def test_a_parent_that_stays_manual_leaves_the_child_manual_and_counted() -> None:
    adhoc = Variable(name="filters", kind="adhoc")
    child = named("pod", 'label_values(up{a=~"$filters"}, pod)')
    out = build([adhoc, child])
    assert out.variables == [] and out.manual == 2


def test_a_child_whose_query_cannot_be_written_is_manual_not_a_wrong_filter() -> None:
    parent = named("a", "label_values(up, a)")
    child = named("b", 'label_values(up{x=~"$a-suffix"}, b)')  # text mixed with a variable
    out = build([parent, child])
    assert [v.name for v in out.variables] == ["a"] and out.manual == 1


def test_a_variable_cycle_creates_no_variable() -> None:
    a = named("a", 'label_values(up{y=~"$b"}, x)')
    b = named("b", 'label_values(up{x=~"$a"}, y)')
    out = build([a, b])
    assert out.variables == [] and out.manual == 2


@pytest.mark.parametrize(
    ("variable", "nrql"),
    [
        (
            named("i", "query_result(pg_up)", regex='/.*instance="([^"]+).*/'),
            "FROM Metric SELECT uniques(instance) WHERE metricName = 'pg_up'",
        ),
        (
            named("r", 'query_result(pg_up{kubernetes_namespace="x"})', regex='/release="([^"]+)/'),
            "FROM Metric SELECT uniques(release) WHERE metricName = 'pg_up' "
            "AND kubernetes_namespace = 'x'",
        ),
        (
            named("m", "metrics(node_.*)"),
            "FROM Metric SELECT uniques(metricName) WHERE metricName LIKE 'node_%'",
        ),
        (named("all", "metrics(.*)"), "FROM Metric SELECT uniques(metricName)"),
        (
            named("h", 'label_values({__name__=~"container.*", instance=~"$h2"}, name)'),
            "FROM Metric SELECT uniques(name) WHERE metricName LIKE 'container%' "
            "AND instance IN ({{h2}})",
        ),
    ],
)
def test_other_prometheus_variable_queries_become_uniques(variable: Variable, nrql: str) -> None:
    parent = named("h2", 'label_values({__name__=~"container.*"}, instance)')
    out = build([parent, variable] if variable.name == "h" else [variable])
    assert nrql_of(out)[variable.name] == nrql


def test_query_result_variables_are_noted_as_approximate() -> None:
    v = named("i", "query_result(pg_up)", regex='/.*instance="([^"]+).*/')
    out = build([v])
    assert out.query_results == 1


def test_a_label_called_metricname_is_manual_because_it_collides_with_the_name_attribute() -> None:
    assert build([named("x", "label_values(up, metricName)")]).manual == 1
    assert build([named("x", 'label_values(up{metricName="a"}, pod)')]).manual == 1


def test_query_result_of_an_expression_or_without_a_capture_is_manual() -> None:
    assert build([named("x", "query_result(sum(up))", regex='/job="([^"]+)/')]).manual == 1
    assert build([named("x", "query_result(up)")]).manual == 1


def test_the_name_map_decides_the_event_of_a_parent_filter() -> None:
    names = NameMap(metrics={"kube_pod_info": NameEntry(event="K8sPodSample", attribute="podName")})
    parent = named("ns", "label_values(kube_pod_info, namespace)")
    child = named("pod", 'label_values(kube_pod_info{namespace=~"$ns"}, pod)')
    out = build([parent, child], names=names)
    assert nrql_of(out)["pod"] == (
        "FROM K8sPodSample SELECT uniques(pod) WHERE podName IS NOT NULL AND namespace IN ({{ns}})"
    )


@pytest.mark.parametrize(
    ("query", "tail"),
    [
        ('label_values(kube_pod_info{name!=""}, pod)', " AND name IS NOT NULL"),
        ('label_values(kube_pod_info{name=~".+"}, pod)', " AND name IS NOT NULL"),
        (
            'label_values(kube_pod_info{version=~"vm(insert|select).*"}, pod)',
            " AND version RLIKE 'vm(insert|select).*'",
        ),
    ],
)
def test_a_variable_query_reads_the_same_regex_matchers_as_a_panel_query(
    query: str, tail: str
) -> None:
    out = build([query_var(query)])
    assert [v.nrql for v in out.variables] == [
        "FROM Metric SELECT uniques(pod) WHERE metricName = 'kube_pod_info'" + tail
    ]
