from pathlib import Path

from importer.core.ir import IRMonitor
from importer.sources.base import Source, register
from importer.sources.cloudwatch.models import CloudWatchAlarm, CompositeAlarm, load_alarms
from importer.sources.cloudwatch.translate import (
    load_namespace_map,
    translate_alarm,
    translate_composite,
)

type Alarm = CloudWatchAlarm | CompositeAlarm


def load(path: Path) -> list[Alarm]:
    metric, composite = load_alarms(path)
    return [*metric, *composite]


def translate(alarms: list[Alarm]) -> list[IRMonitor]:
    prefixes = load_namespace_map()
    return [
        translate_composite(a) if isinstance(a, CompositeAlarm) else translate_alarm(a, prefixes)
        for a in alarms
    ]


SOURCE = Source(
    name="cloudwatch",
    input_help="file or folder with the JSON from `aws cloudwatch describe-alarms`",
    load=load,
    translate=translate,
)
register(SOURCE)
