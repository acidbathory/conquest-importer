"""Playbooks for Grafana contact points, notification policies, mute timings and templates."""

from importer.core.playbook import Playbook, register_playbook
from importer.core.playbooks import links as L


def reg(titles: tuple[str, ...] | str, pb: Playbook) -> None:
    register_playbook(titles, pb)


TEST = "Send a test notification from the destination page before you enable the workflow."

# --- contact points ------------------------------------------------------------------------
_CP_MANUAL = "Grafana contact point needs a manual step"
reg(
    f"{_CP_MANUAL}|slack",
    Playbook(
        grafana="A Slack contact point posts alerts to a channel through a webhook URL or bot token.",
        newrelic="A Slack destination (the New Relic Slack app, OAuth sign-in once per "
        "workspace) with a channel on it, used by a workflow.",
        steps=(
            "Alerts > Destinations > Slack: authenticate with Slack (the workspace admin may "
            "need to approve the New Relic app).",
            "Alerts > Workflows: create or edit the workflow, add a Slack channel notification, "
            "pick the destination and the channel.",
            TEST,
        ),
        differs="The Grafana message template is not carried over; edit the New Relic message "
        "template (issue variables) for the wording.",
        effort_minutes=15,
        links=(L.INTEGRATIONS, L.WORKFLOWS, L.TEMPLATES),
    ),
)
reg(
    f"{_CP_MANUAL}|teams",
    Playbook(
        grafana="A Microsoft Teams contact point posts to a channel through a connector URL.",
        newrelic="The New Relic app for Microsoft Teams, added to the channel; a Teams "
        "destination in New Relic.",
        steps=(
            "Install the New Relic app in Teams and add it to the channel.",
            "Alerts > Destinations > Microsoft Teams: enter the security code the app posts "
            "and pick the team and channel.",
            "Add the channel to the workflow. " + TEST,
        ),
        differs="Not a connector URL; the app is installed per channel.",
        effort_minutes=20,
        links=(L.INTEGRATIONS, L.WORKFLOWS),
    ),
)
reg(
    f"{_CP_MANUAL}|jira",
    Playbook(
        grafana="A Jira contact point creates an issue from an alert.",
        newrelic="A Jira destination (Jira Cloud only) with a channel that has a project "
        "and an issue type.",
        steps=(
            "Create an API token in Jira; Alerts > Destinations > Jira: enter site, user and "
            "token. Two-way sync needs an admin token.",
            "In the workflow add a Jira channel, choose project and issue type.",
            TEST,
        ),
        differs="Jira on-premises or Data Center is not supported (documented). The ids of "
        "project and issue type are chosen in the UI.",
        effort_minutes=30,
        links=(L.INTEGRATIONS, L.WORKFLOWS),
    ),
)
reg(
    f"{_CP_MANUAL}|sns",
    Playbook(
        grafana="An SNS contact point publishes to an Amazon SNS topic.",
        newrelic="An AWS EventBridge destination (New Relic is an EventBridge SaaS partner "
        "event source); an EventBridge rule in AWS forwards to SNS.",
        steps=(
            "Alerts > Destinations > AWS EventBridge: region and 12-digit account id.",
            "In AWS: associate the partner event source with an event bus, add a rule "
            "that sends the events to your SNS topic (or Lambda, SQS).",
            "Add the EventBridge channel to the workflow. " + TEST,
        ),
        differs="One extra hop in AWS that you own and pay for.",
        effort_minutes=45,
        links=(L.INTEGRATIONS, L.DESTINATIONS),
    ),
)
reg(
    f"{_CP_MANUAL}|pagerduty",
    Playbook(
        grafana="A PagerDuty contact point sends events with an integration key.",
        newrelic="A PagerDuty destination: account-level (REST API key, recommended, several "
        "services) or service-level integration key.",
        steps=(
            "Alerts > Destinations > PagerDuty: use the account-level integration, or create "
            "a New Relic integration on the PagerDuty service and paste its key.",
            "Add a PagerDuty channel to the workflow. " + TEST,
        ),
        differs="The stored Grafana key cannot be read; use a new key. Severity mapping "
        "follows New Relic issue priority.",
        effort_minutes=15,
        links=(L.INTEGRATIONS, L.WORKFLOWS),
    ),
)
reg(
    _CP_MANUAL,
    Playbook(
        grafana="A contact point that needs a person to sign in or to supply a key "
        "(Pushover, Slack, Teams, Jira, SNS, PagerDuty).",
        newrelic="A destination of the matching New Relic type (Slack, Teams, Jira, "
        "ServiceNow, PagerDuty, EventBridge, email, webhook) used by a workflow channel. "
        "Pushover has none: use a webhook to a relay you control, or another channel.",
        steps=(
            "Alerts > Destinations > pick the type; enter a new key or sign in.",
            "Alerts > Workflows > add the channel to the workflow. " + TEST,
        ),
        differs="Secrets are not exported from Grafana; every key is new.",
        effort_minutes=20,
        links=(L.INTEGRATIONS, L.WORKFLOWS),
    ),
)
_CP_NONE = "Grafana contact point has no New Relic destination"
reg(
    _CP_NONE,
    Playbook(
        grafana="A contact point for a service New Relic does not have a destination for "
        "(Threema, LINE, MQTT, WeChat, WeCom app).",
        newrelic="None. Workarounds: a webhook destination to a relay you control that "
        "speaks the service's protocol (MQTT broker bridge, Threema gateway), or another "
        "channel for the same people (email, Slack, Teams, mobile push).",
        steps=(
            "Ask who receives these alerts and whether they can use email, Slack, Teams or "
            "the New Relic mobile app.",
            "If the service must stay: run a small relay that accepts a webhook and forwards "
            "(a cloud function). Create a WEBHOOK destination to it, with a payload template "
            "carrying the issue fields.",
            "Add the new channel to the workflow. " + TEST,
        ),
        differs="Another channel or a relay you maintain.",
        effort_minutes=60,
        no_equivalent=True,
        links=(L.INTEGRATIONS, L.DESTINATIONS, L.TEMPLATES),
    ),
)
reg(
    "Grafana contact point type not known",
    Playbook(
        grafana="A contact point of a type the importer's table does not list.",
        newrelic="Depends on the type; recreate it by hand as email, webhook or a native "
        "destination.",
        steps=(
            "Open the contact point in Grafana; note type and settings.",
            "Pick the closest destination in New Relic (Alerts > Destinations).",
            "Report the type to the maintainers so the table can learn it.",
        ),
        differs="Not known.",
        effort_minutes=30,
        links=(L.INTEGRATIONS,),
    ),
)
reg(
    "Grafana contact point as a webhook template",
    Playbook(
        grafana="A chat or incident service reached through its own API (Discord, Google "
        "Chat, Telegram, Webex, OnCall, Opsgenie, VictorOps, DingTalk, Sensu, Kafka).",
        newrelic="A WEBHOOK destination with a channel payload (Handlebars) in the shape the "
        "service expects. Opsgenie has a documented webhook template.",
        steps=(
            "Create the WEBHOOK destination; enter the URL as a secure URL when it holds a token.",
            "Edit the channel payload from the generated Terraform; replace REPLACE_* markers.",
            "Send a test message to a sandbox receiver first. " + TEST,
        ),
        differs="Payloads were built from the services' public APIs, not tested against "
        "them (UNVERIFIED); New Relic always uses POST.",
        effort_minutes=30,
        links=(L.INTEGRATIONS, L.TEMPLATES, L.DESTINATIONS),
    ),
)
reg(
    "Grafana vendor webhook payload not tested",
    Playbook(
        grafana="A webhook payload for a vendor service built by the importer.",
        newrelic="The same WEBHOOK destination; the payload needs a real test.",
        steps=(
            "Send a test notification to a sandbox receiver and compare with the service's "
            "documented body.",
            TEST,
        ),
        differs="The payload shape is UNVERIFIED against the service.",
        effort_minutes=20,
        links=(L.TEMPLATES,),
    ),
)
reg(
    "Grafana webhook settings not converted",
    Playbook(
        grafana="A webhook with an HTTP method, credentials, signing, headers or a custom body.",
        newrelic="A WEBHOOK destination with basic or bearer auth and custom headers; a "
        "channel payload. New Relic always POSTs and does not sign requests.",
        steps=(
            "Add the credentials and custom headers on the destination by hand.",
            "Rewrite the body as a channel payload template.",
            "If the receiver needs an HMAC signature, put a small verifier/relay in front.",
            TEST,
        ),
        differs="Always POST; unsigned.",
        effort_minutes=20,
        links=(L.DESTINATIONS, L.TEMPLATES),
    ),
)
reg(
    "Grafana custom notification template",
    Playbook(
        grafana="A Go template builds the title and message.",
        newrelic="A message template on the channel using issue variables "
        "(issueTitle, priorityText, stateText, issuePageUrl, ...).",
        steps=(
            "Open the channel in the workflow; edit the message template.",
            "Translate `{{ .CommonLabels.alertname }}` to the matching issue variable; "
            "loops over alerts have no equal (one issue per notification).",
            TEST,
        ),
        differs="Go template syntax and its functions do not exist; Handlebars variables do.",
        effort_minutes=20,
        links=(L.TEMPLATES, L.WORKFLOWS),
    ),
)
reg(
    "Grafana email contact point without an address",
    Playbook(
        grafana="An email contact point.",
        newrelic="An EMAIL destination.",
        steps=(
            "Put the addresses in the destinations map under the contact point and convert "
            "again, or create the EMAIL destination in Alerts > Destinations.",
        ),
        differs="None once the address is set.",
        effort_minutes=5,
        links=(L.DESTINATIONS,),
    ),
)
reg(
    "Grafana converted destination needs a secret at apply time",
    Playbook(
        grafana="A credential the export cannot hold.",
        newrelic="A sensitive Terraform variable or the destination form in the UI.",
        steps=(
            "Set `TF_VAR_nr_secret_<name>` in the environment or a tfvars file kept out of git.",
            "Or create the destination in the UI and paste the secret there.",
        ),
        differs="Every secret is new.",
        effort_minutes=5,
        links=(L.DESTINATIONS,),
    ),
)
reg(
    (
        "Grafana policy names a contact point that is not in the export",
        "Grafana route without a destination New Relic can create",
        "Grafana destinations map entry does not fit",
    ),
    Playbook(
        grafana="A route sends to a receiver that was not exported or has nothing New Relic "
        "can create.",
        newrelic="Fix the export or the map, or pick another channel for the route.",
        steps=(
            "Export again with the contact points included, or correct the destinations map.",
            "Choose a channel for the route (see the contact point's playbook).",
        ),
        differs="None once resolved.",
        effort_minutes=15,
        links=(L.DESTINATIONS, L.WORKFLOWS),
    ),
)

