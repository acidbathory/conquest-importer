"""Dynatrace dashboards: read side and plans (no New Relic dashboard objects yet).

Importing this package registers the remediation rules for every dashboard reason and the
taxonomy phrases for them. The phrases are anchored at the start of a reason, so they cannot
capture the wording of another source.

The fixture folder tests/fixtures/dynatrace-dashboards is not registered with
`register_fixtures`: its files are dashboards, not the workflows and SLOs that `convert
--source dynatrace` reads, so they would not belong in the conversion measure.
"""

from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.dynatrace.dashboards import remediation_rules  # noqa: F401  (registers)

for _category, _pattern in (
    (Category.UNSUPPORTED_QUERY, r"^dynatrace dashboard query:"),
    (Category.UNSUPPORTED_FEATURE, r"^dynatrace dashboard tile:"),
    (Category.UNSUPPORTED_QUERY, r"^dynatrace dashboard variable:"),
    (Category.APPROXIMATION, r"^dynatrace dashboard note:"),
    (Category.MISSING_MAPPING, r"^dynatrace dashboard data:"),
    (Category.MANUAL_FOLLOWUP, r"^dynatrace dashboard file:"),
    (Category.MISSING_MAPPING, r"^dynatrace classic dashboard: the tile charts"),
    (Category.UNSUPPORTED_FEATURE, r"^dynatrace classic dashboard: the tile type"),
    (Category.UNSUPPORTED_FEATURE, r"^dynatrace classic dashboard: a management zone"),
):
    register_taxonomy(_category, _pattern)
