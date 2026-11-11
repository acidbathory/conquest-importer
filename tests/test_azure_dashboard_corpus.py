"""The official fixtures and 30 real public workbook templates: exact counts and no gaps.

The numbers are what the converter does today, with the KQL subset translator connected through
`dashboard_kql.py` (a KQL item converts when the query is an aggregate in the subset). They change
only when the converter or the fixtures change; update them with the reason in the commit.
Placeholders are never counted as converted.
"""

from pathlib import Path

import pytest

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.remediation import steps_for
from importer.core.taxonomy import Category, classify
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.azure.dashboard_measure import measure

FIXTURES = Path(__file__).parent / "fixtures"
SETS = {
    "workbook examples": FIXTURES / "azure" / "workbooks",
    "dashboard examples": FIXTURES / "azure" / "dashboards",
    "quickstart templates": FIXTURES / "azure" / "quickstart_templates",
    "public templates": FIXTURES / "azure-workbooks",
}
# (dashboards, widgets, converted, placeholders)
EXPECTED = {
    "workbook examples": (16, 26, 13, 13),
    "dashboard examples": (4, 9, 1, 8),
    "quickstart templates": (2, 10, 7, 3),
    "public templates": (30, 458, 274, 184),
}


def convert(name: str) -> list[IRDashboard]:
    return DASHBOARD_REGISTRY["azure"].convert(SETS[name])


@pytest.mark.parametrize("name", sorted(SETS))
def test_exact_counts(name: str) -> None:
    m = measure(convert(name))
    assert (m.dashboards, m.widgets, m.converted, m.placeholders) == EXPECTED[name]
    assert m.converted + m.placeholders == m.widgets


def test_the_public_templates_are_thirty_and_recorded() -> None:
    files = sorted(SETS["public templates"].glob("*.workbook"))
    assert len(files) == 30
    sources = (SETS["public templates"] / "SOURCES.md").read_text()
    assert "cc8922da42fcf1ef5ae308e076d9a7dc69698655" in sources and "MIT" in sources
    assert all(f.name in sources for f in files)
    assert (SETS["public templates"] / "LICENSE").read_text().lstrip().startswith("MIT License")


def test_ids_were_stripped_from_the_public_templates() -> None:
    import re

    guid = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
    for f in SETS["public templates"].glob("*.workbook"):
        found = set(guid.findall(f.read_text().lower())) - {"00000000-0000-0000-0000-000000000000"}
        assert not found, f.name
        assert '"chartId"' not in f.read_text()


@pytest.mark.parametrize("name", sorted(SETS))
def test_every_placeholder_has_manual_steps_and_every_reason_a_category(name: str) -> None:
    for d in convert(name):
        for w in d.widgets:
            for reason in w.reasons:
                assert classify(reason) is not Category.OTHER, reason
            if w.kind is WidgetKind.UNSUPPORTED:
                assert steps_for(w.reasons), w.reasons


@pytest.mark.parametrize("name", sorted(SETS))
def test_every_dashboard_that_is_not_a_total_placeholder_emits(name: str) -> None:
    for d in convert(name):
        if d.confidence.value in ("exact", "approximate"):
            assert nerdgraph_dashboard(d)
        else:
            with pytest.raises(CannotEmit):
                nerdgraph_dashboard(d)


def test_converted_metric_widgets_use_the_native_data_path() -> None:
    m = measure(convert("public templates"))
    # metric widgets read the Azure Monitor integration; only the KQL ones need log forwarding
    assert m.by_data_path["native"] > 100
    assert set(m.by_data_path) <= {"native", "needs-log-forwarding", "needs-agent", "unknown"}
    assert m.by_type["workbook metrics"] > 100
