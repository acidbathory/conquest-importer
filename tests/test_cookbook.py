"""The remediation cookbook: ranked methods per blocker, kept as data and checked for shape."""

import re

from importer.core import cookbook

STATUSES = {"VERIFIED-VALID", "VERIFIED-VALUE", "DOC-ONLY", "UNVERIFIED"}
REQUIRED = {
    "sum-gauge", "comparison", "variables", "joins", "without", "raw-counter", "over-time",
    "transformations", "histograms", "absent", "histogram-quantile", "predict", "multi-query",
    "recording-rules", "pending-nodata", "routing", "mute-timings", "chat-contacts",
}  # fmt: skip
EFFORT = re.compile(r"^\d+(-\d+)? (minutes|hours|days)$")


def test_every_required_blocker_is_present_with_ranked_methods() -> None:
    found = {b.key: b for b in cookbook.blockers()}
    assert REQUIRED <= set(found), REQUIRED - set(found)
    for key in REQUIRED:
        assert 3 <= len(found[key].methods) <= 6, key


def test_every_method_is_complete() -> None:
    for b in cookbook.blockers():
        assert b.title and b.scope and b.summary and b.ask and b.decision, b.key
        ids = [m.id for m in b.methods]
        assert len(ids) == len(set(ids)), b.key
        for m in b.methods:
            where = f"{b.key}/{m.id}"
            assert m.family in set("ABCDEF"), where
            assert m.status in STATUSES, where
            assert EFFORT.match(m.effort), f"{where}: {m.effort!r}"
            for field in (m.name, m.when, m.prerequisites, m.latency, m.changes, m.risk, m.verify):
                assert field.strip(), where
            assert m.evidence.strip(), where
            assert m.sources and all(s.startswith("https://") for s in m.sources), where
            assert m.examples, where
