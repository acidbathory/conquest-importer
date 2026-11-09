"""Synthetic monitors, synthetic locations, SLO lists and inventory entities, read tolerantly.

Same rules as the dashboard models: each object is validated alone, a bad one is counted and
skipped, sizes and nesting are capped, and no text from the input reaches a reason. Inputs are
the files `extract-dynatrace-inventory` writes (`synthetics/`, `entities/`) and the SLO list
`extract-dynatrace` writes (`slos/slos.json`), or API responses saved by hand.
"""

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from importer.core.ids import MAX_INPUT_BYTES

MAX_MONITORS = 10_000
MAX_ENTITIES = 500_000
MAX_SLOS = 10_000
MAX_DEPTH = 64
MAX_LIST_ITEMS = 20


class Monitor(BaseModel):
    model_config = ConfigDict(frozen=True)

    ref: str  # position in the file; stands in for the id in messages
    name: str = ""  # untrusted: show only through md_cell
    type: str = ""  # HTTP, BROWSER, NETWORK_AVAILABILITY, ...
    enabled: bool = True
    frequency_min: int | None = None
    locations: list[str] = []
    script: dict[str, Any] = {}
    outage_handling: bool = False  # global or local outage handling is on
    outage_runs: int | None = None  # consecutive failed runs that open a global outage
    loading_thresholds: bool = False
    loading_ms: int | None = None  # the first loading-time threshold


class Location(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    name: str = ""
    city: str = ""
    country: str = ""  # country code
    kind: str = ""  # PUBLIC, PRIVATE, CLUSTER


class Entity(BaseModel):
    model_config = ConfigDict(frozen=True)

    type: str
    technologies: list[str] = []
    os: str = ""


class Slo(BaseModel):
    model_config = ConfigDict(frozen=True)

    kind: str  # "custom" (customSli), "template" (sliReference) or "classic" (metricExpression)
    timeframe: str = ""


class Loaded[T](BaseModel):
    items: list[T] = []
    skipped: int = 0
    reported_total: int | None = None  # totalCount from the API, when it was given


def _depth_exceeds(value: Any) -> bool:
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        item, depth = stack.pop()
        if depth > MAX_DEPTH:
            return True
        children = (
            item.values() if isinstance(item, dict) else item if isinstance(item, list) else ()
        )
        stack.extend((child, depth + 1) for child in children)
    return False


def read_json(path: Path) -> Any:
    """The parsed file; ValueError (with a fixed message) when it is too big, deep or not JSON."""
    try:
        if path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError(f"{path.name} is larger than the planner accepts")
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, RecursionError, OSError) as exc:
        if isinstance(exc, ValueError) and "larger than" in str(exc):
            raise
        raise ValueError(f"{path.name} is not readable JSON") from None
    if _depth_exceeds(data):
        raise ValueError(f"{path.name} is nested more deeply than the planner accepts")
    return data


def json_files(path: Path, folder: str, prefix: str) -> list[Path]:
    """`path` itself, or `<prefix>*.json` in `path/<folder>` (or in `path` when there is none)."""
    if path.is_file():
        return [path]
    base = path / folder if (path / folder).is_dir() else path
    return sorted(base.glob(f"{prefix}*.json"))[:1000]


def _items(data: Any, key: str) -> tuple[list[Any], int | None]:
    """The list of objects in a response `{key: [...]}`, a bare list, or one object."""
    if isinstance(data, dict):
        total = data.get("totalCount")
        found = data.get(key)
        if isinstance(found, list):
            return found, total if isinstance(total, int) else None
        return [data], None
    return (data if isinstance(data, list) else []), None


def _text(value: object, limit: int = 200) -> str:
    return value[:limit] if isinstance(value, str) else ""


def _strings(value: object) -> list[str]:
    found = value if isinstance(value, list) else []
    return [s[:200] for s in found[:MAX_LIST_ITEMS] if isinstance(s, str)]


def _small(value: object) -> int | None:
    """A whole number in a sane range (it goes into a plan field), else None."""
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 0 < value < 10**7
        else None
    )


def _first_threshold(thresholds: object) -> object:
    items = thresholds.get("thresholds") if isinstance(thresholds, dict) else None
    first = items[0] if isinstance(items, list) and items else None
    return first.get("valueMs") if isinstance(first, dict) else None


