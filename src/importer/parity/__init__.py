"""Parity verifier: does a converted query show the same numbers as the original?

The converters are checked against fixtures. This package checks them against data: the original
PromQL (or LogQL count) is evaluated on the source, the converted NRQL on New Relic, over the same
window and step, and the two sets of series are compared. Read-only on both sides, and only
meaningful when the same data reaches both places.
"""
