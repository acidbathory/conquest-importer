"""Azure Monitor source: alert rules to New Relic."""

from importer.sources.azure.models import AzureAlert, load_alerts
from importer.sources.azure.translate import translate
from importer.sources.base import Source, import_subpackages, register

SOURCE: Source[AzureAlert] = Source(
    name="azure",
    input_help=(
        "file or folder with the JSON `extract-azure` writes (one list page per resource type)"
    ),
    load=load_alerts,
    translate=translate,
)
register(SOURCE)

# Feature sub-packages (src/importer/sources/azure/<feature>/) load themselves; see
# docs/dev/parallel-work.md.
import_subpackages(__name__, __path__)
