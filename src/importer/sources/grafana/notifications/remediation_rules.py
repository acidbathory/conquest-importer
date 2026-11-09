"""Manual steps and failure categories for the Grafana notification reasons.

The remediation engine is first-match over one list shared by every source, so each pattern is
anchored to the start of the reason and names the Grafana wording. The taxonomy patterns are
registered with `register_taxonomy`, which tries them before the built-in ones and demands an
anchor. `tests/test_grafana_notifications_rules.py` checks that each rule wins for its own
reason, that no other source's rule claims these reasons, and that these patterns claim nothing
of the other sources.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.grafana.notifications import reasons as R

RULES: list[Rule] = [
    (
        re.compile("^" + re.escape(R.R_CP_NONE)),
        "Grafana contact point has no New Relic destination",
        [
            "New Relic has no destination for this kind of contact point. Choose another "
            "channel for the same people (email, a webhook to a relay you control, or a chat "
            "tool New Relic supports) and add it to the workflow by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_CP_MANUAL)),
        "Grafana contact point needs a manual step",
        [
            "The stored secret cannot be read from Grafana, and some destinations need a person "
            "to sign in. Create the destination in New Relic with a new key or token, then add "
            "it to the workflow. The plan lists the exact step for this integration.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_CP_WEBHOOK_TEMPLATE)),
        "Grafana contact point as a webhook template",
        [
            "Create a WEBHOOK destination and write the payload the receiving service expects. "
            "Enter the URL as a secure URL when it holds a token. Send a test notification to a "
            "sandbox receiver before relying on it.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_CP_WEBHOOK_SETTINGS)),
        "Grafana webhook settings not converted",
        [
            "The webhook converts, but some of its settings do not: the HTTP method (New Relic "
            "always posts), credentials, signing, TLS material, headers and a custom payload. "
            "Add the credentials and headers to the destination by hand and check that the "
            "receiver accepts the New Relic payload.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_CP_UNKNOWN)),
        "Grafana contact point type not known",
        [
            "This integration type is not one the importer knows. Recreate the notification "
            "in New Relic by hand, or update the importer's schema table if Grafana added it.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_CUSTOM_TEMPLATE)),
        "Grafana custom notification template",
        [
            "Custom Go templates are not converted. New Relic sends its default message. "
            "Rewrite the wording as a Handlebars template on the channel; use `issueTitle`, "
            "`stateText`, `priorityText` and `issuePageUrl` for the common fields.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_UNDECIDABLE)),
        "Grafana notification policy matcher undecidable",
        [
            "The policy matches on a label whose value the importer cannot know for this rule "
            "(the query has no known label set, or the rule label is a template). Decide by "
            "hand which receiver the rule goes to and create the workflow for it.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_REGEX)),
        "Grafana notification policy regular expression",
        [
            "A workflow filter has no regular expressions. The pattern is too complex to "
            "evaluate safely or to write as a list of values, a prefix or a suffix. Replace it "
            "with an alternation of fixed values, or create the workflow filter by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_ORDER)),
        "Grafana notification policy order",
        [
            "Grafana stops at the first matching route; New Relic workflows run independently. "
            "This order cannot be written as filters. Make the routes mutually exclusive in "
            "Grafana first, or write one workflow per route with explicit filters.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_LIMIT)),
        "Grafana notification policy too large",
        [
            "The tree or the number of routes a rule reaches is larger than the planner works "
            "through. Simplify the policy tree, or plan the routes for this rule by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_NO_RECEIVER)),
        "Grafana notification policy rule without receiver",
        [
            "The policy tree sends this rule to no contact point (the root has no receiver). "
            "Choose a receiver and create the workflow by hand; until then the condition "
            "notifies nobody.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_TIMING)),
        "Grafana notification policy grouping or timing",
        [
            "New Relic has no group wait, group interval or repeat interval for workflows, and "
            "only some group_by settings map to an incident preference. Expect one "
            "notification per issue event and no repeats; adjust the policy's incident "
            "preference by hand if the grouping matters.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_BAD_MATCHER)),
        "Grafana notification policy matcher not understood",
        [
            "A matcher in the policy tree could not be read. Open the route in Grafana, fix "
            "the matcher, and extract again; until then the route is not planned.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_POLICY_INCOMPLETE)),
        "Grafana notification policy tree cut",
        [
            "The policy tree is deeper or larger than the importer reads, so no routes were "
            "planned from it. Flatten the tree in Grafana, or plan the routes by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_DAYS)),
        "Grafana mute timing days of the month",
        [
            "A muting rule repeats on one day of the month; negative days, ranges that cross "
            "day 28, and days 29 to 31 cannot be written. Create one muting rule per day by "
            "hand, or accept that the mute is not converted.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_MONTHS)),
        "Grafana mute timing months or years",
        [
            "A muting rule has no month or year field and no yearly repeat. Bound the rule "
            "with a start and end date for each span, or create it by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_LOCATION)),
        "Grafana mute timing location",
        [
            "The time zone is not a name the importer accepts (`Local` means the server's "
            "zone). Read the zone Grafana runs in and set it on the muting rule by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_TIMES)),
        "Grafana mute timing time range",
        [
            "A time range is not HH:MM to HH:MM with the start before the end. Fix it in "
            "Grafana (split a window that crosses midnight into two) and extract again.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_WEEKDAYS)),
        "Grafana mute timing weekdays",
        [
            "A weekday is not a day name or a number from 0 (Sunday) to 6. Fix it in Grafana "
            "and extract again.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_ACTIVE)),
        "Grafana active time interval",
        [
            "The route notifies only inside the interval. New Relic has no such setting; the "
            "equivalent is a muting rule for the time outside the interval. Create it by hand "
            "and set the workflow to skip muted issues.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_MUTE_COMBINED)),
        "Grafana mute timing with combined fields",
        [
            "Days of the month together with weekdays cannot be one muting rule. Split the "
            "timing into one interval per case in Grafana, or create the rules by hand.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_DATASOURCE_PATH)),
        "Grafana data source without a New Relic data path",
        [
            "Only Prometheus, Loki and Tempo data can be sent to New Relic. A rule over any "
            "other data source (or over Grafana Cloud's own usage metrics) has no New Relic "
            "source; keep that alert in Grafana or find the signal elsewhere.",
        ],
    ),
    (
        re.compile("^" + re.escape(R.R_RULE_GROUP_UNREADABLE)),
        "Grafana rule file group not readable",
        [
            "A group in the rule file has no name or is not an object. Fix the file; rule "
            "files must be JSON (convert a rules.yml with `yq -o=json`).",
        ],
    ),
]

# (category, anchored pattern): registered before the built-in rules
TAXONOMY: list[tuple[Category, str]] = [
    (Category.UNSUPPORTED_FEATURE, r"^grafana contact point (?:has no|type is not)"),
    (Category.MANUAL_FOLLOWUP, r"^grafana contact point needs"),
    (Category.APPROXIMATION, r"^grafana webhook contact point settings"),
    (Category.MANUAL_FOLLOWUP, r"^grafana custom notification template"),
    (
        Category.UNSUPPORTED_QUERY,
        r"^grafana notification policy (?:matcher|regular expression)",
    ),
    (Category.MANUAL_FOLLOWUP, r"^grafana notification policy rule has no receiver"),
    (Category.APPROXIMATION, r"^grafana notification policy grouping or timing"),
    (
        Category.UNSUPPORTED_FEATURE,
        r"^grafana notification policy (?:order|tree)",
    ),
    (Category.UNSUPPORTED_FEATURE, r"^grafana (?:mute timing|active time interval)"),
    (Category.UNSUPPORTED_FEATURE, r"^grafana data source has no new relic data path"),
    (Category.UNSUPPORTED_FEATURE, r"^grafana rule file group"),
]

register_rules(RULES)
for _category, _pattern in TAXONOMY:
    register_taxonomy(_category, _pattern)