# --- notification policies -----------------------------------------------------------------
reg(
    (
        "Grafana notification policy matcher undecidable",
        "Grafana notification policy matcher not understood",
        "Grafana notification policy regular expression",
        "Grafana notification policy order",
        "Grafana notification policy rule without receiver",
    ),
    Playbook(
        grafana="The notification policy tree routes alerts by label matchers, in order; the "
        "first matching route wins; a rule can have no receiver.",
        newrelic="One workflow per route with an issue filter (attribute, operator, value); "
        "workflows run independently and all that match fire.",
        steps=(
            "List the routes in order. For each, write the matchers as an issue filter in "
            "Alerts > Workflows (policy name, condition name, `tags.<name>`).",
            "Make routes mutually exclusive: add the negation of earlier matchers to later "
            "filters (`NOT_IN`, `DOES_NOT_CONTAIN`), since there is no first-match stop.",
            "Regular expression matchers: use CONTAINS, STARTS_WITH, ENDS_WITH or IN a list "
            "(up to 500 values).",
            "Easiest: organize New Relic policies by routing target (one policy per team) and "
            "filter workflows on the policy name.",
            "Rule with no receiver: choose one, or accept that it does not notify.",
        ),
        differs="Grafana stops at the first match; New Relic sends from every matching "
        "workflow, so a badly built filter notifies twice.",
        effort_minutes=45,
        links=(L.WORKFLOWS, L.MUTING),
    ),
)
reg(
    (
        "Grafana notification policy grouping or timing",
        "Grafana notification policy tree too large",
        "Grafana notification policy tree cut",
    ),
    Playbook(
        grafana="Group by labels, group wait, group interval and repeat interval shape how "
        "alerts are batched and repeated.",
        newrelic="Issues group incidents by incident preference of the policy (per policy, "
        "per condition, per condition and target); workflows notify on state changes. There "
        "is no repeat interval.",
        steps=(
            "Choose the incident preference on the policy (Grafana grouping by every label is "
            "closest to PER_CONDITION_AND_TARGET).",
            "Repeated reminders: no equal in a workflow; use a mobile or paging tool that "
            "re-notifies (PagerDuty escalations).",
            "A big tree: split the plan by team and convert each part.",
        ),
        differs="No group wait and no repeat interval.",
        effort_minutes=20,
        no_equivalent=True,
        links=(L.WORKFLOWS,),
    ),
)

