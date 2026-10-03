"""The small interface every source platform implements.

A source does two separate jobs, so each can be tested and reasoned about alone:
  load(path)      read the customer's export into typed source objects (no translation)
  translate(raw)  turn those objects into vendor-neutral IR; never raises, labels what it cannot do
Everything after the IR (emitters, report, apply) is shared and knows nothing about sources.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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
