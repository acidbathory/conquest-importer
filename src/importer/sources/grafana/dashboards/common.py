"""Tolerant readers for untrusted JSON values: nothing here raises on a wrong type."""

import math
import re
from dataclasses import dataclass
from typing import Any

from importer.sources.grafana.dashboards.models import (
    MAX_TEXT,
    Dashboard,
    DsRef,
)

LOKI_HINT = re.compile(r"count_over_time|\|=|\|~|^\s*\{")


@dataclass(frozen=True)
class Found:
    """A dashboard body located inside whatever envelope the file or API response used."""

    shape: str  # classic, v1 or v2
    root: dict[str, Any]
    api_version: str = ""
    name: str = ""  # metadata.name of a resource envelope (the uid)
    folder_uid: str = ""
    folder_title: str = ""
    both: bool = False  # the body has classic panels and v2 elements


def text(value: object, limit: int = MAX_TEXT) -> str:
    """The value when it is a string, cut to `limit`; else an empty string."""
    return value[:limit] if isinstance(value, str) else ""


def note(dash: Dashboard, message: str) -> None:
    """Add a note to the dashboard once."""
    if message not in dash.warnings:
        dash.warnings.append(message)


def mapping(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def whole(value: object, low: int, high: int, default: int) -> int:
    """A whole number inside [low, high]; a bool, text, NaN or infinity gives `default`."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return default
    if isinstance(value, float) and not math.isfinite(value):
        return default
    return max(low, min(high, int(value)))


def truthy(value: object) -> bool:
    """True for `true` and for the old numeric 1; nothing else."""
    return value is True or (isinstance(value, int) and not isinstance(value, bool) and value == 1)


def schema_version(value: object) -> int | None:
    """The schemaVersion as a number between 0 and 1000, else None."""
    if isinstance(value, str):
        digits = value.strip()
        # ASCII only: str.isdigit() accepts superscripts that int() rejects
        ok = digits.isascii() and digits.isdecimal() and len(digits) <= 4
        value = int(digits) if ok else None
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= 1000 else None


def ds_ref(value: object) -> DsRef | None:
    """A data source reference in any of its spellings (object, name, template, legacy -100)."""
    if isinstance(value, dict):
        kind = text(value.get("type") or value.get("group"))
        uid = value.get("uid") or value.get("name")
        return DsRef(type=kind, uid=text(uid))
    if isinstance(value, str):
        return DsRef(uid=text(value))
    if isinstance(value, int) and not isinstance(value, bool):
        return DsRef(uid=str(value))
    return None


# keys that say which kind of data source a query was written for
KEY_HINTS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("rawSql",), "mysql"),
    (("namespace", "metricName"), "cloudwatch"),
    (("metricType",), "stackdriver"),
    (("bucketAggs",), "elasticsearch"),
    (("measurement",), "influxdb"),
    (("azureMonitor",), "grafana-azure-monitor-datasource"),
    (("azureLogAnalytics",), "grafana-azure-monitor-datasource"),
    (("scenarioId",), "grafana-testdata-datasource"),
    (("target", "refCount"), "graphite"),
)


def target_hint(raw: dict[str, Any]) -> str:
    """The data source type a query's keys point to (used when the reference says nothing)."""
    if "expr" in raw:  # a query text that is not a string still came from a PromQL-style editor
        expr = raw["expr"]
        return "loki" if isinstance(expr, str) and LOKI_HINT.search(expr[:2000]) else "prometheus"
    for keys, hint in KEY_HINTS:
        if all(key in raw for key in keys):
            return hint
    return ""
