"""Manual steps for the inventory, re-instrumentation and synthetics reasons.

Each rule anchors on the whole reason text, like dashboards/remediation_rules.py, so it matches
its own reason only. tests/test_dynatrace_inventory_rules.py checks that no registered rule of
any source answers one of these reasons first.
"""

import re

from importer.core.remediation import Rule, register_rules
from importer.sources.dynatrace.inventory import reasons as r

_TABLE: list[tuple[str, str, list[str]]] = [
    (
        r.I_TYPE,
        "Entity type not in the technology table",
        [
            "The table in data/entity_types.json has no row for this Dynatrace entity type. "
            "Decide by hand what monitors it in New Relic, then add a row (with its status "
            "DOCS or UNVERIFIED) so the next plan counts it.",
        ],
    ),
    (
        r.I_TECH,
        "Technology not in the technology table",
        [
            "The table in data/technology_agents.json has no row for this technology. Check "
            "the New Relic agent list for it; if there is none, OpenTelemetry is the usual "
            "route. Add a row with its status.",
        ],
    ),
    (
        r.I_NO_TECH,
        "Service lists no technology",
        [
            "Dynatrace did not report a technology for these services, so the plan cannot "
            "name an agent. Look at the process the service runs in, or at the host, to decide.",
        ],
    ),
    (
        r.I_PROCESS,
        "OneAgent process detection is replaced",
        [
            "Process groups come from OneAgent's automatic detection. New Relic does not "
            "detect them; install the agent that fits each application (see the technology "
            "table) and the infrastructure agent on the hosts. Nothing is migrated.",
        ],
    ),
    (
        r.I_PARTIAL,
        "Entity list is partial",
        [
            "The API reported more entities than were read (a page cap or an unreadable "
            "entry). Treat every count as a lower bound, or raise the limit and read again.",
        ],
    ),
    (
        r.I_ROW,
        "Mapping row not confirmed",
        [
            "This target comes from a row marked UNVERIFIED. Check it against the New Relic "
            "documentation for the technology before planning the rollout.",
        ],
    ),
    (
        r.I_SPELLING,
        "Technology spelling not confirmed",
        [
            "The name matched a table row only after normalising its spelling. Check that the "
            "row is the technology you mean before counting it.",
        ],
    ),
    (
        r.I_OS,
        "Operating system support not confirmed",
        [
            "Check that the New Relic infrastructure agent supports each operating system "
            "listed (for example AIX or Solaris). The plan did not compare the two lists.",
        ],
    ),
    (
        r.S_API_SCRIPT,
        "Synthetic monitor needs a scripted API monitor",
        [
            "A simple monitor cannot do this: POST or a body, several requests, status-list "
            "or regex checks, or scripts. Write a scripted API monitor in Node by hand that "
            "makes the same requests and assertions.",
        ],
    ),
    (
        r.S_BROWSER_SCRIPT,
        "Synthetic monitor needs a scripted browser monitor",
        [
            "JavaScript events and cookies need a scripted browser monitor. Write it by hand "
            "from the clickpath, and keep credentials in New Relic secure credentials.",
        ],
    ),
    (
        r.S_PRE_POST,
        "Pre- and post-processing scripts do not port",
        [
            "Dynatrace scripts call Dynatrace's own `api` object. Read what each one does "
            "(set a value, sign a request, parse a response) and write it again in a "
            "scripted API monitor.",
        ],
    ),
    (
        r.S_STEP,
        "Clickpath as a step monitor",
        [
            "A clickpath of navigate, click, keystrokes and select events fits a step "
            "monitor, but the selector format for each step has not been confirmed. Create "
            "the steps in the New Relic editor and check each locator; use a scripted browser "
            "monitor if a step cannot be expressed.",
        ],
    ),
    (
        r.S_NETWORK,
        "Network availability monitor",
        [
            "New Relic has no ICMP, TCP or DNS monitor. A scripted API monitor can make a "
            "TCP or DNS check from Node (UNVERIFIED); an ICMP ping from public locations is "
            "not possible that way.",
        ],
    ),
    (
        r.S_AUTH,
        "Synthetic monitor with authentication or a regex rule",
        [
            "A simple monitor has no authentication, client certificate or regular-expression "
            "check. Write a scripted API monitor in Node that sends the credentials from New "
            "Relic secure credentials and makes the same assertions.",
        ],
    ),
    (
        r.S_LOCATION_ROW,
        "Location chosen from an unconfirmed row",
        [
            "The New Relic location comes from a table row marked UNVERIFIED, or from the "
            "country only. Check that the location exists and is close to the original.",
        ],
    ),
    (
        r.S_NO_SCRIPT,
        "Synthetic monitor without a script",
        ["The monitor has no requests or events to read. Open it in Dynatrace and recreate it."],
    ),
    (
        r.S_KIND,
        "Synthetic monitor of an unknown kind",
        ["The monitor type is not HTTP, BROWSER or NETWORK_AVAILABILITY. Recreate it by hand."],
    ),
    (
        r.S_LOCATION,
        "Synthetic location without a mapping",
        [
            "The location is not in data/synthetic_locations.json. Pick the nearest New Relic "
            "public location and add a row (status UNVERIFIED until checked).",
        ],
    ),
    (
        r.S_PRIVATE,
        "Private synthetic location",
        [
            "Create a New Relic private location and install a minion on a host that can "
            "reach the target. A private location can take up to ten minutes to become "
            "available. The importer does not do this.",
        ],
    ),
    (
        r.S_NO_LOCATION,
        "Synthetic monitor without a location",
        ["Choose at least one New Relic location before creating the monitor."],
    ),
    (
        r.S_FREQUENCY,
        "Synthetic frequency rounded up",
        ["The monitor ran at an interval New Relic does not offer. Pick the period you want."],
    ),
    (
        r.S_ALERT,
        "Availability and timing thresholds",
        [
            "Dynatrace opens an availability problem after a number of failed runs and can "
            "alert on loading time. In New Relic create an alert condition on the monitor's "
            "results (failures in a row, or duration) after the monitor exists. The plan "
            "records the numbers (consecutive runs, loading time) in plan.json.",
        ],
    ),
    (
        r.S_REDIRECT,
        "Redirect setting direction not confirmed",
        [
            "Check by testing one monitor whether a redirect counts as a failure in New "
            "Relic, then set treat_redirect_as_failure to match the Dynatrace setting.",
        ],
    ),
    (
        r.S_USER_AGENT,
        "User agent dropped",
        [
            "A simple monitor has no user agent field. If the target depends on it, use a "
            "scripted API monitor that sets the header.",
        ],
    ),
    (
        r.S_DISABLED,
        "Disabled synthetic monitor",
        ["The monitor is disabled in Dynatrace. Decide whether to create it at all."],
    ),
]

RULES: list[Rule] = [
    (re.compile("^" + re.escape(reason)), title, steps) for reason, title, steps in _TABLE
]

register_rules(RULES)
