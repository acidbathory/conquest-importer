from pathlib import Path

import pytest

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import Confidence, IRMonitor
from importer.core.remediation import steps_for
from importer.sources import DASHBOARD_REGISTRY, REGISTRY

FIXTURES = Path("tests/fixtures")


def _monitors() -> list[IRMonitor]:
    out: list[IRMonitor] = []
    for name, folder in (("cloudwatch", "cloudwatch"), ("gcp", "gcp"), ("dynatrace", "dynatrace")):
        src = REGISTRY[name]
        out += src.translate(src.load(FIXTURES / folder))
    return out


@pytest.mark.parametrize("monitor", _monitors(), ids=lambda m: m.name[:40].replace("\n", " "))
def test_every_unconverted_alert_has_remediation(monitor: IRMonitor) -> None:
    if monitor.confidence in (Confidence.EXACT, Confidence.APPROXIMATE):
        pytest.skip("converted")
    assert monitor.remediation or steps_for(monitor.reasons), monitor.reasons


@pytest.mark.parametrize(
    ("name", "folder"), [("cloudwatch", "cloudwatch-dashboards"), ("gcp", "gcp-dashboards")]
)
def test_every_failed_widget_has_remediation(name: str, folder: str) -> None:
    src = DASHBOARD_REGISTRY[name]
    dashboards: list[IRDashboard] = src.translate(src.load(FIXTURES / folder))
    for dash in dashboards:
        for w in dash.widgets:
            if w.confidence in (Confidence.MANUAL, Confidence.UNSUPPORTED):
                assert steps_for(w.reasons), (w.title, w.reasons)


def test_steps_are_deduplicated_and_ordered() -> None:
    steps = steps_for(["anomaly detection alarms are not supported yet"] * 2)
    assert len(steps) == len(set(steps)) == 2


@pytest.mark.parametrize(
    ("reason", "title"),
    [
        ("forecast conditions are not converted", "Forecast condition"),
        ("this alarm is a forecast alarm", "Forecast or outlier monitor"),
        ("metric math expressions in widgets are not supported yet", "Metric math in a widget"),
        ("metric math functions are not supported yet", "Metric math expression"),
    ],
)
def test_a_specific_rule_is_not_shadowed_by_a_broader_one(reason: str, title: str) -> None:
    from importer.core.remediation import remedies_for

    assert [r.title for r in remedies_for([reason])] == [title]


@pytest.mark.parametrize(
    "reason",
    [
        "not emitted: no evaluation window",
        "not emitted: no thresholds",
        "not emitted: evaluation window 99s is outside 60s-24h",
        "not emitted: duration 90s is not a multiple of the 60s aggregation window",
        "not emitted: signal kind heartbeat is not supported",
        "not emitted: confidence is manual",
        "alarm has no static threshold",
        "metric type 'x' is not a recognised name",
    ],
)
def test_emitter_limit_reasons_have_guidance(reason: str) -> None:
    assert steps_for([reason])


def test_registering_the_same_rules_twice_adds_nothing() -> None:
    import importlib

    from importer.core import remediation
    from importer.sources.gcp import remediation_rules

    before = len(remediation._COMPILED)
    importlib.reload(remediation_rules)
    assert len(remediation._COMPILED) == before
