"""The converted pending period must say when New Relic opens the incident (measured live)."""

from pathlib import Path

from importer.sources import REGISTRY

RULES = Path(__file__).parent.parent / "scripts/live-test/grafana-sandbox/alert-fire/rules.json"


def test_the_pending_note_warns_that_the_incident_opens_later_than_in_grafana() -> None:
    monitors = REGISTRY["grafana"].convert(RULES)
    cpu = next(m for m in monitors if m.name == "ckg-synth-cpu")
    pending = [r for r in cpu.reasons if r.startswith("the pending period becomes")]
    assert len(pending) == 1
    assert "aggregation delay" in pending[0]
    assert "opens" in pending[0]
