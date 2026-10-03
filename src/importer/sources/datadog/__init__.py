from pathlib import Path

from importer.core.ir import IRMonitor
from importer.sources.base import Source, register
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.translate import load_metric_map, translate_monitor


def load(path: Path) -> list[DatadogMonitor]:
    return [DatadogMonitor.model_validate_json(p.read_text()) for p in sorted(path.glob("*.json"))]


def translate(monitors: list[DatadogMonitor]) -> list[IRMonitor]:
    metric_map = load_metric_map()
    return [translate_monitor(m, metric_map) for m in monitors]


SOURCE = Source(
    name="datadog",
    input_help="directory with one Datadog monitor JSON file per monitor",
    load=load,
    translate=translate,
)
register(SOURCE)
