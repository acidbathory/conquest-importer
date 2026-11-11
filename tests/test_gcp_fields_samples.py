"""Conversion of Google's public sample policies (tests/fixtures/gcp-fields/SOURCES.md)."""

from collections import Counter
from pathlib import Path

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence, IRMonitor
from importer.sources.gcp.models import load_policies
from importer.sources.gcp.translate import translate_policy

SAMPLES = Path(__file__).parent / "fixtures" / "gcp-fields"
CONVERTED = (Confidence.EXACT, Confidence.APPROXIMATE)


def convert_all() -> dict[str, IRMonitor]:
    out: dict[str, IRMonitor] = {}
    for file in sorted(SAMPLES.glob("*.json")):
        (policy,) = load_policies(file)
        (monitor,) = translate_policy(policy)
        out[file.name] = monitor
    return out


def test_conditions_converted_per_kind() -> None:
    total: Counter[str] = Counter()
    done: Counter[str] = Counter()
    for monitor in convert_all().values():
        total[monitor.source_type] += 1
        done[monitor.source_type] += monitor.confidence in CONVERTED
    assert dict(total) == {"conditionThreshold": 18, "conditionAbsent": 5, "conditionMatchedLog": 8}
    # data-path classification: a metric New Relic does not poll (Ops Agent, Prometheus, custom)
    # converts as an approximation that names what must be sent, instead of refusing
    assert dict(done) == {"conditionThreshold": 16, "conditionAbsent": 5, "conditionMatchedLog": 8}


def test_what_stays_manual_is_explained() -> None:
    manual = {n: m for n, m in convert_all().items() if m.confidence is Confidence.MANUAL}
    reasons = {n.split("__")[0]: m.reasons[0] for n, m in manual.items()}
    assert sorted(reasons) == ["etcd-gke", "google-bigquery"]
    assert "histogram" in reasons["etcd-gke"]
    assert "several polls" in reasons["google-bigquery"]


def test_newly_converted_samples_are_real_queries_that_name_their_data_path() -> None:
    monitors = convert_all()
    for name, monitor in monitors.items():
        if monitor.confidence in CONVERTED and monitor.data_path not in (None, "native"):
            assert nrql_for(monitor).startswith(("SELECT ", "FROM ")), name
            assert (
                "FROM Metric" in nrql_for(monitor) or monitor.source_type == "conditionMatchedLog"
            )
            assert monitor.reasons[0].startswith("data path: ") or monitor.source_type == (
                "conditionMatchedLog"
            ), name
    assert nrql_for(monitors["apache__high-request-rate.v1.json"]) == (
        "SELECT sum(apache.requests) FROM Metric"
    )


def test_converted_samples_keep_their_fields() -> None:
    monitors = convert_all()
    gce = monitors["google-gce__host-error-log-detected-within-vm.v1.json"]
    assert gce.emit is not None and gce.emit.violation_time_limit_seconds == 3600
    assert "Documentation format: text/markdown" in (gce.emit.description_note or "")
    assert "logName LIKE '%/logs/cloudaudit.googleapis.com%system_event'" in nrql_for(gce)
    sap = monitors["google-cloud-agent-for-sap__host-memory-alert.v1.json"]
    assert sap.emit is not None and sap.emit.violation_time_limit_seconds == 300
    assert "gcp-severity:CRITICAL" in sap.tags


def test_the_couchdb_two_stage_policy_is_one_grouped_sum_of_rates() -> None:
    """ALIGN_RATE, then REDUCE_SUM by instance over the same 300s: one stage."""
    monitor = convert_all()["couchdb__couchdb-high-request-rate.v1.json"]
    assert monitor.confidence in CONVERTED
    assert nrql_for(monitor) == ("SELECT sum(couchdb.httpd.requests) FROM Metric FACET instance_id")
