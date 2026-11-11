"""Metrics that New Relic's Google integration does not poll still convert, with the data path
they need named on the item (owner policy: converted = a correct query exists for the metric as it
will arrive in New Relic)."""

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor
from importer.sources.gcp.datapath import MetricSource, classify
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy


def policy(filter_text: str, **body: object) -> GcpAlertPolicy:
    condition = {
        "filter": filter_text,
        "aggregations": [{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_MEAN"}],
        "comparison": "COMPARISON_GT",
        "thresholdValue": 0.8,
        "duration": "600s",
        **body,
    }
    return GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "combiner": "OR",
            "conditions": [{"displayName": "c", "conditionThreshold": condition}],
        }
    )


def convert(metric: str, **body: object) -> IRMonitor:
    (m,) = translate_policy(policy(f'metric.type="{metric}"', **body))
    return m


@pytest.mark.parametrize(
    ("metric", "source"),
    [
        ("compute.googleapis.com/instance/cpu/utilization", MetricSource.NATIVE),
        ("kubernetes.io/container/cpu/core_usage_time", MetricSource.NATIVE),
        ("agent.googleapis.com/memory/percent_used", MetricSource.OPS_AGENT),
        ("workload.googleapis.com/jvm.memory.used", MetricSource.OPS_AGENT),
        ("prometheus.googleapis.com/up/gauge", MetricSource.MANAGED_PROMETHEUS),
        ("external.googleapis.com/prometheus/up", MetricSource.MANAGED_PROMETHEUS),
        ("custom.googleapis.com/orders/failed", MetricSource.CUSTOM),
        ("external.googleapis.com/other/x", MetricSource.CUSTOM),
        ("logging.googleapis.com/user/timeouts", MetricSource.LOGGING_BASED),
        ("example.com/x", MetricSource.UNKNOWN),
        ("", MetricSource.UNKNOWN),
    ],
)
def test_classifier(metric: str, source: MetricSource) -> None:
    assert classify(metric) is source


def test_native_metric_carries_data_path_native() -> None:
    m = convert("compute.googleapis.com/instance/cpu/utilization")
    assert m.data_path == "native"
    assert "native" not in json.dumps(m.model_dump(mode="json"))  # pinned IR stays byte-identical


def test_managed_prometheus_keeps_its_prometheus_name_and_drops_the_type() -> None:
    m = convert("prometheus.googleapis.com/http_requests_total/counter", thresholdValue=5)
    assert m.confidence is Confidence.APPROXIMATE
    assert m.data_path == "needs-prometheus-remote-write"
    assert "http_requests_total" in nrql_for(m)
    assert "counter" not in nrql_for(m) and "gcp." not in nrql_for(m)
    assert m.reasons[0].startswith("data path: needs-prometheus-remote-write")


def test_prometheus_labels_arrive_as_plain_attributes() -> None:
    (m,) = translate_policy(
        policy(
            'metric.type="prometheus.googleapis.com/up/gauge" AND metric.labels.job="api"',
            aggregations=[
                {
                    "alignmentPeriod": "300s",
                    "perSeriesAligner": "ALIGN_MEAN",
                    "groupByFields": ["metric.label.instance"],
                }
            ],
        )
    )
    q = nrql_for(m)
    assert "job = 'api'" in q and "FACET instance" in q
    assert "metric.job" not in q


def test_prometheus_histogram_stays_manual_with_a_precise_reason() -> None:
    m = convert("prometheus.googleapis.com/latency_seconds/histogram")
    assert m.confidence is Confidence.MANUAL
    assert "histogram" in m.reasons[0] and m.data_path is None


def test_ops_agent_workload_metric_uses_the_receiver_name_and_is_unverified() -> None:
    m = convert("workload.googleapis.com/jvm.memory.used")
    assert m.confidence is Confidence.APPROXIMATE
    assert m.data_path == "needs-ops-agent-or-otel"
    assert "average(jvm.memory.used)" in nrql_for(m)
    assert any("UNVERIFIED" in r for r in m.reasons)


def test_ops_agent_legacy_metric_keeps_the_google_name_with_a_hint() -> None:
    m = convert("agent.googleapis.com/iis/request_count")  # no documented mapping: stays Google's
    assert m.data_path == "needs-ops-agent-or-otel"
    assert "agent.iis.request_count" in nrql_for(m)
    text = " ".join(m.reasons)
    assert "UNVERIFIED" in text and "iis.request.count" in text and "name map" in text


def test_custom_metric_needs_custom_ingest() -> None:
    m = convert("custom.googleapis.com/orders/failed")
    assert m.data_path == "needs-custom-ingest"
    assert "orders.failed" in nrql_for(m)
    assert any("UNVERIFIED" in r for r in m.reasons)


def test_percentile_aligner_on_a_non_polled_metric_is_manual() -> None:
    m = convert(
        "custom.googleapis.com/lat",
        aggregations=[{"alignmentPeriod": "300s", "perSeriesAligner": "ALIGN_PERCENTILE_95"}],
    )
    assert m.confidence is Confidence.MANUAL


@pytest.mark.parametrize("metric", ["logging.googleapis.com/user/timeouts", "example.com/x"])
def test_log_based_and_unknown_metrics_stay_manual(metric: str) -> None:
    m = convert(metric)
    assert m.confidence is Confidence.MANUAL and m.data_path is None


