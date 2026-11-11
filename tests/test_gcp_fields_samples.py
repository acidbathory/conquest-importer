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
    assert dict(done) == {"conditionThreshold": 4, "conditionAbsent": 4, "conditionMatchedLog": 8}


def test_what_stays_manual_is_explained() -> None:
    manual = {n: m for n, m in convert_all().items() if m.confidence is Confidence.MANUAL}
    assert len(manual) == 15
    uncollected = [
        m
        for m in manual.values()
        if "not one of the Google Cloud" in m.reasons[-1]
        or "not one of the Google Cloud" in m.reasons[0]
    ]
    assert len(uncollected) == 14
    (other,) = (m for m in manual.values() if m not in uncollected)
    assert "several polls" in other.reasons[0]


def test_converted_samples_keep_their_fields() -> None:
    monitors = convert_all()
    gce = monitors["google-gce__host-error-log-detected-within-vm.v1.json"]
    assert gce.emit is not None and gce.emit.violation_time_limit_seconds == 3600
    assert "Documentation format: text/markdown" in (gce.emit.description_note or "")
    assert "logName LIKE '%/logs/cloudaudit.googleapis.com%system_event'" in nrql_for(gce)
    sap = monitors["google-cloud-agent-for-sap__host-memory-alert.v1.json"]
    assert sap.emit is not None and sap.emit.violation_time_limit_seconds == 300
    assert "gcp-severity:CRITICAL" in sap.tags
