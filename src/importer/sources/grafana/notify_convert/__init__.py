"""Convert Grafana contact points, notification policies and mute timings to New Relic.

Reads the folder written by `extract-grafana-notifications` and the converted alert rules, and
writes notification plans (destinations, channels, workflows), muting rules, Terraform and a
report. Importing this package registers the remediation rules and taxonomy patterns for the
reasons of the conversion step. The command is in `importer.commands.grafana_notify_convert`.
"""

from importer.sources.grafana.notify_convert import remediation_rules  # noqa: F401  (registers)
