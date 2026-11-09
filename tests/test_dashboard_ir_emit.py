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
    Severity,
    Threshold,
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
    assert (
        "TIMESERIES" not in queries(dash(widget(series=auto, visualization=Visualization.PIE)))[0]
    )
    fixed = queries(dash(widget()))[0]
    assert fixed.endswith("TIMESERIES 60 SECONDS")


def test_raw_series_follow_the_metric_series_unchanged() -> None:
    raw = [RawNrqlSeries(label="x", nrql="SELECT count(*) FROM Log FACET host TIMESERIES AUTO")]
    d = dash(widget(raw_series=raw))
    assert queries(d)[1] == raw[0].nrql
    assert raw[0].nrql in terraform(d)
    only_raw = dash(widget(series=[], raw_series=raw))
    assert queries(only_raw) == [raw[0].nrql]


def test_a_chart_with_only_raw_series_is_not_turned_into_a_placeholder() -> None:
    raw = [RawNrqlSeries(nrql="SELECT 1 FROM Log")]
    assert nerdgraph_widget(dash(widget(series=[], raw_series=raw)))["visualization"] == {
        "id": "viz.line"
    }


# --- height ------------------------------------------------------------------------------------


@pytest.mark.parametrize("emit", [nerdgraph_dashboard, terraform])
def test_both_emitters_refuse_a_height_the_provider_refuses(emit: Any) -> None:
    tall = widget(layout=Layout(column=1, row=1, width=4, height=13))
    with pytest.raises(CannotEmit, match="12"):
        emit(dash(tall))


def test_the_provider_maximum_height_is_still_emitted() -> None:
    d = dash(widget(layout=Layout(column=1, row=1, width=4, height=12)))
    assert "height = 12" in terraform(d)


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
    th = [Threshold(severity=Severity.CRITICAL, from_value=1)]
    with_options = nerdgraph_widget(dash(widget(options=opts, thresholds=th)))
    assert with_options == plain


def test_line_and_table_thresholds_become_threshold_blocks() -> None:
    th = [
        Threshold(severity=Severity.WARNING, from_value=50, to_value=80, name='a "b"'),
        Threshold(severity=Severity.CRITICAL, from_value=80),
    ]
    line = terraform(dash(widget(thresholds=th)))
    assert 'name = "a \\"b\\""' in line
    assert '        severity = "critical"' in line and '        from = "80"' in line
    assert "to = " not in line.split("critical")[1]
    table_th = [Threshold(severity=Severity.SEVERE, to_value=5, column_name='c"ol')]
    table = terraform(dash(widget(visualization=Visualization.TABLE, thresholds=table_th)))
    assert 'column_name = "c\\"ol"' in table
    for text in (line, table):
        assert schema_errors(text) == []


def test_billboard_thresholds_become_critical_and_warning() -> None:
    th = [
        Threshold(severity=Severity.CRITICAL, from_value=90),
        Threshold(severity=Severity.WARNING, from_value=70.5),
    ]
    text = terraform(dash(widget(visualization=Visualization.BILLBOARD, thresholds=th)))
    assert '      critical = "90"' in text and '      warning = "70.5"' in text
    assert "threshold {" not in text


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


def test_a_variable_in_a_built_query_must_be_defined() -> None:
    d = dash(widget(series=[Series(signal=signal("{{ghost}}"), period_seconds=60)]))
    with pytest.raises(CannotEmit, match="ghost"):
        nerdgraph_dashboard(d)


def test_a_variable_in_a_built_query_clause_that_forbids_it_is_refused() -> None:
    raw = RawNrqlSeries(nrql="SELECT count(*) FROM Log FACET host LIMIT 5")
    ok = dash(widget(raw_series=[raw]))
    assert queries(ok)
    sig = signal("{{v}}")
    bad = Series(label="{{v}}", signal=sig, period_seconds=60)  # a variable inside AS
    v = IRVariable(name="v", title="V", type=VariableType.STRING)
    with pytest.raises(CannotEmit, match="AS"):
        nerdgraph_dashboard(dash(widget(series=[bad]), variables=[v]))


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
    t = Threshold(severity=Severity.WARNING, from_value=1, name=text)
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
