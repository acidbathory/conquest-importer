"""Signals about each dashboard that an extract folder holds, read without any network call.

The extract keeps Grafana's own dashboard JSON plus a small `meta` block. Everything here is a
hint for ranking, never a fact about use: only a usage file (see `usage.py`) says what is viewed.
"""

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable

MAX_FILES = 20_000
MAX_QUERIES = 2_000  # per dashboard, for the duplicate check
MIN_QUERIES_FOR_DUPLICATE = 2
NEAR_DUPLICATE = 0.8  # share of queries two dashboards have in common (Jaccard)
SKIP_FILES = {"folders.json", "library-panels.json", "plan.json"}
FLAG_WORDS = {
    "test": "test",
    "copy": "copy",
    "old": "old",
    "tmp": "tmp",
    "temp": "tmp",
    "deprecated": "deprecated",
}
_WORD = re.compile(r"[a-z]+")
QUERY_KEYS = ("expr", "query", "rawSql", "expression", "queryText")


@dataclass
class Signals:
    uid: str
    title: str
    folder: str = ""
    tags: tuple[str, ...] = ()
    starred: bool = False
    version: int | None = None
    updated: str = ""
    provisioned: bool = False
    community: bool = False  # imported from grafana.com (a gnetId is set)
    panel_count: int = 0
    panel_types: Counter[str] = field(default_factory=Counter)
    datasources: tuple[str, ...] = ()
    title_flags: tuple[str, ...] = ()
    empty: bool = False
    queries: frozenset[str] = frozenset()


def _walk(panels: Any) -> list[dict[str, Any]]:
    """Every panel, rows included in the walk but not counted by the caller as content."""
    found: list[dict[str, Any]] = []
    for p in panels if isinstance(panels, list) else []:
        if isinstance(p, dict):
            found.append(p)
            found += _walk(p.get("panels"))
    return found


def _ds_name(ref: Any) -> str:
    if isinstance(ref, dict):
        return str(ref.get("type") or ref.get("uid") or "")
    return str(ref) if isinstance(ref, str) and not ref.startswith("$") else ""


def title_flags(title: str) -> tuple[str, ...]:
    """Whole words in a title that mark it as test, copy, old, temporary or deprecated."""
    seen: list[str] = []
    for word in _WORD.findall(title.lower()):
        flag = FLAG_WORDS.get(word)
        if flag and flag not in seen:
            seen.append(flag)
    return tuple(seen)


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def signals_for(doc: dict[str, Any], folder_titles: dict[str, str]) -> Signals | None:
    inner = doc.get("dashboard")
    dash: dict[str, Any] = inner if isinstance(inner, dict) else doc
    raw_meta = doc.get("meta")
    meta: dict[str, Any] = raw_meta if isinstance(raw_meta, dict) else {}
    if "panels" not in dash and "rows" not in dash:
        return None
    title = printable(str(dash.get("title") or ""), 200)
    uid = printable(str(dash.get("uid") or meta.get("uid") or title), 200)
    panels = _walk(dash.get("panels")) + _walk(
        [p for r in dash.get("rows") or [] if isinstance(r, dict) for p in r.get("panels") or []]
    )
    content = [p for p in panels if p.get("type") != "row"]
    types = Counter(printable(str(p.get("type") or "unknown"), 60) for p in content)
    sources: list[str] = []
    queries: set[str] = set()
    for p in content:
        refs = [p.get("datasource")] + [
            t.get("datasource") for t in p.get("targets") or [] if isinstance(t, dict)
        ]
        for name in (_ds_name(x) for x in refs):
            if name and name not in sources:
                sources.append(printable(name, 60))
        for t in p.get("targets") or []:
            if isinstance(t, dict):
                for key in QUERY_KEYS:
                    if isinstance(t.get(key), str) and t[key].strip():
                        queries.add(" ".join(t[key].split())[:2000])
    folder_uid = str(meta.get("folderUid") or "")
    tags = dash.get("tags")
    return Signals(
        uid=uid,
        title=title,
        folder=printable(str(meta.get("folderTitle") or folder_titles.get(folder_uid, "")), 120),
        tags=tuple(printable(str(t), 60) for t in tags[:50]) if isinstance(tags, list) else (),
        starred=meta.get("isStarred") is True,
        version=_int(dash.get("version")) or _int(meta.get("version")),
        updated=printable(str(meta.get("updated") or ""), 40),
        provisioned=meta.get("provisioned") is True,
        community=bool(dash.get("gnetId")),
        panel_count=len(content),
        panel_types=types,
        datasources=tuple(sources[:20]),
        title_flags=title_flags(title),
        empty=not content,
        queries=frozenset(sorted(queries)[:MAX_QUERIES]),
    )


def _read(path: Path) -> Any:
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _folders(root: Path) -> dict[str, str]:
    data = _read(root / "folders.json") if root.is_dir() else None
    items = data.get("folders") if isinstance(data, dict) else None
    return {
        str(f["uid"]): str(f.get("title", ""))
        for f in items or []
        if isinstance(f, dict) and "uid" in f
    }


def read_signals(path: Path) -> list[Signals]:
    """Signals for every dashboard file under `path` (a folder or one file), in file-name order."""
    files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
    titles = _folders(path)
    out: list[Signals] = []
    seen: set[str] = set()
    for file in [f for f in files if f.name not in SKIP_FILES][:MAX_FILES]:
        data = _read(file)
        for doc in data if isinstance(data, list) else [data]:
            s = signals_for(doc, titles) if isinstance(doc, dict) else None
            if s and s.uid not in seen:  # the converter also keeps the first of a repeated uid
                seen.add(s.uid)
                out.append(s)
    return out


def duplicate_groups(signals: list[Signals]) -> list[list[str]]:
    """Groups of dashboard uids whose panel queries are the same or nearly so (Jaccard >= 0.8).

    A dashboard with fewer than two queries is never grouped; it has too little to compare.
    """
    cand = [s for s in signals if len(s.queries) >= MIN_QUERIES_FOR_DUPLICATE]
    parent = list(range(len(cand)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, a in enumerate(cand):
        for j in range(i + 1, len(cand)):
            b = cand[j]
            if len(a.queries & b.queries) / len(a.queries | b.queries) >= NEAR_DUPLICATE:
                parent[find(j)] = find(i)
    groups: dict[int, list[str]] = {}
    for i, s in enumerate(cand):
        groups.setdefault(find(i), []).append(s.uid)
    return [g for g in groups.values() if len(g) > 1]
