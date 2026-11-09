"""The rules2 fixture folder: what converts, to which NRQL, and why the others stay manual."""

import importlib
from pathlib import Path

import pytest

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor
from importer.sources import REGISTRY
from importer.sources.base import EXTRA_FIXTURES

# the package re-exports the function `translate`, which hides the module of that name
t = importlib.import_module("importer.sources.grafana.translate")
RULES2 = Path(__file__).parent / "fixtures" / "grafana-rules2"

CONVERTED = {
    "r2-counter-rate": (
        "SELECT sum(http_requests_total) / 300 FROM Metric WHERE env = 'prod' FACET service"
    ),
    "r2-counter-increase": "SELECT sum(orders_total) FROM Metric",
    "r2-error-ratio": "SELECT sum(errors_total) / sum(requests_total) FROM Metric",
    "r2-memory-percent": "SELECT average(mem_used) / average(mem_total) * 100 FROM Metric",
    "r2-payments-absent": (
        "SELECT count(*) FROM Metric WHERE metricName = 'up' AND job = 'payments'"
    ),
    "r2-heartbeat-absent-over-time": (
        "SELECT count(*) FROM Metric WHERE metricName = 'heartbeat_total'"
    ),
    "r2-classic-single": "SELECT average(node_cpu_busy) FROM Metric",
    "r2-resample-then-reduce": "SELECT average(queue_depth) FROM Metric",
    "ApiErrorBurst": (
        "SELECT count(*) FROM Log WHERE env = 'prod' AND app LIKE 'api%' "
        "AND message LIKE '%error%' FACET app"
    ),
    "PaymentsDown": "SELECT count(*) FROM Metric WHERE metricName = 'up' AND job = 'payments'",
    "PaymentsErrorRate": ("SELECT sum(payments_errors_total) / 300 FROM Metric WHERE env = 'prod'"),
    "QueueDeep": (
        "SELECT average(queue_depth) FROM Metric WHERE queue IN ('orders', 'billing') FACET queue"
    ),
}
MANUAL = {
    "r2-error-ratio-one-metric": t.R_RATIO_SHAPE,
    "r2-up-compared-in-query": t.R_COMPARE_AND_NODE,
    "r2-rate-of-a-gauge": t.R_COUNTER_NAME,
    "r2-math-over-two-queries": t.R_MATH_QUERIES.format(count=2),
    "r2-classic-two-conditions": t.R_CLASSIC,
    "ApiLatencyFromLogs": t.LOKI_PARSER,
    "app:error_lines:rate5m": t.R_RECORD,
    "LatencyP95": "promql uses histogram_quantile",
    "job:payments_errors:rate5m": t.R_RECORD,
}


@pytest.fixture(scope="module")
def monitors() -> dict[str, IRMonitor]:
    found = REGISTRY["grafana"].convert(RULES2)
    assert len(found) == len(CONVERTED) + len(MANUAL)
    return {m.name: m for m in found}


@pytest.mark.parametrize(("title", "nrql"), sorted(CONVERTED.items()))
def test_each_converted_rule_gives_its_query(
    monitors: dict[str, IRMonitor], title: str, nrql: str
) -> None:
    m = monitors[title]
    assert m.confidence is Confidence.APPROXIMATE and nrql_for(m) == nrql


@pytest.mark.parametrize(("title", "reason"), sorted(MANUAL.items()))
def test_each_manual_rule_has_its_one_reason(
    monitors: dict[str, IRMonitor], title: str, reason: str
) -> None:
    assert monitors[title].confidence is Confidence.MANUAL
    assert monitors[title].reasons == [reason]


def test_the_folder_is_part_of_the_grafana_measure() -> None:
    assert "tests/fixtures/grafana-rules2" in EXTRA_FIXTURES["grafana"]
