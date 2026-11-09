"""Grafana source: Grafana-managed alert rules to New Relic."""

from importer.sources.base import Source, register
from importer.sources.grafana import remediation_rules  # noqa: F401  (registers its rules)
from importer.sources.grafana.models import GrafanaRule, load_rules
from importer.sources.grafana.translate import translate

SOURCE: Source[GrafanaRule] = Source(
    name="grafana",
    input_help=(
        "file or folder with Grafana alert rules in any read shape "
        "(the output of `importer extract-grafana`)"
    ),
    load=load_rules,
    translate=translate,
)
register(SOURCE)
