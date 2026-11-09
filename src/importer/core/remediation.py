"""Manual steps for things that did not migrate (or migrated with a caveat).

Same idea as taxonomy.py: ordered keyword rules over the free-text reasons, first match per
reason. `IRMonitor.remediation` (specific, computed by a source) takes precedence over these
generic steps. A test fails when a reason on an unconverted fixture has no rule, which is the
prompt to add one here. Steps are guidance for a human; none of them is carried out by the tool.
"""

import re
from dataclasses import dataclass

Rule = tuple[re.Pattern[str], str, list[str]]  # (reason pattern, title, manual steps)

_COMPILED: list[Rule] = []


def register_rules(rules: list[Rule]) -> None:
    """Called by each source on import. Rules match case-insensitively; first match wins."""
    _COMPILED.extend(
        (re.compile(p.pattern, re.IGNORECASE), title, steps) for p, title, steps in rules
    )


# Incidental notes that sit beside a real blocker; they never decide which group an asset is in.
SECONDARY_TITLES = frozenset({"Non-SNS alarm actions"})


@dataclass(frozen=True)
class Remedy:
    title: str  # the kind of problem, shared by every asset with it
    steps: tuple[str, ...]  # generic manual steps for that kind of problem

    @property
    def secondary(self) -> bool:
        return self.title in SECONDARY_TITLES


def remedies_for(reasons: list[str]) -> list[Remedy]:
    """The first matching rule per reason, without repeats."""
    import importer.sources  # noqa: F401  (loads every source's rules; avoids a cycle)

    found: list[Remedy] = []
    for reason in reasons:
        for pattern, title, steps in _COMPILED:
            if pattern.search(reason):
                remedy = Remedy(title, tuple(steps))
                if remedy not in found:
                    found.append(remedy)
                break
    return found


def steps_for(reasons: list[str]) -> list[str]:
    """Generic manual steps for a list of reasons, without repeats."""
    steps: list[str] = []
    for remedy in remedies_for(reasons):
        steps += [x for x in remedy.steps if x not in steps]
    return steps


# Limits of the emitters, shared by every source. Patterns avoid the wording of source rules.
register_rules(
    [
        (
            re.compile(r"no evaluation window|evaluation window .* is outside|no thresholds"),
            "No usable window or threshold",
            [
                "Create the condition by hand in New Relic with a threshold and a duration "
                "between 1 minute and 24 hours, using the query in the report as its NRQL.",
            ],
        ),
        (
            re.compile(r"is not a multiple of the"),
            "Duration does not fit the window",
            [
                "New Relic needs the duration to be a whole number of aggregation windows. "
                "Create the condition by hand, rounding the duration up to the next whole "
                "window.",
            ],
        ),
        (
            re.compile(r"signal kind .* is not supported|confidence is"),
            "Cannot be created automatically",
            [
                "The converted query can be read in the report but the importer does not "
                "create this kind of condition. Create it by hand from that query.",
            ],
        ),
        (
            re.compile(r"alarm has no static threshold"),
            "No static threshold",
            [
                "The alarm compares against a band or another metric, not a fixed number. Use "
                "a New Relic baseline condition, or pick a fixed threshold from the history.",
            ],
        ),
        (
            re.compile(r"is not a recognised name"),
            "Unusual metric name",
            [
                "The metric type has characters the importer does not accept in a New Relic "
                "metric name. Look up the metric in Metrics Explorer and write the NRQL by hand.",
            ],
        ),
    ]
)
