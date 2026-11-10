"""NerdGraph and Terraform output for the extended dashboard IR."""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import (
    IRDashboard,
    IRVariable,
    IRWidget,
    Layout,
    RawNrqlSeries,
    Series,
    Threshold,
    ThresholdSeverity,
    VariableItem,
    VariableType,
    Visualization,
    WidgetKind,
    WidgetOptions,
)
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import (
    ACCOUNT_PLACEHOLDER,
    dashboard_resource,
    dashboards_terraform,
    nerdgraph_dashboard,
    number_text,
)
from importer.core.ir import (
    Aggregation,
    Confidence,
    MetricSignal,
    MetricTerm,
    NrTarget,
    TagFilter,
)

SCHEMA: dict[str, Any] = json.loads(
    (Path(__file__).parent / "fixtures" / "newrelic-one-dashboard-schema.json").read_text()
)


def signal(scope_value: str | None = None) -> MetricSignal:
    term = MetricTerm(
        metric="cpu",
        space_aggregation=Aggregation.AVG,
        nr_target=NrTarget(event="Metric", attribute="cpu"),
    )
    scope = [TagFilter(key="host", value=scope_value)] if scope_value is not None else []
    return MetricSignal(terms={"a": term}, aggregation=Aggregation.AVG, scope=scope)


def widget(**extra: Any) -> IRWidget:
    base: dict[str, Any] = {
        "kind": WidgetKind.CHART,
        "source_type": "t",
        "title": "w",
        "layout": Layout(column=1, row=1, width=4, height=3),
        "visualization": Visualization.LINE,
        "series": [Series(signal=signal(), period_seconds=60)],
        "confidence": Confidence.EXACT,
    }
    if "raw_series" in extra and "series" not in extra:
        base["series"] = []
    return IRWidget(**{**base, **extra})


def dash(*widgets: IRWidget, **extra: Any) -> IRDashboard:
    return IRDashboard(
        source_system="grafana",
        source_key="k",
        name=extra.pop("name", "n"),
        widgets=list(widgets),
        confidence=Confidence.EXACT,
        **extra,
    )


def queries(d: IRDashboard) -> list[str]:
    page = nerdgraph_dashboard(d)["variables"]["dashboard"]["pages"][0]
    return [q["query"] for w in page["widgets"] for q in w["rawConfiguration"]["nrqlQueries"]]


def nerdgraph_widget(d: IRDashboard) -> dict[str, Any]:
    widgets: list[dict[str, Any]] = nerdgraph_dashboard(d)["variables"]["dashboard"]["pages"][0][
        "widgets"
    ]
    return widgets[0]


def terraform(d: IRDashboard) -> str:
    return dashboard_resource(d, 1)


# A small structural parser for the HCL this module writes, checked against the provider schema.
_BLOCK = re.compile(r'^(\w+)(?: "[^"]*")* \{$')
_ATTR = re.compile(r"^(\w+)\s*=")


def schema_errors(hcl: str) -> list[str]:
    errors: list[str] = []
    stack: list[dict[str, Any]] = []
    for raw in hcl[hcl.index("resource ") :].splitlines():
        line = raw.strip()
        if m := _BLOCK.match(line):
            if not stack:
                stack.append(SCHEMA)  # the resource itself
            else:
                child = stack[-1]["blocks"].get(m.group(1))
                if child is None:
                    errors.append(f"unknown block {m.group(1)}")
                    child = {"attributes": {}, "blocks": {}}
                stack.append(child)
        elif line == "}":
            stack.pop()
        elif stack and (m := _ATTR.match(line)) and m.group(1) not in stack[-1]["attributes"]:
            errors.append(f"unknown attribute {m.group(1)}")
    return errors


# --- table, raw series, automatic bucket -------------------------------------------------------


def test_a_table_widget_has_no_timeseries_and_its_own_widget_type() -> None:
    d = dash(widget(visualization=Visualization.TABLE))
    assert nerdgraph_widget(d)["visualization"] == {"id": "viz.table"}
    assert "TIMESERIES" not in queries(d)[0]
    assert "widget_table {" in terraform(d)


def test_auto_period_writes_timeseries_auto_only_on_charts_over_time() -> None:
    auto = [Series(signal=signal(), period_seconds=60, auto_period=True)]
    assert queries(dash(widget(series=auto)))[0].endswith(" TIMESERIES AUTO")
    assert queries(dash(widget(series=auto, visualization=Visualization.AREA)))[0].endswith(
        " TIMESERIES AUTO"
    )
    fixed = queries(dash(widget()))[0]
    assert fixed.endswith("TIMESERIES 60 SECONDS")


