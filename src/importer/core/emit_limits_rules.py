"""Remediation rules for the reasons the New Relic condition emitters give when a setting is out
of bounds (see emit/common.py: check_limits). Imported by emit/common.py, so the rules exist
whenever an emitter does. The wording of the reasons is fixed there; these patterns anchor on it.
"""

import re

from importer.core.remediation import Rule, register_rules

RULES: list[Rule] = [
    (
        re.compile(r"more than two thresholds|two thresholds have the same severity"),
        "Too many thresholds",
        [
            "A New Relic NRQL condition has at most two terms: one critical and one warning. "
            "Keep the two that matter most as one condition.",
            "Create a second condition by hand, with the same query, for each threshold that is "
            "left over.",
        ],
    ),
    (
        re.compile(
            r"loss-of-signal timeout \d+s is outside|aggregation (timer|delay) \d+s is outside|"
            r"violation time limit \d+s is outside|title template is \d+ characters|"
            r"runbook URL is not|slide-by \d+s must divide|fill option STATIC needs|"
            r"a fill value needs|ignore on expected termination needs|"
            r"null-safe count scope (names|covers)|aggregation timer is only used|"
            r"a fill value must be|muting operator \w+ takes no values"
        ),
        "Condition setting outside New Relic's limits",
        [
            "The condition was converted but one of its settings is outside what New Relic "
            "accepts, so it was not emitted. The reason names the setting and the allowed range.",
            "Create the condition by hand with the setting inside the range (loss-of-signal "
            "timeout 30 seconds to 48 hours, aggregation timer 5 seconds to 20 minutes, "
            "aggregation delay up to 20 minutes, violation time limit 5 minutes to 30 days, "
            "title template up to 256 characters).",
        ],
    ),
]

register_rules(RULES)
