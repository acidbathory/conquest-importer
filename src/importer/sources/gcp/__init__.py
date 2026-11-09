"""Google Cloud Monitoring source: alert policies to New Relic."""

from importer.sources.base import DashboardSource, Source, register, register_dashboards
from importer.sources.gcp import remediation_rules  # noqa: F401  (registers its rules)
from importer.sources.gcp.dashboard_models import load_dashboards
from importer.sources.gcp.dashboards import translate_dashboards
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

DASHBOARDS = DashboardSource(
    name="gcp",
    input_help="file or folder with Cloud Monitoring dashboard JSON (extract-gcp writes it)",
    load=load_dashboards,
    translate=translate_dashboards,
)
register_dashboards(DASHBOARDS)