def test_raw_series_are_written_unchanged_to_both_outputs() -> None:
    raw = [RawNrqlSeries(nrql="SELECT count(*) FROM Log FACET host TIMESERIES AUTO")]
    d = dash(widget(raw_series=raw))
    assert queries(d) == [raw[0].nrql]
    assert raw[0].nrql in terraform(d)


def test_pretty_printed_raw_nrql_is_emitted_as_given() -> None:
    nrql = "SELECT count(*)\n  FROM Log\n\tFACET host"
    d = dash(widget(raw_series=[RawNrqlSeries(nrql=nrql)]))
    assert queries(d) == [nrql]
    assert "\\n  FROM Log" in terraform(d)


def test_the_emitters_check_raw_queries_again_so_a_later_edit_cannot_slip_through() -> None:
    raw = RawNrqlSeries(nrql="SELECT 1 FROM Log")
    d = dash(widget(raw_series=[raw]))
    raw.nrql = "SELECT 1 FROM Log SINCE 1 day ago"
    with pytest.raises(CannotEmit, match="SINCE"):
        nerdgraph_dashboard(d)


def test_a_chart_with_only_raw_series_is_not_turned_into_a_placeholder() -> None:
    raw = [RawNrqlSeries(nrql="SELECT 1 FROM Log")]
    assert nerdgraph_widget(dash(widget(series=[], raw_series=raw)))["visualization"] == {
        "id": "viz.line"
    }


# --- height ------------------------------------------------------------------------------------


HEIGHTS = [6, 6, 6, 8, 12, 15, 20, 24, 26, 30]


def tall_dashboard() -> IRDashboard:
    widgets = [
        widget(title=f"w{i}", layout=Layout(column=1, row=1 + 10 * i, width=12, height=h))
        for i, h in enumerate(HEIGHTS)
    ]
    return dash(*widgets)


def test_tall_widgets_are_clamped_and_the_dashboard_is_still_emitted() -> None:
    d = tall_dashboard()
    sent = nerdgraph_dashboard(d)["variables"]["dashboard"]
    heights = [w["layout"]["height"] for w in sent["pages"][0]["widgets"]]
    assert heights == [min(h, 12) for h in HEIGHTS]
    rows = [w["layout"]["row"] for w in sent["pages"][0]["widgets"]]
    assert rows == [1 + 10 * i for i in range(10)]
    assert len(sent["pages"][0]["widgets"]) == 10
    hcl = terraform(d)
    assert hcl.count("height = 12") == 6 and "height = 30" not in hcl
    assert "height = 6" in hcl and "height = 8" in hcl


def test_the_clamp_is_recorded_in_the_ir_and_in_the_output_description() -> None:
    d = tall_dashboard()
    noted = [w.title for w in d.widgets if w.reasons]
    assert noted == ["w5", "w6", "w7", "w8", "w9"]
    assert d.widgets[9].reasons == ["height 30 clamped to 12"]
    sent = nerdgraph_dashboard(d)["variables"]["dashboard"]
    assert "w9 (30)" in sent["description"] and "clamped to 12" in sent["description"]
    assert 'description = "' in terraform(d) and "w9 (30)" in terraform(d)
    plain = nerdgraph_dashboard(dash(widget()))["variables"]["dashboard"]["description"]
    assert "clamped" not in plain and "description" not in terraform(dash(widget()))


def test_clamping_does_not_edit_the_ir_layout() -> None:
    d = tall_dashboard()
    nerdgraph_dashboard(d)
    assert [w.layout.height for w in d.widgets] == HEIGHTS


def test_the_provider_maximum_height_is_emitted_as_it_is() -> None:
    d = dash(widget(layout=Layout(column=1, row=1, width=4, height=12)))
    assert "height = 12" in terraform(d) and d.widgets[0].reasons == []


# --- options and thresholds --------------------------------------------------------------------


