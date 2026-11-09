"""Grafana notification objects: contact points, policies, mute timings, templates, rule files.

Read side and pure plans. Importing this package registers the remediation rules and taxonomy
patterns for the notification reasons (see `remediation_rules.py`).
"""

from importer.sources.grafana.notifications import remediation_rules  # noqa: F401  (registers)
