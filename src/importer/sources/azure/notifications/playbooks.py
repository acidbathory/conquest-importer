"""Cookbook entries for the Azure receivers New Relic cannot reach, and for AddActionGroups.

Each is keyed by the title of its remediation rule. All text is fixed and ASCII. The links point to
New Relic pages already used by other playbooks; nothing here was run against a live system.
"""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L

PHONE_DIFFERS = (
    "The message text, the sender number and the retry behaviour belong to the replacement "
    "service, not to Azure. Phone numbers are not copied by the importer."
)

register_playbook(
    ("Azure SMS receiver", "Azure voice receiver"),
    Playbook(
        source="An Azure action group sends an SMS or a voice call to a phone number when an "
        "alert fires.",
        newrelic="None. Use a PagerDuty destination (PagerDuty sends the SMS or call), an "
        "Opsgenie webhook, or a WEBHOOK destination to an SMS gateway.",
        steps=(
            "List the SMS and voice receivers of the action group in Azure; the report gives "
            "each one a logical name and its position.",
            "Create each person in PagerDuty (or the gateway) with the phone number, in that "
            "tool only.",
            "In New Relic create the PAGERDUTY_SERVICE_INTEGRATION destination (or a WEBHOOK "
            "destination) and add it to the workflow of the action group.",
            "Send a test issue and confirm the message arrives before enabling the workflow.",
        ),
        differs=PHONE_DIFFERS,
        effort_minutes=30,
        links=(L.DESTINATIONS, L.WORKFLOWS),
        no_equivalent=True,
    ),
)
register_playbook(
    "Azure app push receiver",
    Playbook(
        source="An Azure action group pushes a notification to the Azure mobile app of an account.",
        newrelic="MOBILE_PUSH, one destination per person.",
        steps=(
            "Each person installs the New Relic mobile app and signs in.",
            "Create a MOBILE_PUSH destination for that user and add it to the workflow.",
        ),
        differs="New Relic mobile push is only for the user who owns it; the Azure account is "
        "not copied.",
        effort_minutes=10,
        links=(L.DESTINATIONS,),
    ),
)
register_playbook(
    "Azure Event Hub receiver",
    Playbook(
        source="An Azure action group writes the alert to an Event Hub.",
        newrelic="None. A WEBHOOK destination to a forwarder you own.",
        steps=(
            "Write a small forwarder (an Azure Function or Logic App with an HTTP trigger) "
            "that writes the body it receives to the event hub.",
            "Create a WEBHOOK destination to the forwarder URL, with the URL secret as a "
            "sensitive variable, and use the payload template from the report.",
            "Add it to the workflow and test with one issue.",
        ),
        differs="The event is a New Relic issue, not an Azure alert; consumers of the hub must "
        "read the new shape.",
        effort_minutes=60,
        links=(L.DESTINATIONS, L.TEMPLATES),
        no_equivalent=True,
    ),
)
register_playbook(
    "Azure ARM role receiver",
    Playbook(
        source="An Azure action group emails everyone who holds an RBAC role.",
        newrelic="None. An EMAIL destination with the current holders.",
        steps=(
            "List the holders of the role in the portal (Access control (IAM)).",
            "Create an EMAIL destination with their addresses and add it to the workflow.",
            "Repeat when the role membership changes, or use a distribution list.",
        ),
        differs="New Relic does not follow later changes in the role.",
        effort_minutes=15,
        links=(L.DESTINATIONS,),
        no_equivalent=True,
    ),
)
register_playbook(
    "Azure alert processing rule that adds action groups",
    Playbook(
        source="An Azure alert processing rule adds action groups to every alert that matches "
        "its scope and conditions.",
        newrelic="A workflow whose issue filter selects the same alerts and whose destinations "
        "are those of the action groups.",
        steps=(
            "Work out which converted conditions the rule covers (scope, severity, alert rule "
            "name).",
            "Create a workflow whose issue filter matches those conditions by name or tag.",
            "Add the destinations of each action group the rule adds.",
        ),
        differs="A muting rule is not involved: it can only withhold notifications.",
        effort_minutes=20,
        links=(L.WORKFLOWS, L.MUTING),
    ),
)