def test_options_are_written_as_provider_attributes_and_blocks() -> None:
    opts = WidgetOptions(unit="BYTES", legend=False, y_min=0, y_max=99.5, y_zero=True)
    text = terraform(dash(widget(options=opts)))
    assert "      legend_enabled = false" in text
    assert "      y_axis_left_min = 0" in text and "      y_axis_left_max = 99.5" in text
    assert "      y_axis_left_zero = true" in text
    assert 'units {\n        unit = "BYTES"\n      }' in text.replace("\n\n", "\n")
    assert schema_errors(terraform(dash(widget(options=opts)))) == []


def test_options_with_no_verified_name_are_not_emitted() -> None:
    opts = WidgetOptions(decimals=2, stacked=True)
    d = dash(widget(visualization=Visualization.AREA, options=opts))
    assert "precision" not in terraform(d) and "stacked" not in terraform(d)
    assert "widget_area {" in terraform(d)


def test_nerdgraph_keeps_its_widget_shape_when_options_are_set() -> None:
    plain = nerdgraph_widget(dash(widget()))
    opts = WidgetOptions(unit="BYTES", legend=True)
    th = [Threshold(severity=ThresholdSeverity.CRITICAL, from_value=1)]
    with_options = nerdgraph_widget(dash(widget(options=opts, thresholds=th)))
    assert with_options == plain


def test_line_and_table_thresholds_become_threshold_blocks() -> None:
    th = [
        Threshold(severity=ThresholdSeverity.WARNING, from_value=50, to_value=80, name='a "b"'),
        Threshold(severity=ThresholdSeverity.CRITICAL, from_value=80),
    ]
    line = terraform(dash(widget(thresholds=th)))
    assert 'name = "a \\"b\\""' in line
    assert '        severity = "critical"' in line and '        from = "80"' in line
    assert "to = " not in line.split("critical")[1]
    table_th = [Threshold(severity=ThresholdSeverity.SEVERE, to_value=5, column_name='c"ol')]
    table = terraform(dash(widget(visualization=Visualization.TABLE, thresholds=table_th)))
    assert 'column_name = "c\\"ol"' in table
    for text in (line, table):
        assert schema_errors(text) == []


def test_billboard_thresholds_become_critical_and_warning() -> None:
    th = [
        Threshold(severity=ThresholdSeverity.CRITICAL, from_value=90),
        Threshold(severity=ThresholdSeverity.WARNING, from_value=70.5),
    ]
    text = terraform(dash(widget(visualization=Visualization.BILLBOARD, thresholds=th)))
    assert '      critical = "90"' in text and '      warning = "70.5"' in text
    assert "threshold {" not in text


def test_duplicate_billboard_severities_are_refused_so_terraform_never_repeats_an_attribute() -> (
    None
):
    th = [
        Threshold(severity=ThresholdSeverity.CRITICAL, from_value=90),
        Threshold(severity=ThresholdSeverity.CRITICAL, from_value=95),
    ]
    with pytest.raises(ValueError, match="more than one"):
        widget(visualization=Visualization.BILLBOARD, thresholds=th)


def test_a_table_threshold_has_no_name_in_the_model_or_the_output() -> None:
    with pytest.raises(ValueError, match="name"):
        widget(
            visualization=Visualization.TABLE,
            thresholds=[Threshold(severity=ThresholdSeverity.SEVERE, to_value=5, name="n")],
        )
    ok = Threshold(severity=ThresholdSeverity.SEVERE, to_value=5, column_name="c")
    text = terraform(dash(widget(visualization=Visualization.TABLE, thresholds=[ok])))
    block = text.split("threshold {")[1].split("}")[0]
    assert "name" not in block.replace("column_name", "")


@pytest.mark.parametrize(
    ("value", "text"),
    [
        (1073741824, "1073741824"),
        (1234567, "1234567"),
        (12345678901, "12345678901"),
        (0.1, "0.1"),
        (1e-7, "1e-07"),
        (123456.789012, "123456.789012"),
        (-5.0, "-5"),
        (0.0, "0"),
    ],
)
def test_numbers_are_written_with_every_digit(value: float, text: str) -> None:
    th = [Threshold(severity=ThresholdSeverity.WARNING, from_value=value)]
    opts = WidgetOptions(y_max=value) if value >= 0 else WidgetOptions()
    hcl = terraform(dash(widget(thresholds=th, options=opts)))
    assert f'from = "{text}"' in hcl
    if value >= 0:
        assert f"y_axis_left_max = {text}\n" in hcl


@pytest.mark.parametrize("value", [0.1, 1e-7, 1e21, 2.0**53, 2.0**53 + 2, 1073741824.0, 1.5e300])
def test_number_text_round_trips_exactly(value: float) -> None:
    assert float(number_text(value)) == value


