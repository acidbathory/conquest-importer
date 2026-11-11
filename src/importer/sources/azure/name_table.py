"""Loader for `name_table.json`: Azure metric names sourced from documentation, not derived.

Each row joins a name printed in the New Relic Azure Monitor integration metrics table to the
Microsoft Learn definition of the metric (unit, aggregations, dimensions). Nothing in the file was
seen arriving in an account. See docs/research/azure-names-verified.md for the sources.
"""

import json
import re
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

PATH = Path(__file__).with_name("name_table.json")


@dataclass(frozen=True)
class Row:
    name: str
    unit: str
    aggregations: tuple[str, ...]
    dimensions: tuple[str, ...]
    scale: float  # multiply an Azure threshold by this to get one in New Relic's unit


@cache
def _data() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(PATH.read_text(encoding="utf-8"))
    return loaded


@cache
def _documented() -> frozenset[str]:
    return frozenset(str(n) for n in _data()["documented_names"])


def lookup(namespace: str, metric: str) -> Row | None:
    """The sourced row for an Azure namespace and REST metric name (any case), else None."""
    raw = _data()["metrics"].get(f"{namespace.strip().lower()}|{metric.strip().lower()}")
    if raw is None:
        return None
    default = _data().get("default_scale", 1.0)
    scale = raw.get("scale", default)
    return Row(
        name=str(raw["name"]),
        unit=str(raw["unit"]),
        aggregations=tuple(str(a) for a in raw["aggregations"]),
        dimensions=tuple(str(d) for d in raw["dimensions"]),
        scale=float(scale),
    )


def is_documented(name: str) -> bool:
    """True when this exact New Relic metric name is printed in the New Relic documentation."""
    return name in _documented()


def otel_metric_name(metric: str, aggregation: str) -> str:
    """The name the OpenTelemetry `azure_monitor` receiver gives one aggregation of a metric."""
    return f"azure_{re.sub(' ', '_', metric)}_{aggregation}".lower()
