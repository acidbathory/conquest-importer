"""Signals read offline from an extract folder."""

import json
from pathlib import Path
from typing import Any

from importer.sources.grafana.triage.signals import duplicate_groups, read_signals


def _dash(uid: str, title: str, panels: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    meta = {"folderTitle": "Ops", "provisioned": False, **extra.pop("meta", {})}
    return {"dashboard": {"uid": uid, "title": title, "panels": panels, **extra}, "meta": meta}


def _panel(expr: str, kind: str = "timeseries", ds: str = "prometheus") -> dict[str, Any]:
    return {
        "type": kind,
        "title": "p",
        "datasource": {"type": ds, "uid": "x"},
        "targets": [{"refId": "A", "expr": expr}],
    }


def _write(tmp: Path, *dashes: dict[str, Any]) -> Path:
    (tmp / "dashboards").mkdir()
    for i, d in enumerate(dashes):
        (tmp / "dashboards" / f"dashboard-{i}.json").write_text(json.dumps(d))
    return tmp


def test_basic_signals(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        _dash(
            "a",
            "API latency",
            [_panel("up"), {"type": "row", "panels": [_panel("rate(x[5m])", "stat")]}],
            tags=["prod", "api"],
            version=7,
            meta={"isStarred": True, "updated": "2026-01-02T03:04:05Z"},
        ),
    )
    (s,) = read_signals(root)
    assert (s.uid, s.folder, s.tags, s.starred, s.version) == ("a", "Ops", ("prod", "api"), True, 7)
    assert s.updated == "2026-01-02T03:04:05Z"
    assert s.panel_count == 2 and dict(s.panel_types) == {"timeseries": 1, "stat": 1}
    assert s.datasources == ("prometheus",) and not s.empty and not s.provisioned


def test_title_flags_empty_and_community(tmp_path: Path) -> None:
    root = _write(
        tmp_path,
        _dash("t", "Test dash", [_panel("up")]),
        _dash("c", "Copy of API", [_panel("up")]),
        _dash("o", "OLD node", [_panel("up")]),
        _dash("tmpx", "tmp stuff", [_panel("up")]),
        _dash("d", "[DEPRECATED] x", [_panel("up")]),
        _dash("e", "Latest", []),
        _dash("g", "Nginx", [_panel("up")], gnetId=9614, meta={"provisioned": True}),
        _dash("n", "Contest results", [_panel("up")]),
    )
    by = {s.uid: s for s in read_signals(root)}
    assert by["t"].title_flags == ("test",)
    assert by["c"].title_flags == ("copy",)
    assert by["o"].title_flags == ("old",)
    assert by["tmpx"].title_flags == ("tmp",)
    assert by["d"].title_flags == ("deprecated",)
    assert by["n"].title_flags == ()  # "contest" is not "test"
    assert by["e"].empty and not by["g"].empty
    assert by["g"].provisioned and by["g"].community


def test_duplicates_exact_and_near(tmp_path: Path) -> None:
    same = [_panel(f"m{i}") for i in range(10)]
    near = [*same[:9], _panel("other")]
    root = _write(
        tmp_path,
        _dash("a", "A", same),
        _dash("b", "B", same),
        _dash("c", "C", near),
        _dash("z", "Z", [_panel("unrelated")]),
        _dash("e1", "E1", []),
        _dash("e2", "E2", []),
    )
    sigs = read_signals(root)
    groups = duplicate_groups(sigs)
    assert groups == [["a", "b", "c"]]  # empty dashboards are never "duplicates"
