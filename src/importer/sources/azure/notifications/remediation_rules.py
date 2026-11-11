"""Manual steps and failure categories for the Azure notification reasons.

Every pattern is anchored to the start of the reason and names the Azure wording, so a rule wins for
its own reason and for no other source's. The taxonomy patterns are registered with
`register_taxonomy`, which tries them before the built-in ones. The longer methods for the
receivers New Relic cannot reach (SMS, voice, app push, Event Hub, ARM role) and for
AddActionGroups are the steps below; `playbooks.py` adds the cookbook entry for each.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.core.taxonomy import Category, register_taxonomy
from importer.sources.azure.notifications import reasons as R
from importer.sources.azure.notifications import receivers as S
from importer.sources.azure.notifications.processing import STEPS

K = Category

# (reason, title, steps, category)
TABLE: list[tuple[str, str, list[str], Category]] = [
    (
        R.R_SECRET,
        "Azure webhook destination needs a secret",
        [
            S.STEP_URL_PART,
            "For Terraform set the sensitive variable named in the report (nr_secret_ plus the "
            "secret name in lower case) with TF_VAR_nr_secret_<name> in the environment, or in a "
            "tfvars file that stays out of git. Azure shows a Logic App callback URL, a function "
            "key or a runbook webhook token once; fetch it again from the Azure resource if you "
            "no longer have it.",
        ],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_COMMON_SCHEMA,
        "Azure webhook receiver and the common alert schema",
        [
            S.STEP_COMMON_SCHEMA,
            "The report lists which common alert schema fields are filled and which are left "
            "out. If the receiver needs more, put a small translator in front of it (an Azure "
            "Function or Logic App) that builds what it expects from the New Relic payload.",
        ],
        K.APPROXIMATION,
    ),
    (R.R_AAD, "Azure webhook with Microsoft Entra authentication", [S.STEP_AAD], K.MANUAL_FOLLOWUP),
    (R.R_NO_URL, "Azure receiver without a usable URL", [S.STEP_NO_URL], K.MANUAL_FOLLOWUP),
    (
        R.R_URL_CREDENTIALS,
        "Azure webhook URL with credentials",
        [S.STEP_CREDENTIALS],
        K.MANUAL_FOLLOWUP,
    ),
    (R.R_HTTP_URL, "Azure webhook URL that is not https", [S.STEP_HTTP], K.APPROXIMATION),
    (R.R_NO_EMAIL, "Azure email receiver without an address", [S.STEP_NO_EMAIL], K.MANUAL_FOLLOWUP),
    (
        R.R_EMAIL_CUT,
        "Azure email receivers beyond the address limit",
        [
            "A New Relic EMAIL destination holds at most 150 addresses. Create a second EMAIL "
            "destination for the rest, or use a distribution list.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_SMS,
        "Azure SMS receiver",
        [
            S.STEP_PHONE.format(what="SMS", how="send the SMS"),
            S.STEP_PHONE_PRIVACY,
            "Method: (1) in PagerDuty add the person to an escalation policy with an SMS "
            "notification rule; (2) in New Relic create a PagerDuty destination and attach it "
            "to the workflow; (3) send a test issue and confirm the SMS arrives before enabling.",
        ],
        K.UNSUPPORTED_FEATURE,
    ),
    (
        R.R_VOICE,
        "Azure voice receiver",
        [
            S.STEP_PHONE.format(what="voice call", how="phone the person"),
            S.STEP_PHONE_PRIVACY,
            "Method: in PagerDuty (or Opsgenie) add a phone call notification rule for the "
            "person, attach that destination to the workflow, and test it with one issue.",
        ],
        K.UNSUPPORTED_FEATURE,
    ),
    (R.R_APP_PUSH, "Azure app push receiver", [S.STEP_PUSH], K.MANUAL_FOLLOWUP),
    (R.R_EVENT_HUB, "Azure Event Hub receiver", [S.STEP_EVENT_HUB], K.UNSUPPORTED_FEATURE),
    (R.R_ARM_ROLE, "Azure ARM role receiver", [S.STEP_ARM_ROLE], K.UNSUPPORTED_FEATURE),
    (R.R_ITSM, "Azure ITSM receiver", [S.STEP_ITSM], K.MANUAL_FOLLOWUP),
    (
        R.R_UNKNOWN_RECEIVER,
        "Azure action group with a receiver type that is not read",
        [S.STEP_UNKNOWN],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_GROUP_DISABLED,
        "Azure action group is disabled",
        [
            "A disabled action group sent nothing. Its workflow is planned disabled; enable it "
            "only if you want notifications from this group again.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_GROUP_NOT_EXPORTED,
        "Azure alert rule points at an action group that is not in the export",
        [
            "Export the action groups of every subscription the alert rules use (an alert rule "
            "can point at an action group in another subscription), then convert again.",
        ],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_TAG_FILTER,
        "Azure workflow filter depends on the alert conversion tag",
        [
            "Open one converted condition in New Relic and confirm it carries the tag "
            "azure.actionGroup, then trigger a test issue and check that the workflow matches "
            "it. If it does not, convert again with --route-by name.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_RULE_ADD,
        "Azure alert processing rule that adds action groups",
        [STEPS[R.R_RULE_ADD]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_LEGACY,
        "Azure action rule from the 2019 preview api",
        [STEPS[R.R_RULE_LEGACY]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_ACTION,
        "Azure alert processing rule with an unknown action",
        [STEPS[R.R_RULE_ACTION]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_FIELD,
        "Azure alert processing rule condition with nothing to match",
        [STEPS[R.R_RULE_FIELD]],
        K.UNSUPPORTED_FEATURE,
    ),
    (
        R.R_RULE_OPERATOR,
        "Azure alert processing rule condition that cannot be a muting condition",
        [STEPS[R.R_RULE_OPERATOR]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_TAG,
        "Azure alert processing rule needs a tag on the converted conditions",
        [STEPS[R.R_RULE_TAG]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_SCOPE,
        "Azure alert processing rule scope that cannot be matched",
        [STEPS[R.R_RULE_SCOPE]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_SCOPE_RULE_SUB,
        "Azure alert processing rule scope matched by the alert rule subscription",
        [
            "Azure scopes a processing rule by the subscription of the alert's target resource; "
            "the muting rule uses the subscription of the alert rule. They differ when an alert "
            "rule watches resources in another subscription. Check the condition list in the "
            "muting rule.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_RULE_NO_MATCH,
        "Azure alert processing rule matches no converted condition",
        [
            "Nothing was created. Either the alert rules it covers were not converted, or no "
            "converted condition matches its scope and conditions. Create the muting rule when "
            "those conditions exist.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_RULE_SCHEDULE,
        "Azure alert processing rule schedule that cannot be a muting window",
        [STEPS[R.R_RULE_SCHEDULE]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_ZONE,
        "Azure alert processing rule time zone not recognised",
        [STEPS[R.R_RULE_ZONE]],
        K.MANUAL_FOLLOWUP,
    ),
    (
        R.R_RULE_SEVERAL,
        "Azure alert processing rule became several muting rules",
        [
            "Each recurrence (and each day of a monthly recurrence, and each block of 500 "
            "condition names) is its own muting rule, because a New Relic muting rule has one "
            "schedule. Enable or delete them together. Check the first daylight saving change "
            "after enabling; whether a weekly window that crosses midnight repeats on the "
            "listed days as Azure does is UNVERIFIED.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_RULE_ALWAYS,
        "Azure alert processing rule without a schedule",
        [
            "The Azure rule suppresses at all times. The muting rule has no schedule and is "
            "planned disabled; that New Relic accepts a muting rule with no schedule is "
            "UNVERIFIED. Enable it only when you want permanent suppression of these "
            "conditions; deleting the rule ends it.",
        ],
        K.APPROXIMATION,
    ),
    (
        R.R_RULE_EXPIRED,
        "Azure alert processing rule schedule has ended",
        [
            "The schedule ended before today, so no muting rule was made. Nothing to do unless "
            "the Azure rule is meant to be reused.",
        ],
        K.APPROXIMATION,
    ),
]

RULES: list[Rule] = [
    (re.compile("^" + re.escape(reason)), title, steps) for reason, title, steps, _ in TABLE
]

register_rules(RULES)
for _reason, _title, _steps, _category in TABLE:
    register_taxonomy(_category, "^" + re.escape(_reason))