# --- variables ---------------------------------------------------------------------------------


def variables() -> list[IRVariable]:
    return [
        IRVariable(
            name="host",
            title='Host "name"',
            type=VariableType.NRQL,
            nrql="FROM Metric SELECT uniques(host)",
            account_ids=[12, 34],
            multi_select=True,
            default_values=["*"],
            ignore_time_range=True,
        ),
        IRVariable(
            name="env",
            title="Env",
            type=VariableType.ENUM,
            items=[VariableItem(title="Prod", value="prod"), VariableItem(value="dev")],
            default_values=["prod"],
        ),
        IRVariable(name="free", title="Free", type=VariableType.STRING),
    ]


def with_variables() -> IRDashboard:
    raw = RawNrqlSeries(nrql="SELECT count(*) FROM Log WHERE host IN ({{host}}) FACET {{free}}")
    return dash(widget(raw_series=[raw]), variables=variables())


def test_variables_are_written_to_terraform_and_match_the_schema() -> None:
    text = terraform(with_variables())
    assert text.count("  variable {") == 3
    assert '    title = "Host \\"name\\""' in text
    assert '    type = "nrql"' in text
    assert '    default_values = ["*"]' in text
    assert "    is_multi_selection = true" in text
    assert "account_ids = [12, 34]" in text
    assert "ignore_time_range = true" in text and "excluded" not in text
    assert '    item {\n      title = "Prod"\n      value = "prod"\n    }' in text
    assert schema_errors(terraform(with_variables())) == []


def test_variables_are_written_to_nerdgraph() -> None:
    sent = nerdgraph_dashboard(with_variables())["variables"]["dashboard"]["variables"]
    host, env, free = sent
    assert host["type"] == "NRQL" and host["isMultiSelection"] is True
    assert host["nrqlQuery"] == {
        "accountIds": [12, 34],
        "query": "FROM Metric SELECT uniques(host)",
    }
    assert host["defaultValues"] == [{"value": {"string": "*"}}]
    assert host["options"] == {"ignoreTimeRange": True}
    assert env["items"] == [{"title": "Prod", "value": "prod"}, {"value": "dev"}]
    assert env["replacementStrategy"] == "DEFAULT"
    assert "nrqlQuery" not in free and "items" not in free


def test_an_nrql_variable_without_accounts_uses_the_account_placeholder() -> None:
    v = IRVariable(name="h", title="H", type=VariableType.NRQL, nrql="FROM Log SELECT uniques(h)")
    sent = nerdgraph_dashboard(dash(widget(), variables=[v]))["variables"]["dashboard"]
    assert sent["variables"][0]["nrqlQuery"]["accountIds"] == [ACCOUNT_PLACEHOLDER]
    assert "account_ids" not in terraform(dash(widget(), variables=[v]))


def test_a_dashboard_without_variables_has_no_variables_key() -> None:
    assert "variables" not in nerdgraph_dashboard(dash(widget()))["variables"]["dashboard"]
    assert "variable {" not in terraform(dash(widget()))


def test_a_variable_in_a_built_query_must_be_defined_when_the_dashboard_has_variables() -> None:
    other = IRVariable(name="other", title="O", type=VariableType.STRING)
    d = dash(
        widget(series=[Series(signal=signal("{{ghost}}"), period_seconds=60)]), variables=[other]
    )
    with pytest.raises(CannotEmit, match="ghost"):
        nerdgraph_dashboard(d)


def test_a_variable_in_a_built_query_clause_that_forbids_it_is_refused() -> None:
    bad = Series(label="{{v}}", signal=signal("{{v}}"), period_seconds=60)  # inside AS
    v = IRVariable(name="v", title="V", type=VariableType.STRING)
    with pytest.raises(CannotEmit, match="AS"):
        nerdgraph_dashboard(dash(widget(series=[bad]), variables=[v]))


@pytest.mark.parametrize(
    ("label", "value"),
    [
        ("{{instance}}", "web"),
        ("Cost {{team}}", "web"),
        ("plain", "{{x}}"),
        ("plain", "a{{"),
        ("plain", "}}"),
        ("}}", "{{"),
        ("{{ghost}} {{", "{{ghost}}"),
    ],
)
def test_braces_in_structured_series_are_literal_text_and_emit_as_before(
    label: str, value: str
) -> None:
    series = [Series(label=label, signal=signal(value), period_seconds=60)]
    d = dash(widget(series=series))
    (query,) = queries(d)
    assert f"AS '{label}'" in query and value in query
    assert "AS" in terraform(d)


