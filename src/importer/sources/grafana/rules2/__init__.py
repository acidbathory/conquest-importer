"""Deeper alert-rule conversion for Grafana.

`fields` holds what the translator imports (rule fields beyond the query). `promql` registers
extra PromQL forms with the translator and is imported by the source package after the
translator, so it is not imported here.
"""
