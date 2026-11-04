from pathlib import Path

import pytest

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import Confidence, IRMonitor
from importer.core.remediation import steps_for
from importer.sources import DASHBOARD_REGISTRY, REGISTRY

FIXTURES = Path("tests/fixtures")


def _monitors() -> list[IRMonitor]:
    out: list[IRMonitor] = []
    for name, folder in (("cloudwatch", "cloudwatch"), ("datadog", "datadog")):
        src = REGISTRY[name]
        out += src.translate(src.load(FIXTURES / folder))
    return out


@pytest.mark.parametrize("monitor", _monitors(), ids=lambda m: m.name[:40].replace("\n", " "))
def test_every_unconverted_alert_has_remediation(monitor: IRMonitor) -> None:
    if monitor.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
        pytest.skip("converted")
    assert monitor.remediation or steps_for(monitor.reasons), monitor.reasons


def test_every_failed_widget_has_remediation() -> None:
    src = DASHBOARD_REGISTRY["cloudwatch"]
    dashboards: list[IRDashboard] = src.translate(src.load(FIXTURES / "cloudwatch-dashboards"))
    for dash in dashboards:
        for w in dash.widgets:
            if w.confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED):
                assert steps_for(w.reasons), (w.title, w.reasons)


def test_steps_are_deduplicated_and_ordered() -> None:
    steps = steps_for(["anomaly detection alarms are not supported yet"] * 2)
    assert len(steps) == len(set(steps)) == 2
