"""The remediation cookbook: ranked methods per blocker, kept as data and checked for shape."""

import re

from importer.core import cookbook

STATUSES = {"VERIFIED-VALID", "VERIFIED-VALUE", "DOC-ONLY", "UNVERIFIED"}
REQUIRED = {
    "sum-gauge", "comparison", "variables", "joins", "without", "raw-counter", "over-time",
    "transformations", "histograms", "absent", "histogram-quantile", "predict", "multi-query",
    "recording-rules", "pending-nodata", "routing", "mute-timings", "chat-contacts",
}  # fmt: skip
EFFORT = re.compile(r"^\d+(-\d+)? (minutes?|hours?|days?)$")


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


# --- the user guide is rendered from the data, and its NRQL passes the project checks -----------
from pathlib import Path  # noqa: E402

from importer.core.cookbook.render import GUIDE_PATH, render_markdown  # noqa: E402
from importer.core.dashboard_nrql import check_raw_nrql  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
TAIL = re.compile(r"\s+(SINCE|UNTIL|COMPARE WITH)\b.*$", re.IGNORECASE | re.DOTALL)
# Not allowed in an alert condition query (see the NRQL alert conditions page).
CONDITION_FORBIDDEN = re.compile(
    r"\b(TIMESERIES|SLIDE BY|LIMIT|JOIN|COMPARE WITH|SINCE|UNTIL|histogram|cardinality)\b|FROM\s*\(",
    re.IGNORECASE,
)


def test_user_guide_is_up_to_date_with_the_data() -> None:
    text = (ROOT / GUIDE_PATH).read_text(encoding="utf-8")
    assert text == render_markdown(), "run: uv run python scripts/improve/render_cookbook.py"


def test_every_nrql_in_the_guide_is_extracted_and_passes_the_safety_checks() -> None:
    text = (ROOT / GUIDE_PATH).read_text(encoding="utf-8")
    in_guide = [m.strip() for m in re.findall(r"```nrql\n(.*?)\n```", text, re.DOTALL)]
    in_data = [
        e.text for b in cookbook.blockers() for m in b.methods for e in m.examples
        if e.kind in cookbook.NRQL_KINDS
    ]  # fmt: skip
    assert sorted(in_guide) == sorted(in_data)
    assert len(in_guide) >= 60
    for query in in_guide:
        check_raw_nrql(TAIL.sub("", query))


def test_condition_queries_obey_the_condition_rules() -> None:
    found = 0
    for b in cookbook.blockers():
        for m in b.methods:
            for e in m.examples:
                if e.kind == "nrql-alert":
                    found += 1
                    assert not CONDITION_FORBIDDEN.search(e.text), f"{b.key}/{m.id}: {e.text}"
    assert found >= 15


def test_example_kinds_are_known() -> None:
    known = set(cookbook.NRQL_KINDS) | set(cookbook.OTHER_KINDS)
    for b in cookbook.blockers():
        for m in b.methods:
            assert all(e.kind in known and e.text.strip() for e in m.examples), f"{b.key}/{m.id}"


def test_blocker_lookup_uses_title_then_pattern() -> None:
    comparison = cookbook.blocker_for("Dashboard query comparison or vector matching", "x == 1")
    joins = cookbook.blocker_for(
        "Dashboard query comparison or vector matching", "uses vector matching on(pod) group_left"
    )
    assert comparison is not None and comparison.key == "comparison"
    assert joins is not None and joins.key == "joins"
    assert cookbook.blocker_for("no such rule title") is None


def test_every_blocker_title_is_a_real_remediation_rule() -> None:
    import importer.sources  # noqa: F401
    from importer.core import remediation

    titles = {t for _, t, _ in remediation._COMPILED}
    for b in cookbook.blockers():
        for t in b.titles:
            assert t in titles, f"{b.key}: {t}"