def parse_monitor(ref: str, raw: object) -> Monitor | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None
    script = raw.get("script")
    anomaly = raw.get("anomalyDetection")
    anomaly = anomaly if isinstance(anomaly, dict) else {}
    outage = anomaly.get("outageHandling")
    outage = outage if isinstance(outage, dict) else {}
    thresholds = anomaly.get("loadingTimeThresholds")
    policy = outage.get("globalOutagePolicy")
    frequency = raw.get("frequencyMin")
    return Monitor(
        ref=ref,
        name=_text(raw.get("name")),
        type=raw["type"][:40],
        enabled=raw.get("enabled") is not False,
        frequency_min=frequency if isinstance(frequency, int) else None,
        locations=_strings(raw.get("locations")),
        script=script if isinstance(script, dict) else {},
        outage_handling=bool(outage.get("globalOutage") or outage.get("localOutage")),
        outage_runs=_small(policy.get("consecutiveRuns") if isinstance(policy, dict) else None),
        loading_thresholds=isinstance(thresholds, dict) and thresholds.get("enabled") is True,
        loading_ms=_small(_first_threshold(thresholds)),
    )


def parse_location(raw: object) -> Location | None:
    if not isinstance(raw, dict):
        return None
    ident = raw.get("entityId") or raw.get("id")
    if not isinstance(ident, str):
        return None
    return Location(
        id=ident[:80],
        name=_text(raw.get("name")),
        city=_text(raw.get("city")),
        country=_text(raw.get("countryCode"), 8),
        kind=_text(raw.get("type"), 20),
    )


def parse_entity(raw: object) -> Entity | None:
    if not isinstance(raw, dict) or not isinstance(raw.get("type"), str):
        return None
    props = raw.get("properties")
    props = props if isinstance(props, dict) else {}
    return Entity(
        type=raw["type"][:80],
        technologies=_strings(props.get("serviceTechnologyTypes") or raw.get("technologies")),
        os=_text(props.get("osType") or raw.get("os"), 40),
    )


def parse_slo(raw: object) -> Slo | None:
    if not isinstance(raw, dict):
        return None
    if isinstance(raw.get("customSli"), dict):
        criteria = raw.get("criteria")
        first = criteria[0] if isinstance(criteria, list) and criteria else {}
        frame = first.get("timeframeFrom") if isinstance(first, dict) else ""
        return Slo(kind="custom", timeframe=_text(frame, 40))
    if isinstance(raw.get("sliReference"), dict):
        return Slo(kind="template")
    if "metricExpression" in raw or "evaluationType" in raw:
        return Slo(
            kind="classic", timeframe=_text(raw.get("evaluationWindow") or raw.get("timeframe"), 40)
        )
    return None


def _load[T](
    path: Path,
    where: tuple[str, str, str],
    parse: Callable[[str, Any], T | None],
    cap: int,
) -> Loaded[T]:
    """Read `<prefix>*.json` from `<folder>`; `where` is (folder, file prefix, response key)."""
    folder, prefix, key = where
    loaded: Loaded[T] = Loaded()
    for file in json_files(path, folder, prefix):
        try:
            data = read_json(file)
        except ValueError:
            loaded.skipped += 1
            continue
        items, total = _items(data, key)
        if total is not None:
            loaded.reported_total = (loaded.reported_total or 0) + total
        for n, item in enumerate(items):
            if len(loaded.items) >= cap:
                raise ValueError(f"more than {cap} {key}; split the export")
            parsed = parse(f"{file.name}[{n}]", item)
            if parsed is None:
                loaded.skipped += 1
            else:
                loaded.items.append(parsed)
    return loaded


def load_monitors(path: Path) -> Loaded[Monitor]:
    return _load(path, ("synthetics", "monitors", "monitors"), parse_monitor, MAX_MONITORS)


def load_locations(path: Path) -> Loaded[Location]:
    return _load(
        path,
        ("synthetics", "locations", "locations"),
        lambda _ref, raw: parse_location(raw),
        MAX_MONITORS,
    )


def load_entities(path: Path) -> Loaded[Entity]:
    return _load(
        path,
        ("entities", "entities", "entities"),
        lambda _ref, raw: parse_entity(raw),
        MAX_ENTITIES,
    )


def load_slos(path: Path) -> Loaded[Slo]:
    return _load(path, ("slos", "slos", "slos"), lambda _ref, raw: parse_slo(raw), MAX_SLOS)
