"""Settings objects to IR monitors. One monitor per object; nothing is dropped silently.

A metric event or a Davis detector converts when it can and is manual with a reason when it
cannot. Every other object is an inventory row. An object that could not be read at all is a
manual item that carries the loader's problem text. `translate` never raises for an object.
"""

from importer.core.ir import Confidence, IRMonitor
from importer.core.text import printable
from importer.sources.dynatrace.classic import models
from importer.sources.dynatrace.classic.common import monitor
from importer.sources.dynatrace.classic.detectors import translate_detector
from importer.sources.dynatrace.classic.inventory import translate_inventory
from importer.sources.dynatrace.classic.metric_events import translate_metric_event
from importer.sources.dynatrace.classic.models import SettingsObject

UNREADABLE = "the settings object could not be read: {problem}"
NAME_LIMIT = 80


class Keys:
    """Hands out unique keys: the object id, then 'id#2', 'id#3'... skipping any key in use."""

    def __init__(self) -> None:
        self.used: set[str] = set()
        self.next_suffix: dict[str, int] = {}

    def take(self, base: str) -> str:
        suffix = self.next_suffix.get(base, 1)
        key = base
        while key in self.used:
            suffix += 1
            key = f"{base}#{suffix}"
        self.used.add(key)
        self.next_suffix[base] = suffix
        return key


def object_key(obj: SettingsObject, index: int, keys: Keys) -> str:
    """The object id, made unique. Without an id the position stands in."""
    return keys.take(obj.objectId or f"{obj.schemaId or 'object'}/{index}")


def _convert(obj: SettingsObject, key: str) -> IRMonitor:
    if obj.problem:
        return monitor(
            key=key,
            name=printable(obj.objectId or obj.schemaId or "Settings object", NAME_LIMIT),
            source_type="settings object",
            confidence=Confidence.MANUAL,
            reasons=[UNREADABLE.format(problem=obj.problem)],
        )
    if obj.schemaId == models.METRIC_EVENTS:
        return translate_metric_event(obj, key)
    if obj.schemaId == models.DETECTORS:
        return translate_detector(obj, key)
    return translate_inventory(obj, key)


def translate(objects: list[SettingsObject]) -> list[IRMonitor]:
    keys = Keys()
    return [_convert(obj, object_key(obj, i, keys)) for i, obj in enumerate(objects)]
