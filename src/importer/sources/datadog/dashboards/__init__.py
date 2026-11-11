"""Datadog dashboards: widgets, groups and template variables to New Relic dashboards.

Importing this package registers the dashboard source (`convert-dashboards --source datadog`),
the manual-step rules, the playbooks and the taxonomy rules for the dashboard reasons. The monitor
translator in the parent package is reused for queries, scopes and the name map.
"""

from importer.sources.base import DashboardSource, register_dashboards
from importer.sources.datadog.dashboards import (  # noqa: F401  (each registers its own rules)
    playbooks,
    rules,
)
from importer.sources.datadog.dashboards.models import DatadogDashboard, load_dashboards
from importer.sources.datadog.dashboards.translate import translate_dashboards

DASHBOARDS: DashboardSource[DatadogDashboard] = DashboardSource(
    name="datadog",
    input_help=(
        "file or folder with Datadog dashboard JSON (the body `GET /api/v1/dashboard/{id}` "
        "returns, or a list of them); optional name-map.json beside"
    ),
    load=load_dashboards,
    translate=translate_dashboards,
)
register_dashboards(DASHBOARDS)
