"""Compound conditions: which monitors are parts of one, and the policy the parts go in.

New Relic needs every part of a compound condition enabled, so each part evaluates and can open
issues of its own. The parts therefore go in a policy of their own that no workflow routes, and
only the compound condition sits in the routed policy (docs/research/nr-capability-live-check.md).
"""

from collections.abc import Iterable

from importer.core.ir import IRMonitor

PARTS_POLICY_SUFFIX = " (compound parts)"


def part_keys(monitors: Iterable[IRMonitor]) -> set[str]:
    """The source keys of the monitors that are a part of a compound monitor in `monitors`."""
    return {key for m in monitors if m.compound is not None for key in m.compound.components}


def parts_policy_name(policy_name: str) -> str:
    """The policy that holds the parts of the compound conditions of `policy_name`."""
    return f"{policy_name}{PARTS_POLICY_SUFFIX}"
