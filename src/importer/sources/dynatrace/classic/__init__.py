"""Dynatrace classic and Davis alerting objects (Settings 2.0) to New Relic.

Reads metric events, Davis anomaly detectors, alerting profiles, problem notifications,
maintenance windows, management zones and the built-in anomaly detection settings. Metric events
and static detectors convert; the rest are listed as manual items. This package registers
everything it owns (its source, rules, taxonomy and fixtures) so no shared file needs an edit; see
docs/dev/parallel-work.md.
"""

from importer.sources.base import Source, register, register_fixtures
from importer.sources.dynatrace.classic import (
    remediation_rules,  # noqa: F401  (registers its rules)
    taxonomy_rules,  # noqa: F401  (registers its taxonomy)
)
from importer.sources.dynatrace.classic.models import SettingsObject, load_objects
from importer.sources.dynatrace.classic.translate import translate

SOURCE: Source[SettingsObject] = Source(
    name="dynatrace-classic",
    input_help=(
        "file or folder with Settings 2.0 objects (the output of "
        "`importer extract-dynatrace-classic`)"
    ),
    load=load_objects,
    translate=translate,
)
register(SOURCE)
register_fixtures("dynatrace-classic", "tests/fixtures/dynatrace-classic")
