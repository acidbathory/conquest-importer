"""Usage file loading and the score and bucket rules."""

import json
from datetime import date
from pathlib import Path

import pytest

from importer.sources.grafana.triage.score import Bucket, Verdict, decide
from importer.sources.grafana.triage.signals import Signals
from importer.sources.grafana.triage.usage import Usage, UsageError, load_usage

TODAY = date(2026, 11, 10)


def test_usage_from_csv_with_aliases(tmp_path: Path) -> None:
    f = tmp_path / "u.csv"
    f.write_text("Dashboard UID,Views,Last Viewed\na,12,2026-11-01\nb,0,\nc,x,2026-01-01\n")
    got = load_usage(f)
    assert got["a"] == Usage(views_30d=12, last_viewed=date(2026, 11, 1))
    assert got["b"] == Usage(views_30d=0, last_viewed=None)
    assert "c" not in got  # an unreadable count is not guessed


def test_usage_from_json_list_and_wrapper(tmp_path: Path) -> None:
    rows = [{"uid": "a", "views_30d": 3, "last_viewed": "2026-10-30T10:00:00Z"}]
    for name, data in (("l.json", rows), ("w.json", {"dashboards": rows})):
        f = tmp_path / name
        f.write_text(json.dumps(data))
        assert load_usage(f)["a"] == Usage(3, date(2026, 10, 30))


def test_usage_errors(tmp_path: Path) -> None:
    f = tmp_path / "bad.json"
    f.write_text("{nope")
    with pytest.raises(UsageError):
        load_usage(f)
    g = tmp_path / "nocols.csv"
    g.write_text("a,b\n1,2\n")
    with pytest.raises(UsageError, match="uid"):
        load_usage(g)


def _sig(**kw: object) -> Signals:
    base: dict[str, object] = {"uid": "a", "title": "API", "panel_count": 5}
    base.update(kw)
    return Signals(**base)  # type: ignore[arg-type]


def v(sig: Signals, usage: Usage | None = None, dup_of: str = "", conv: float = 1.0) -> Verdict:
    return decide(sig, usage, dup_of=dup_of, convertible=conv, today=TODAY)


def test_skip_reasons() -> None:
    assert v(_sig(empty=True, panel_count=0)).bucket is Bucket.SKIP
    assert v(_sig(title_flags=("test",))).bucket is Bucket.SKIP
    assert v(_sig(title_flags=("copy",))).bucket is Bucket.SKIP
    assert "duplicate of b" in v(_sig(), dup_of="b").reasons[0]
    assert v(_sig(), Usage(0, None)).bucket is Bucket.SKIP
    assert v(_sig(), Usage(5, date(2025, 1, 1))).bucket is Bucket.SKIP  # not viewed for 300 days
    assert v(_sig(provisioned=True)).bucket is Bucket.SKIP


def test_usage_decides_now_or_later() -> None:
    assert v(_sig(), Usage(40, date(2026, 11, 8))).bucket is Bucket.NOW
    assert v(_sig(), Usage(1, date(2026, 10, 20))).bucket is Bucket.LATER
    # the same dashboard without usage is only "later": no usage data, no claim it matters
    assert v(_sig()).bucket is Bucket.LATER
    assert v(_sig(starred=True)).bucket is Bucket.NOW


def test_old_flag_and_poor_conversion_lower_the_score() -> None:
    good = v(_sig(), Usage(40, date(2026, 11, 8)))
    assert v(_sig(title_flags=("old",)), Usage(40, date(2026, 11, 8))).score < good.score
    assert v(_sig(), Usage(40, date(2026, 11, 8)), conv=0.0).score < good.score


def test_provisioned_with_views_is_kept() -> None:
    assert v(_sig(provisioned=True), Usage(30, date(2026, 11, 9))).bucket is Bucket.NOW


def test_quickstart_hook_is_empty_until_a_map_exists() -> None:
    from importer.sources.grafana.triage.score import quickstart_for

    assert quickstart_for(_sig(community=True)) is None
