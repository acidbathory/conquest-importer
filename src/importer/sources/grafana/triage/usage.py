"""The optional usage file: how often each dashboard was viewed, exported by the owner.

Grafana's REST API does not return per-dashboard view counts that we could verify, so the
numbers come from a file. JSON (a list, or an object with a `dashboards` list) or CSV, one row
per dashboard. Column names are matched loosely so an export from Usage Insights needs little
editing; rows without a readable uid or view count are ignored rather than guessed.
"""

import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from importer.core.ids import MAX_INPUT_BYTES

UID_KEYS = ("uid", "dashboarduid", "dashboard_uid", "dashboard uid")
VIEW_KEYS = ("views_30d", "views", "views30d", "views (30d)", "view_count", "viewcount")
LAST_KEYS = ("last_viewed", "lastviewed", "last viewed", "last_view", "lastview")


class UsageError(ValueError):
    pass


@dataclass(frozen=True)
class Usage:
    views_30d: int
    last_viewed: date | None


def _norm(row: dict[str, Any]) -> dict[str, Any]:
    return {str(k).strip().lower(): v for k, v in row.items() if k is not None}


def _pick(row: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for k in keys:
        if k in row:
            return row[k]
    return None


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _count(value: Any) -> int | None:
    try:
        n = int(float(str(value).replace(",", "").strip()))
    except ValueError:
        return None
    return n if n >= 0 else None


def _rows(path: Path) -> list[dict[str, Any]]:
    if path.stat().st_size > MAX_INPUT_BYTES:
        raise UsageError(f"{path.name} is larger than the importer reads")
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except ValueError as exc:
            raise UsageError(f"{path.name} is not valid JSON: {exc}") from None
        if isinstance(data, dict):
            data = data.get("dashboards")
        if not isinstance(data, list):
            raise UsageError(f"{path.name}: expected a list of rows or {{'dashboards': [...]}}")
        return [_norm(r) for r in data if isinstance(r, dict)]
    return [_norm(r) for r in csv.DictReader(io.StringIO(text))]


def load_usage(path: Path) -> dict[str, Usage]:
    """Usage by dashboard uid. Raises UsageError when no row has a uid and a view count column."""
    try:
        rows = _rows(path)
    except OSError as exc:
        raise UsageError(str(exc)) from None
    if rows and not any(_pick(r, UID_KEYS) is not None for r in rows):
        raise UsageError(f"{path.name}: no uid column (expected one of {', '.join(UID_KEYS)})")
    out: dict[str, Usage] = {}
    for r in rows:
        uid, views = str(_pick(r, UID_KEYS) or "").strip(), _count(_pick(r, VIEW_KEYS))
        if uid and views is not None:
            out[uid] = Usage(views, _date(_pick(r, LAST_KEYS)))
    return out
