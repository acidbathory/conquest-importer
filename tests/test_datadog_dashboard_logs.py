"""Datadog dashboard widgets over logs: a count or a measure aggregate becomes a Log query.

The search is translated by the monitor's log search translator. Every conversion is approximate
(log attribute names are unverified and the logs must be forwarded to New Relic); a search the
translator refuses, or a source other than logs, stays a placeholder.
"""

from pathlib import Path
from typing import Any

import pytest

from importer.core.dashboard_ir import Visualization, WidgetKind
from importer.core.ir import Confidence
from test_datadog_dashboards import one, widget


def logq(
    search: str | None = "service:web status:error",
    compute: dict[str, Any] | None = None,
    group_by: list[dict[str, Any]] | None = None,
    source: str = "logs",
) -> dict[str, Any]:
    q: dict[str, Any] = {
        "name": "q",
        "data_source": source,
        "compute": compute or {"aggregation": "count"},
        "group_by": group_by or [],
        "indexes": ["*"],
    }
    if search is not None:
        q["search"] = {"query": search}
    return q


def lw(kind: str, q: dict[str, Any], **fields: Any) -> dict[str, Any]:
    return widget(kind, requests=[{"queries": [q], "formulas": [{"formula": "q"}], **fields}])


def test_log_count_timeseries_with_group_by(tmp_path: Path) -> None:
    group = [{"facet": "host", "limit": 5, "sort": {"order": "desc", "aggregation": "count"}}]
    _, item, queries = one(tmp_path, lw("timeseries", logq(group_by=group)))
    assert item.kind is WidgetKind.CHART
    assert item.confidence is Confidence.APPROXIMATE
    assert item.visualization is Visualization.LINE
    assert queries == [
        "SELECT count(*) FROM Log WHERE service = 'web' AND status = 'error' "
        "FACET host LIMIT 5 TIMESERIES AUTO"
    ]
    assert item.data_path == "needs-log-forwarding"
    text = " ".join(item.reasons)
    assert "UNVERIFIED" in text
    assert "forwarded" in text


def test_log_query_without_a_formula_list(tmp_path: Path) -> None:
    w = widget("timeseries", requests=[{"queries": [logq()]}])
    _, item, queries = one(tmp_path, w)
    assert item.kind is WidgetKind.CHART
    assert queries == [
        "SELECT count(*) FROM Log WHERE service = 'web' AND status = 'error' TIMESERIES AUTO"
    ]


def test_log_measure_aggregate_query_value(tmp_path: Path) -> None:
    q = logq(compute={"aggregation": "avg", "metric": "@duration"})
    _, item, queries = one(tmp_path, lw("query_value", q))
    assert item.visualization is Visualization.BILLBOARD
    assert queries == [
        "SELECT average(duration) FROM Log WHERE service = 'web' AND status = 'error'"
    ]


def test_log_toplist_facets_without_timeseries(tmp_path: Path) -> None:
    q = logq(group_by=[{"facet": "@http.status_code", "limit": 10}])
    _, item, queries = one(tmp_path, lw("toplist", q))
    assert item.visualization is Visualization.BAR
    assert queries == [
        "SELECT count(*) FROM Log WHERE service = 'web' AND status = 'error' "
        "FACET http.status_code LIMIT 10"
    ]


def test_log_query_value_with_group_by_is_a_placeholder(tmp_path: Path) -> None:
    q = logq(group_by=[{"facet": "host"}])
    _, item, _ = one(tmp_path, lw("query_value", q))
    assert item.kind is WidgetKind.UNSUPPORTED


@pytest.mark.parametrize(
    "q",
    [
        logq(search="@a:(b OR c*) AND NOT (x y)"),  # NOT over a group: the translator refuses
        logq(search=None),
        logq(search="service:$svc"),  # a dashboard template variable
        logq(compute={"aggregation": "pc95", "metric": "@d"}),
        logq(source="rum"),
        logq(source="events"),
    ],
)
def test_log_widgets_that_stay_placeholders(tmp_path: Path, q: dict[str, Any]) -> None:
    _, item, _ = one(tmp_path, lw("timeseries", q))
    assert item.kind is WidgetKind.UNSUPPORTED
    assert item.confidence is Confidence.MANUAL
    assert item.reasons[0].startswith("dashboard widget reads ")


def test_a_log_formula_over_two_queries_stays_a_placeholder(tmp_path: Path) -> None:
    w = widget(
        "timeseries",
        requests=[
            {
                "queries": [logq(), {**logq(), "name": "r"}],
                "formulas": [{"formula": "q / r"}],
            }
        ],
    )
    _, item, _ = one(tmp_path, w)
    assert item.kind is WidgetKind.UNSUPPORTED


def with_vars(tmp_path: Path, w: dict[str, Any], variables: list[dict[str, Any]]) -> Any:
    from test_datadog_dashboards import one as one_

    return one_(tmp_path, w, variables=variables)


def test_bare_prefixed_variable_becomes_an_in_filter(tmp_path: Path) -> None:
    d, item, queries = with_vars(
        tmp_path,
        lw("timeseries", logq(search="service:web $host")),
        [{"name": "host", "prefix": "host", "default": "*"}],
    )
    assert item.kind is WidgetKind.CHART
    assert queries[0] == (
        "SELECT count(*) FROM Log WHERE service = 'web' AND host IN ({{host}}) TIMESERIES AUTO"
    )
    assert "{{host}}" in queries[0]
    assert [v.name for v in d.variables] == ["host"]
    assert "FROM Log" in d.variables[0].nrql


def test_keyed_variable_value(tmp_path: Path) -> None:
    _, item, queries = with_vars(
        tmp_path,
        lw("toplist", logq(search="@repo.name:$repo", group_by=[{"facet": "host"}])),
        [{"name": "repo", "prefix": "@repo.name", "default": "*"}],
    )
    assert item.kind is WidgetKind.CHART
    assert "repo.name IN ({{repo}})" in queries[0]


def test_prefixless_variable_uses_its_labelled_default(tmp_path: Path) -> None:
    _, item, queries = with_vars(
        tmp_path,
        lw("timeseries", logq(search="$scope")),
        [{"name": "scope", "default": "service:web"}],
    )
    assert item.kind is WidgetKind.CHART
    assert "service = 'web'" in queries[0]
    assert any("default" in r for r in item.reasons)


@pytest.mark.parametrize("search", ["$nope", "@a:$v.value", "svc:web$v", "@a:(x OR $v)"])
def test_unreadable_variable_uses_stay_placeholders(tmp_path: Path, search: str) -> None:
    _, item, _ = with_vars(
        tmp_path,
        lw("timeseries", logq(search=search)),
        [{"name": "v", "prefix": "a", "default": "*"}],
    )
    assert item.kind is WidgetKind.UNSUPPORTED
    assert item.reasons[0].startswith("dashboard widget reads logs")
