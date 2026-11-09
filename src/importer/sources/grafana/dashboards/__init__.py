"""Grafana dashboards: read side and coverage plan (no New Relic dashboard is built here).

Importing this package registers the manual-step rules and the taxonomy rules for the
dashboard reasons. The dashboard fixtures are not registered with the improvement measure:
that measure converts alert exports through a `Source`, and dashboards have no converter yet.
"""

from importer.sources.grafana.dashboards import (
    remediation_rules,  # noqa: F401  (registers its rules)
)
