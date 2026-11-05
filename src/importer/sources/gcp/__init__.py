"""Google Cloud Monitoring source: alert policies to New Relic."""

from importer.sources.base import Source, register
from importer.sources.gcp import remediation_rules  # noqa: F401  (registers its rules)
from importer.sources.gcp.models import GcpAlertPolicy, load_policies
from importer.sources.gcp.translate import translate

SOURCE: Source[GcpAlertPolicy] = Source(
    name="gcp",
    input_help=(
        "file or folder with the JSON from `gcloud alpha monitoring policies list --format=json`"
    ),
    load=load_policies,
    translate=translate,
)
register(SOURCE)