# --- the remaining titles, as a table: (source, new relic, steps, differs, minutes, no equivalent)
_Row = tuple[str, str, tuple[str, ...], str, int, bool]
_MORE: dict[str, _Row] = {
    "Azure webhook destination needs a secret": (
        "A webhook receiver whose URL holds a token, key or signature.",
        "A WEBHOOK destination with a secure URL: the host is stored, the rest is a secret.",
        (
            "Fetch the callback URL again from the Azure resource (Logic App trigger, function "
            "key, runbook webhook).",
            "Set the sensitive Terraform variable named in the report, for example with "
            "TF_VAR_nr_secret_<name> in the environment.",
            "Apply the Terraform file and send a test notification.",
        ),
        "The URL is never stored in the plan or the report.",
        10,
        False,
    ),
    "Azure webhook receiver and the common alert schema": (
        "A webhook that receives the Azure common alert schema JSON.",
        "A WEBHOOK destination whose payload template is laid out like that schema.",
        (
            "Read the field table in the report: which fields are filled and which are left out.",
            "Send a test notification to a sandbox receiver and compare with what it parses.",
            "If it needs more, put a small translator (Function or Logic App) in front of it.",
        ),
        "Fields that describe the Azure alert (signal type, target ids, context) have no value.",
        30,
        False,
    ),
    "Azure webhook with Microsoft Entra authentication": (
        "A webhook called with a Microsoft Entra access token.",
        "A WEBHOOK destination with OAuth2, which can only be set in the New Relic UI.",
        (
            "Create the destination in the New Relic UI and choose OAuth2.",
            "Or move the receiver to a URL with its own secret and use the secure URL.",
        ),
        "A script cannot create the OAuth2 setup.",
        20,
        False,
    ),
    "Azure receiver without a usable URL": (
        "A webhook-style receiver whose URL is masked or missing in the export.",
        "A WEBHOOK destination created by hand.",
        (
            "Open the receiver in the Azure portal and copy its URL.",
            "Create the destination with the URL as a secure URL and add it to the workflow.",
        ),
        "Nothing was converted for this receiver.",
        10,
        False,
    ),
    "Azure webhook URL with credentials": (
        "A webhook URL with a user name and password inside it.",
        "A WEBHOOK destination with basic auth.",
        (
            "Create the destination in New Relic and enter the user and password as basic auth.",
            "Use the URL without the credentials.",
        ),
        "The credentials are entered once, in New Relic only.",
        10,
        False,
    ),
    "Azure webhook URL that is not https": (
        "A webhook receiver with an http URL.",
        "A WEBHOOK destination; whether New Relic accepts http is UNVERIFIED.",
        (
            "Send a test notification.",
            "If it is refused, put an https endpoint in front of the receiver.",
        ),
        "Unencrypted delivery is not recommended.",
        10,
        False,
    ),
    "Azure email receiver without an address": (
        "An email receiver with an empty or invalid address.",
        "An EMAIL destination.",
        ("Find the intended address in Azure.", "Add it to the EMAIL destination by hand."),
        "The receiver was not converted.",
        5,
        False,
    ),
    "Azure email receivers beyond the address limit": (
        "An action group with more than 150 email receivers.",
        "A second EMAIL destination, or a distribution list.",
        ("Create another EMAIL destination for the rest.", "Add it to the workflow."),
        "A destination holds at most 150 addresses.",
        10,
        False,
    ),
    "Azure ITSM receiver": (
        "A receiver that creates a ticket through an IT Service Management Connector.",
        "SERVICE_NOW or SERVICE_NOW_APP for ServiceNow; a WEBHOOK for other products.",
        (
            "Find the connector's product in the Log Analytics workspace.",
            "For ServiceNow install the New Relic application and create the destination.",
            "Add it to the workflow and test a ticket.",
        ),
        "Ticket fields and templates are configured again in New Relic.",
        60,
        False,
    ),
    "Azure action group with a receiver type that is not read": (
        "A receiver type this importer does not know.",
        "Depends on the type.",
        ("List the unknown receivers in the Azure portal.", "Recreate each by hand."),
        "Not converted.",
        15,
        False,
    ),
    "Azure action group is disabled": (
        "A disabled action group sends nothing.",
        "A workflow that stays disabled.",
        ("Keep the workflow disabled.", "Enable it only if notifications should return."),
        "Nothing differs while it stays disabled.",
        5,
        False,
    ),
    "Azure alert rule points at an action group that is not in the export": (
        "An alert rule that names an action group in another subscription or resource group.",
        "A workflow made after the group is exported.",
        ("Export that action group.", "Convert again."),
        "No workflow exists for it yet.",
        10,
        False,
    ),
    "Azure workflow filter depends on the alert conversion tag": (
        "Routing of alerts to an action group.",
        "A workflow whose issue filter matches the tag azure.actionGroup.",
        (
            "Open one converted condition and confirm the tag.",
            "Trigger a test issue and check the workflow matches.",
            "If not, convert again with --route-by name.",
        ),
        "That the tag reaches the issue is UNVERIFIED.",
        15,
        False,
    ),
    "Azure action rule from the 2019 preview api": (
        "A suppression or action rule from the retired 2019 preview API.",
        "A muting rule or workflow made by hand.",
        ("Recreate it as an alert processing rule in Azure.", "Export and convert again."),
        "Not converted.",
        15,
        False,
    ),
    "Azure alert processing rule with an unknown action": (
        "An alert processing rule with an action type this importer does not know.",
        "A muting rule or workflow made by hand.",
        ("Read the action in the portal.", "Create the matching object by hand."),
        "Not converted.",
        15,
        False,
    ),
    "Azure alert processing rule condition with nothing to match": (
        "A condition on monitor condition, signal type, description or alert context.",
        "None. Narrow the muting rule by hand or accept the wider rule.",
        (
            "Decide whether the wider muting rule is acceptable.",
            "Otherwise list the exact condition names by hand.",
        ),
        "New Relic cannot match on these fields.",
        15,
        True,
    ),
    "Azure alert processing rule condition that cannot be a muting condition": (
        "A condition with an operator or values New Relic cannot express.",
        "A muting rule made by hand.",
        ("Write the condition names that apply.", "Create the muting rule."),
        "Not converted.",
        15,
        False,
    ),
    "Azure alert processing rule needs a tag on the converted conditions": (
        "A scope or condition on severity, service, resource type, group or resource.",
        "A muting rule on condition names, built once the conversion writes the tags.",
        (
            "Have the alert conversion write the tags in docs/dev/azure-notification-link.md.",
            "Convert the notifications again.",
        ),
        "Without the tags the rule could mute the wrong conditions.",
        15,
        False,
    ),
    "Azure alert processing rule scope that cannot be matched": (
        "A scope that is not a subscription, resource group or resource id.",
        "A muting rule made by hand.",
        ("Work out the conditions in the scope.", "Create the muting rule."),
        "Not converted.",
        15,
        False,
    ),
    "Azure alert processing rule scope matched by the alert rule subscription": (
        "A subscription scope, which Azure applies to the alert's target resource.",
        "A muting rule on the conditions whose alert rule is in that subscription.",
        ("Check the condition list in the muting rule.",),
        "Alert rules that watch another subscription are not covered correctly.",
        10,
        False,
    ),
    "Azure alert processing rule matches no converted condition": (
        "A rule whose scope and conditions select no converted alert.",
        "Nothing is created.",
        ("Convert the alert rules it covers, then convert again.",),
        "No muting rule exists.",
        5,
        False,
    ),
    "Azure alert processing rule schedule that cannot be a muting window": (
        "A schedule with a missing time, unknown recurrence or bad date.",
        "A muting rule with a start, end and optional repeat.",
        ("Write the window by hand.", "Create the muting rule disabled and review it."),
        "Not converted.",
        15,
        False,
    ),
    "Azure alert processing rule time zone not recognised": (
        "A schedule in a time zone name this importer does not map.",
        "A muting rule in an IANA time zone.",
        ("Find the IANA name, for example Europe/Berlin.", "Create the muting rule."),
        "Not converted.",
        10,
        False,
    ),
    "Azure alert processing rule became several muting rules": (
        "One rule with several recurrences, days of the month or many conditions.",
        "One muting rule for each, because a muting rule has one schedule.",
        ("Enable or delete the rules together.", "Check the first daylight saving change."),
        "Several objects for one rule; weekly windows past midnight are UNVERIFIED.",
        10,
        False,
    ),
    "Azure alert processing rule without a schedule": (
        "A suppression rule that is always active.",
        "A muting rule with no schedule; that New Relic accepts it is UNVERIFIED.",
        ("Enable only for permanent suppression.", "Delete the rule to end it."),
        "Planned disabled.",
        5,
        False,
    ),
    "Azure alert processing rule schedule has ended": (
        "A schedule whose end date has passed.",
        "Nothing is created.",
        ("Nothing to do unless the rule is reused.",),
        "No muting rule exists.",
        5,
        False,
    ),
}
for _title, (_src, _nr, _steps, _differs, _minutes, _none) in _MORE.items():
    register_playbook(
        _title,
        Playbook(
            source=_src,
            newrelic=_nr,
            steps=_steps,
            differs=_differs,
            effort_minutes=_minutes,
            links=(L.WORKFLOWS, L.DESTINATIONS, L.MUTING),
            no_equivalent=_none,
        ),
    )
