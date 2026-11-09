"""The small interface every source platform implements.

A source does two separate jobs, so each can be tested and reasoned about alone:
  load(path)      read the customer's export into typed source objects (no translation)
  translate(raw)  turn those objects into vendor-neutral IR; never raises, labels what it cannot do
Everything after the IR (emitters, report, apply) is shared and knows nothing about sources.
"""

import importlib
import pkgutil
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from importer.core.dashboard_ir import IRDashboard
from importer.core.ir import IRMonitor


@dataclass(frozen=True)
class Source[T]:
    name: str
    input_help: str  # what --in must point at, shown in CLI help
    load: Callable[[Path], list[T]]
    translate: Callable[[list[T]], list[IRMonitor]]

    def convert(self, path: Path) -> list[IRMonitor]:
        return self.translate(self.load(path))


REGISTRY: dict[str, Source[Any]] = {}


def register(source: Source[Any]) -> None:
    REGISTRY[source.name] = source


@dataclass(frozen=True)
class DashboardSource[T]:
    """Same two-step shape as Source, for dashboards."""

    name: str
    input_help: str
    load: Callable[[Path], list[T]]
    translate: Callable[[list[T]], list[IRDashboard]]

    def convert(self, path: Path) -> list[IRDashboard]:
        return self.translate(self.load(path))


DASHBOARD_REGISTRY: dict[str, DashboardSource[Any]] = {}


def register_dashboards(source: DashboardSource[Any]) -> None:
    DASHBOARD_REGISTRY[source.name] = source


# Extra fixture folders per source for the improvement loop's offline measure. A source (or a
# parallel branch) registers its own folders here instead of editing core/improve.py.
EXTRA_FIXTURES: dict[str, list[str]] = {}


def register_fixtures(source: str, *folders: str) -> None:
    """Add fixture folders (relative to the repository root) for a source; idempotent."""
    known = EXTRA_FIXTURES.setdefault(source, [])
    known.extend(f for f in folders if f not in known)


def import_subpackages(package: str, path: Iterable[str]) -> list[str]:
    """Import every sub-package of a source package (e.g. `importer.sources.grafana.dashboards`).

    A parallel branch adds a sub-package for its feature; its `__init__` registers whatever it
    owns (rules, taxonomy, fixtures, dashboard translators). Nobody edits the source's own
    `__init__`. Names starting with `_` are skipped. Returns the imported names, sorted.
    """
    loaded: list[str] = []
    for info in sorted(pkgutil.iter_modules(path), key=lambda m: m.name):
        if info.ispkg and not info.name.startswith("_"):
            importlib.import_module(f"{package}.{info.name}")
            loaded.append(info.name)
    return loaded
