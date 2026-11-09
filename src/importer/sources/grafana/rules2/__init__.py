"""Deeper alert-rule conversion for Grafana: PromQL forms that the plain selector handler skips.

Loaded by `import_subpackages`; each module registers what it adds with the translator.
"""

from importer.sources.grafana.rules2 import promql  # noqa: F401  (registers its forms)
