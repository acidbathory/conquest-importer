"""Dynatrace source: workflows and SLOs to New Relic."""

from importer.sources.base import Source, register
from importer.sources.dynatrace import remediation_rules  # noqa: F401  (registers its rules)
from importer.sources.dynatrace.models import DtItem, load_exports
from importer.sources.dynatrace.translate import translate

SOURCE: Source[DtItem] = Source(
    name="dynatrace",
    input_help=(
        "file or folder with workflow and SLO JSON (the output of `importer extract-dynatrace`)"
    ),
    load=load_exports,
    translate=translate,
)
register(SOURCE)
