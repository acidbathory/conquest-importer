"""Grafana source: Grafana-managed alert rules to New Relic."""

from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.base import Source, import_subpackages, register
from importer.sources.grafana import remediation_rules  # noqa: F401  (registers its rules)
from importer.sources.grafana.models import GrafanaRule, load_rules
from importer.sources.grafana.rules2 import promql  # noqa: F401  (registers its PromQL forms)
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

# reasons of the PromQL forms (counters, ratios, absence, comparisons); anchored to their wording
register_taxonomy(
    Category.UNSUPPORTED_QUERY,
    r"^promql (?:counter (?:function|rate|range)|equality or inequality|ratio:|absent\(\) returns 1"
    r"|expression compares inside the query)",
)
register_taxonomy(Category.MANUAL_FOLLOWUP, r"^grafana origin:")
register_taxonomy(Category.APPROXIMATION, r"^this rule comes from a prometheus-style rule file")

# Feature sub-packages (src/importer/sources/grafana/<feature>/) load themselves; see
# docs/dev/parallel-work.md.
import_subpackages(__name__, __path__)