def test_the_variable_checks_run_for_a_dashboard_that_uses_raw_series() -> None:
    raw = RawNrqlSeries(nrql="SELECT count(*) FROM Log")
    other = dash(
        widget(raw_series=[raw]),
        widget(series=[Series(signal=signal("{{ghost}}"), period_seconds=60)]),
    )
    with pytest.raises(CannotEmit, match="ghost"):
        nerdgraph_dashboard(other)


# --- hostile text ------------------------------------------------------------------------------

HOSTILE = [
    '"; resource "x" "y" {',
    '${file("/etc/passwd")}',
    "%{ if true }x%{ endif }",
    "line\nbreak\r\ttab",
    "back\\slash",
    "}}{{",
]


@pytest.mark.parametrize("text", HOSTILE)
def test_hostile_titles_and_variable_text_never_break_out_of_hcl_strings(text: str) -> None:
    v = IRVariable(
        name="v",
        title=text,
        type=VariableType.ENUM,
        items=[VariableItem(title=text, value=text)],
        default_values=[text],
    )
    t = Threshold(severity=ThresholdSeverity.WARNING, from_value=1, name=text)
    d = dash(widget(title=text, thresholds=[t]), name=text, variables=[v])
    hcl = terraform(d)
    assert "${" not in hcl.replace("$${", "")
    assert "%{" not in hcl.replace("%%{", "")
    assert "\r" not in hcl
    assert schema_errors(hcl) == []
    payload = json.dumps(nerdgraph_dashboard(d))
    assert json.loads(payload)["variables"]["dashboard"]["name"] == text


@pytest.mark.parametrize("bad", ["a b", "a}}", "a'b", "a\nb", "a-b", "$(x)"])
def test_hostile_variable_names_cannot_be_constructed(bad: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        IRVariable(name=bad, title="t", type=VariableType.STRING)


def test_terraform_for_a_full_dashboard_has_one_header_and_valid_names() -> None:
    d = with_variables()
    text = dashboards_terraform([(1, d), (2, d)])
    assert text.count('provider "newrelic"') == 1
    assert schema_errors(text) == []


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_terraform_for_the_new_fields_validates(tmp_path: Path) -> None:
    d = with_variables()
    (tmp_path / "main.tf").write_text(dashboards_terraform([(1, d)]))
    terraform_bin = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform_bin, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("cannot download the New Relic provider (offline or blocked)")
    check = subprocess.run(  # noqa: S603
        [terraform_bin, "validate", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr


def _parent_and_child() -> tuple[IRVariable, IRVariable]:
    parent = IRVariable(
        name="env", title="Env", type=VariableType.NRQL, nrql="FROM T SELECT uniques(e)"
    )
    child = IRVariable(
        name="machine",
        title="Machine",
        type=VariableType.NRQL,
        nrql="FROM T SELECT uniques(m) WHERE e IN ({{env}})",
    )
    return parent, child


def test_a_nested_variable_is_written_after_its_parent_in_both_outputs() -> None:
    parent, child = _parent_and_child()
    d = dash(widget(), variables=[parent, child])
    names = [v["name"] for v in nerdgraph_dashboard(d)["variables"]["dashboard"]["variables"]]
    assert names == ["env", "machine"]
    tf = terraform(d)
    assert tf.index('name = "env"') < tf.index('name = "machine"')
    assert "{{env}}" in tf


def test_the_emitters_refuse_a_child_listed_before_its_parent() -> None:
    parent, child = _parent_and_child()
    d = dash(widget(), variables=[parent, child]).model_copy(update={"variables": [child, parent]})
    with pytest.raises(CannotEmit, match="before"):
        nerdgraph_dashboard(d)
    with pytest.raises(CannotEmit, match="before"):
        terraform(d)


def test_the_emitters_refuse_a_variable_query_that_names_an_undefined_variable() -> None:
    _, child = _parent_and_child()
    parent, _ = _parent_and_child()
    d = dash(widget(), variables=[parent, child]).model_copy(update={"variables": [child]})
    with pytest.raises(CannotEmit, match="env"):
        nerdgraph_dashboard(d)