def test_absence_condition_on_a_prometheus_metric_converts() -> None:
    (m,) = translate_policy(
        GcpAlertPolicy.model_validate(
            {
                "displayName": "p",
                "conditions": [
                    {
                        "displayName": "c",
                        "conditionAbsent": {
                            "filter": 'metric.type="prometheus.googleapis.com/up/gauge"',
                            "duration": "600s",
                            "aggregations": [{"alignmentPeriod": "300s"}],
                        },
                    }
                ],
            }
        )
    )
    assert m.confidence is Confidence.APPROXIMATE
    assert m.data_path == "needs-prometheus-remote-write"


def test_log_match_condition_needs_log_forwarding() -> None:
    (m,) = translate_policy(
        GcpAlertPolicy.model_validate(
            {
                "displayName": "p",
                "conditions": [
                    {
                        "displayName": "c",
                        "conditionMatchedLog": {"filter": "severity>=ERROR"},
                    }
                ],
            }
        )
    )
    assert m.confidence is Confidence.APPROXIMATE and m.data_path == "needs-log-forwarding"


def _widget(metric: str) -> dict[str, object]:
    return {
        "title": "w",
        "xyChart": {
            "dataSets": [
                {
                    "timeSeriesQuery": {
                        "timeSeriesFilter": {
                            "filter": f'metric.type="{metric}"',
                            "aggregation": {
                                "alignmentPeriod": "60s",
                                "perSeriesAligner": "ALIGN_MEAN",
                            },
                        }
                    }
                }
            ]
        },
    }


def test_widgets_carry_the_data_path() -> None:
    from importer.core.dashboard_ir import Layout, WidgetKind
    from importer.sources.gcp.dashboards import Context, _safe_widget

    layout = Layout(column=1, row=1, width=4, height=3)
    native = _safe_widget(
        _widget("compute.googleapis.com/instance/cpu/utilization"), layout, Context()
    )
    prom = _safe_widget(_widget("prometheus.googleapis.com/up/gauge"), layout, Context())
    assert native.kind is WidgetKind.CHART and native.data_path == "native"
    assert prom.kind is WidgetKind.CHART and prom.data_path == "needs-prometheus-remote-write"
    assert any(r.startswith("data path:") for r in prom.reasons)
    assert "native" not in json.dumps(native.model_dump(mode="json"))


def test_plan_gcp_datapath_writes_a_plan_without_secrets(tmp_path: Path) -> None:
    ir = tmp_path / "alerts" / "ir"
    ir.mkdir(parents=True)
    for i, metric in enumerate(
        ["prometheus.googleapis.com/up/gauge", "workload.googleapis.com/jvm.memory.used"]
    ):
        m = convert(metric)
        (ir / f"{i}.json").write_text(m.model_dump_json())
    out = tmp_path / "plan"
    result = CliRunner().invoke(
        app, ["plan-gcp-datapath", "--alerts", str(tmp_path / "alerts"), "--out", str(out)]
    )
    assert result.exit_code == 0, result.output
    plan = (out / "datapath-plan.md").read_text()
    assert "needs-prometheus-remote-write" in plan and "needs-ops-agent-or-otel" in plan
    assert "up" in plan and "jvm.memory.used" in plan
    assert "UNVERIFIED" in plan
    assert (
        "REPLACE_WITH_YOUR_NEW_RELIC_LICENSE_KEY"
        in (out / "otel-collector-ops-agent.yml").read_text()
    )
    assert "remote_write" in (out / "prometheus-remote-write.yml").read_text()
    data = json.loads((out / "datapath-plan.json").read_text())
    assert data["classes"]["needs-prometheus-remote-write"]["metrics"] == ["up"]


def test_plan_gcp_datapath_refuses_an_existing_folder(tmp_path: Path) -> None:
    out = tmp_path / "plan"
    out.mkdir()
    (out / "keep.txt").write_text("x")
    result = CliRunner().invoke(app, ["plan-gcp-datapath", "--out", str(out)])
    assert result.exit_code != 0


def test_scoreboard_counts_data_path_classes_separately(tmp_path: Path) -> None:
    from importer.core import scoreboard as sb

    ir = tmp_path / "out" / "gcp" / "ir"
    ir.mkdir(parents=True)
    rows = [
        ("a", "approximate", None),  # gcp default: native
        ("b", "approximate", "needs-custom-ingest"),
        ("c", "manual", None),
    ]
    for name, conf, path in rows:
        data = {"source_system": "gcp", "name": name, "confidence": conf}
        if path:
            data["data_path"] = path
        (ir / f"{name}.json").write_text(json.dumps(data))
    other = tmp_path / "out" / "grafana" / "ir"
    other.mkdir(parents=True)
    (other / "g.json").write_text(
        json.dumps({"source_system": "grafana", "name": "g", "confidence": "exact"})
    )
    m = sb.gather([tmp_path / "out"], root=None, evidence=None, today="2026-11-11")["metrics"]
    assert m["out/gcp/alerts_converted"] == 2
    assert m["out/gcp/alerts_datapath_native"] == 1
    assert m["out/gcp/alerts_datapath_needs-custom-ingest"] == 1
    assert m["out/gcp/alerts_datapath_known"] == 2
    assert not any("grafana" in k and "datapath" in k for k in m)  # backward compatible
    assert sb.direction("out/gcp/alerts_datapath_native") == 0