# --- mute timings --------------------------------------------------------------------------
reg(
    (
        "Grafana mute timing days of the month",
        "Grafana mute timing months or years",
        "Grafana mute timing location",
        "Grafana mute timing time range",
        "Grafana mute timing weekdays",
        "Grafana active time interval",
        "Grafana mute timing with too many schedules",
        "Grafana mute timing with combined fields",
        "Grafana unused mute timing",
        "Grafana policy names a mute timing that is not in the export",
    ),
    Playbook(
        grafana="A mute timing silences notifications on a time pattern (weekdays, time of "
        "day, days of the month, months, a time zone) for the routes that use it.",
        newrelic="A muting rule: a filter on alert event attributes (conditionName, "
        "policyName, tags.<name>) and a schedule with a start, end, time zone and a daily, "
        "weekly (chosen days) or monthly repeat.",
        steps=(
            "Alerts > Muting rules > + Add a rule; filter on the conditions the route covers.",
            "Schedule: start and end time, the time zone, repeat daily or weekly with the "
            "days. Overnight ranges are one start/end that crosses midnight.",
            "Days of the month list or months: a monthly repeat has one day; make one rule per "
            "day or accept the nearest pattern (not documented as a list).",
            "Several patterns in one timing: one muting rule each.",
            "Use the strict muting handling in the workflow so a muted issue is not notified.",
        ),
        differs="A muting rule covers every workflow that does not use the strict handling; "
        "New Relic still records alert events while muted.",
        effort_minutes=20,
        links=(L.MUTING, L.TF_MUTING, L.GRAFANA_MUTE),
    ),
)
