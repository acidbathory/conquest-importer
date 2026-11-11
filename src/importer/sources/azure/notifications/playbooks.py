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
