"""Manual steps and failure categories for the reasons of the conversion step.

Same rules as the read side (`notifications/remediation_rules.py`): every pattern is anchored to
the start of the reason, a rule wins for its own reason and for no other, and the taxonomy
patterns are registered with `register_taxonomy`.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.grafana.notify_convert import reasons as C

RULES: list[Rule] = [
    (
        re.compile("^" + re.escape(C.R_SECRET)),
        "Grafana contact point needs a secret at apply time",
        [
            "The converted destination refers to a secret by name; no value is stored. For "
            "Terraform, set the sensitive variable named in the report (nr_secret_<name in lower "
            "case>) with TF_VAR_nr_secret_<name> in the environment, or in a tfvars file that "
            "stays out of git. The URL secret is the part of the webhook URL after the fixed "
            "start that the destination shows.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_NO_ADDRESS)),
        "Grafana email contact point without an address",
        [
            "The export holds no address for this contact point (it was created by an older "
            "tool or the address field was empty). Put the addresses in the destinations map "
            "under the contact point and convert again, or create the EMAIL destination by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_UNKNOWN_RECEIVER)),
        "Grafana policy names a contact point that is not in the export",
        [
            "A notification policy sends alerts to a contact point name that the export does not "
            "have, so Grafana would drop them. Fix the policy in Grafana, or create the "
            "destination by hand and attach it to the workflow.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_UNKNOWN_TIMING)),
        "Grafana policy names a mute timing that is not in the export",
        [
            "The policy refers to a mute timing that was not exported. The workflow was planned "
            "without muting. Export the mute timings again, or create the muting rule by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_NO_DESTINATION)),
        "Grafana route without a destination New Relic can create",
        [
            "Every contact point of this route needs a person (Slack sign-in, Teams, Jira, "
            "SNS through EventBridge, an email with no address), has no New Relic equivalent, "
            "or was skipped in the destinations map. No workflow was planned. Do the manual "
            "steps listed for the contact point, then create the workflow by hand or add a "
            "destinations map entry and convert again.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_MAP_MISMATCH)),
        "Grafana destinations map entry does not fit",
        [
            "A map entry names a target the contact point type cannot use (an email for a "
            "Slack contact point, a webhook URL name for an email, and so on). The entry was "
            "ignored. Correct the entry.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_VENDOR_UNVERIFIED)),
        "Grafana vendor webhook payload not tested",
        [
            "The payload was built from the service's public API and from what Grafana sends, "
            "and was never sent to the service. Send a test notification to a sandbox receiver "
            "(the destination test in the New Relic UI) and compare the result before enabling "
            "the workflow. The report lists what is uncertain for each service.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_MUTE_SCOPE)),
        "Grafana mute timing as a muting rule",
        [
            "A New Relic muting rule matches conditions, not routes: it mutes the converted "
            "conditions of the route in every workflow that honours muting. Only the workflows "
            "of that route were set to do so. Check that no other workflow with the strict "
            "handling carries the same conditions, and check the first change of daylight "
            "saving time after enabling the rule.",
        ],
    ),
    (
        re.compile("^" + re.escape(C.R_ORPHAN_MUTE)),
        "Grafana mute timing no route uses",
        [
            "No route that carries a converted rule uses this mute timing, so no muting rule "
            "was made from it. If it is meant for rules that were not converted, create the "
            "muting rule when those conditions exist.",
        ],
    ),
]

# (category, anchored pattern): registered before the built-in rules
TAXONOMY: list[tuple[Category, str]] = [
    (Category.MANUAL_FOLLOWUP, r"^grafana notification policy (?:receiver|names a mute timing)"),
    (Category.MANUAL_FOLLOWUP, r"^grafana notification route has no destination"),
    (Category.MANUAL_FOLLOWUP, r"^grafana destinations map entry"),
    (Category.APPROXIMATION, r"^grafana vendor webhook payload"),
    (Category.APPROXIMATION, r"^grafana muting rule from a mute timing"),
    (Category.MANUAL_FOLLOWUP, r"^grafana unused mute timing"),
]

register_rules(RULES)
for _category, _pattern in TAXONOMY:
    register_taxonomy(_category, _pattern)
