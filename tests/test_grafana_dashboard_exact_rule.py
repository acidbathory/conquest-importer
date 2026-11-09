"""When a converted Grafana tile may be called exact.

The rule (see `may_be_exact` in panels.py): a tile is exact only when it holds static content the
converter copies verbatim: a row heading or a markdown text panel with no note. A tile that
runs a query is never exact, because three things cannot be proved offline: the bucket New
Relic picks differs from the Grafana step, counter versus gauge is read from the metric name,
and an instant value is the latest point in Grafana but a whole-range value in New Relic.
"""

from pathlib import Path

import pytest

from importer.core.dashboard_ir import Layout, Visualization, WidgetKind
from importer.core.ir import Confidence
from importer.sources.grafana.dashboard_convert.name_map import NameEntry, NameMap
from importer.sources.grafana.dashboard_convert.panels import Draft, may_be_exact
from importer.sources.grafana.dashboard_convert.translate import convert_all, load_items

FIXTURES = Path(__file__).parent / "fixtures"
DIRS = ["grafana-dashboard-convert", "grafana-dashboards", "grafana-dashboard-exact"]


def widgets(
    names: NameMap | None = None,
) -> list[tuple[str, str, Confidence, WidgetKind, list[str]]]:
    found = []
    for name in DIRS:
        path = FIXTURES / name
        if not path.exists():
            continue
        dashboards, _ = convert_all(load_items(path), names)
        for dash in dashboards:
            for w in dash.widgets:
                found.append((name, w.source_type, w.confidence, w.kind, w.reasons))
    return found


def test_only_static_content_is_ever_exact_in_the_fixtures() -> None:
    for _, source_type, confidence, kind, reasons in widgets():
        if confidence == Confidence.EXACT:
            assert source_type in {"row", "text"}
            assert kind == WidgetKind.MARKDOWN and not reasons


def test_a_name_map_does_not_make_a_query_tile_exact() -> None:
    names = NameMap(metrics={"up": NameEntry(event="Metric", attribute="up")})
    for _, source_type, confidence, _kind, _ in widgets(names):
        if source_type not in {"row", "text"}:
            assert confidence != Confidence.EXACT


@pytest.mark.parametrize(
    ("kind", "notes", "expected"),
    [
        (WidgetKind.MARKDOWN, [], True),
        (WidgetKind.MARKDOWN, ["a note"], False),
        (WidgetKind.CHART, [], False),
        (WidgetKind.UNSUPPORTED, [], False),
    ],
)
def test_the_rule(kind: WidgetKind, notes: list[str], expected: bool) -> None:
    assert may_be_exact(kind, notes) is expected


def test_a_chart_draft_cannot_carry_exact() -> None:
    draft = Draft(
        WidgetKind.CHART,
        "stat",
        "t",
        Confidence.EXACT,
        [],
        Visualization.BILLBOARD,
        ["SELECT latest(up) FROM Metric"],
    )
    with pytest.raises(ValueError, match="exact"):
        draft.widget(Layout(column=1, row=1, width=4, height=3))
