"""Datadog source: monitors from exported JSON to New Relic (file-first, no credentials)."""

from importer.sources.base import Source, import_subpackages, register
from importer.sources.datadog import playbooks, remediation_rules, taxonomy  # noqa: F401
from importer.sources.datadog.models import DatadogMonitor, load_monitors
from importer.sources.datadog.translate import translate

SOURCE: Source[DatadogMonitor] = Source(
    name="datadog",
    input_help=(
        "file or folder with monitor JSON: the list `GET /api/v1/monitor` returns, one monitor, "
        "or integration monitor templates (no credentials needed); optional name-map.json beside"
    ),
    load=load_monitors,
    translate=translate,
)
register(SOURCE)
# Feature sub-packages (src/importer/sources/datadog/<feature>/) load themselves; see
# docs/dev/parallel-work.md.
import_subpackages(__name__, __path__)
