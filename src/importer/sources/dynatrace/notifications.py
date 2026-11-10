"""Davis problem notification workflows to New Relic notification plans.

A Davis workflow is a notification route: when a problem opens, send messages. The plan holds
what New Relic needs for the same route. Email recipients become email destinations (the only
part that is complete), the other services become manual destinations (a secret or an OAuth
sign-in is needed), and the trigger's tag filter becomes a workflow issue filter where it maps.
What does not map is a note. Every plan is marked as needing review.

Recipient addresses go into the plan file and the Terraform only. Notes, steps and the report
name counts and fixed vocabulary, never an address or other text from the export.

The New Relic side is not confirmed against a live account (UNVERIFIED): the issue attribute
`accumulations.tag.<key>` is documented in New Relic's workflow examples, but a live test must
show that it matches (docs/research/newrelic-notifications-and-nrql-reference.md, section 1.2).
"""

import hashlib
import re
from typing import Any

from importer.core.notification_ir import (
    EMAIL_ADDRESS,
    ChannelKind,
    DestinationType,
    IRChannel,
    ManualDestination,
    NotificationPlan,
    PlannedPredicate,
    PlannedWorkflow,
    plan_destinations,
    spec_for,
    tag_predicate,
)
from importer.core.report import md_cell
from importer.core.report_brand import header_lines
from importer.core.text import printable
from importer.sources.dynatrace.models import DtTask, DtWorkflow
from importer.sources.dynatrace.translate import DQL_ACTION, action_label, davis_config

MAX_RECIPIENTS = 20
MAX_TAGS = 10
MAX_TAG_VALUES = 20
MAX_NAME = 120
TAG_KEY = re.compile(r"[A-Za-z0-9_.:-]{1,64}")
TAG_VALUE_LIMIT = 128
MAX_MANUAL = 50
MAX_PLANS = 2000
NAME_LIMIT = 80  # title characters kept in a New Relic name
# Dynatrace templating: it would become a literal filter that never matches
TEMPLATE = re.compile(r"\{\{|\{%|\$\{")
CATEGORIES = ("availability", "error", "slowdown", "resource", "custom")
# trigger settings that are not mapped; "always" is the default for the maintenance setting
UNMAPPED_SETTINGS = (
    "severityThreshold",
    "problemOpenDuration",
    "triggerOn",
    "maintenanceWindowTriggerBehavior",
    "triggerOnUpdateFields",
)
SETTING_DEFAULTS: tuple[object, ...] = (None, "", "always", [], {})

AGAIN_STEP = "Enter the credentials again in New Relic; Dynatrace does not export them."
# New Relic documents these services as webhook templates, not as destination types.
VENDOR_WEBHOOK_STEPS = [
    "New Relic reaches this service through a webhook destination. A vendor payload template is "
    "required: write the payload and headers the service's API expects.",
    "Create the webhook destination with the service's API URL and credentials.",
]
# label -> (closest New Relic destination type, steps). A type of "" means there is none.
SERVICES: dict[str, tuple[str, list[str]]] = {
    "slack": (
        "SLACK",
        [
            "Create a Slack destination in New Relic and sign in with Slack (OAuth); it cannot "
            "be scripted.",
            "Create a channel on it for the Slack channel the workflow posted to.",
        ],
    ),
    "pagerduty": (
        "PAGERDUTY_SERVICE_INTEGRATION",
        ["Create a PagerDuty destination with the service integration key. " + AGAIN_STEP],
    ),
    "jira": (
        "JIRA",
        ["Create a Jira destination with the site URL and an API token. " + AGAIN_STEP],
    ),
    "ms teams": (
        DestinationType.MICROSOFT_TEAMS.value,
        [
            *spec_for(DestinationType.MICROSOFT_TEAMS).human_steps,
            "Dynatrace does not export the Teams connection, so none of it carries over.",
        ],
    ),
    "servicenow": (
        DestinationType.SERVICE_NOW_APP.value,
        [
            *spec_for(DestinationType.SERVICE_NOW_APP).human_steps,
            "Create a ServiceNow application destination with the instance URL and the "
            "credentials. " + AGAIN_STEP,
            "The older SERVICE_NOW destination still exists but is in maintenance mode.",
        ],
    ),
    "http request": (
        "WEBHOOK",
        [
            "Create a webhook destination with the request URL, headers and any token. "
            + AGAIN_STEP,
            "Write the payload as a New Relic template; Dynatrace placeholders do not carry over.",
        ],
    ),
    **{
        service: ("WEBHOOK", [*VENDOR_WEBHOOK_STEPS, AGAIN_STEP])
        for service in ("opsgenie", "victorops", "xmatters")
    },
}
NO_EQUIVALENT = [
    "This action has no matching New Relic destination type. Decide how the message should "
    "reach people (a webhook to the service's API is the usual route) and build it by hand.",
    "The action was not converted: manual.",
]


def _recipients(task: DtTask, notes: list[str]) -> list[str]:
    """Valid addresses from to, cc and bcc, once each. Anything unread leaves a note."""
    found: list[str] = []
    bad = 0
    unread: set[str] = set()
    for field in ("to", "cc", "bcc"):
        raw = task.input.get(field)
        if raw is None or raw == []:
            continue
        if not isinstance(raw, list):
            unread.add("template" if isinstance(raw, str) and TEMPLATE.search(raw) else "list")
            continue
        for item in raw:
            if isinstance(item, str) and TEMPLATE.search(item):
                unread.add("template")
            elif not isinstance(item, str) or not EMAIL_ADDRESS.match(item):
                bad += 1
            elif item not in found:
                found.append(item)
    if "template" in unread:
        notes.append("recipients are a template and were not read")
    if "list" in unread:
        notes.append("recipients are not a list and were not read")
    if bad:
        notes.append(f"{bad} recipient entries are not valid email addresses and were left out")
    if any(task.input.get(f) for f in ("cc", "bcc")):
        notes.append("cc and bcc recipients become ordinary recipients in New Relic")
    if not (found or unread or bad):
        notes.append("an email task has no readable recipients")
    return found


def _has_template(raw: object) -> bool:
    values = raw if isinstance(raw, list) else [raw]
    return any(isinstance(v, str) and TEMPLATE.search(v) for v in values)


def _tag_values(raw: object) -> list[str] | None:
    values = raw if isinstance(raw, list) else [raw]
    texts = [v for v in values if isinstance(v, str) and v and v.isprintable()]
    ok = 0 < len(texts) == len(values) <= MAX_TAG_VALUES and all(
        len(v) <= TAG_VALUE_LIMIT for v in texts
    )
    return texts if ok else None


def _tag_predicates(value: dict[str, Any], notes: list[str]) -> list[PlannedPredicate]:
    """Entity tags as issue-filter predicates; a note says what could not be mapped."""
    tags = value.get("entityTags")
    if not tags:
        return []
    unreadable = "entity tags are not in a form the importer reads (tag name and values)"
    if not isinstance(tags, dict) or len(tags) > MAX_TAGS:
        notes.append(unreadable)
        return []
    predicates: list[PlannedPredicate] = []
    for key, raw in tags.items():
        if _has_template(raw):
            notes.append("an entity tag value is a template and was not mapped")
            return []
        values = _tag_values(raw)
        if not isinstance(key, str) or not TAG_KEY.fullmatch(key) or values is None:
            notes.append(unreadable)
            return []
        predicates.append(tag_predicate(key, values))
    match = value.get("entityTagsMatch")
    if len(predicates) > 1 and match != "all":
        mode = "any" if match == "any" else "not stated"
        notes.append(
            f"the tag match mode is {mode}, but a New Relic issue filter needs all tags to "
            "match; the tags were not mapped"
        )
        return []
    notes.append(
        "the issue filter attribute accumulations.tag.NAME is documented but unverified for "
        "this account; check it"
    )
    return predicates


def _trigger_notes(value: dict[str, Any], active: bool) -> list[str]:
    notes: list[str] = []
    categories = value.get("categories")
    if categories is not None and not isinstance(categories, dict):
        notes.append("Davis categories are not in a form the importer reads")
    if isinstance(categories, dict):
        chosen = [c for c in CATEGORIES if categories.get(c) is True]
        if chosen:
            notes.append(
                f"Davis categories ({', '.join(chosen)}) have no New Relic issue attribute, so "
                "the workflow does not filter on them; choose a priority or condition filter"
            )
    if value.get("customFilter"):
        notes.append("a custom filter (DQL) is set and was not mapped")
    if value.get("onProblemClose"):
        notes.append(
            "the Dynatrace workflow also ran when a problem closed; set the New Relic channel "
            "to notify when an issue is closed"
        )
    notes += [
        f"{name} is set and was not mapped"
        for name in UNMAPPED_SETTINGS
        if value.get(name) not in SETTING_DEFAULTS
    ]
    if not active:
        notes.append("the Dynatrace trigger was inactive, so the workflow was not running")
    return notes


def _manual(task: DtTask) -> ManualDestination:
    service = action_label(task.action)
    nr_type, steps = SERVICES.get(service, ("", NO_EQUIVALENT))
    channel = task.input.get("channel") if service == "slack" else None
    label = printable(channel, 80) if isinstance(channel, str) else ""
    return ManualDestination(service=service, nr_type=nr_type, label=label, steps=steps)


def _active(wf: DtWorkflow) -> bool:
    event = wf.trigger.get("eventTrigger")
    return not (isinstance(event, dict) and event.get("isActive") is False)


def _plan(
    wf: DtWorkflow, value: dict[str, Any], key: str, name: str, prefix: str
) -> NotificationPlan:
    notes = _trigger_notes(value, _active(wf))
    addresses: list[str] = []
    manual: list[ManualDestination] = []
    tasks = [t for t in wf.tasks.values() if t.action != DQL_ACTION]
    for task in tasks:
        if action_label(task.action) == "email":
            addresses += [a for a in _recipients(task, notes) if a not in addresses]
        else:
            manual.append(_manual(task))
    if not tasks:
        notes.append("the workflow has no notification task")
    if len(addresses) > MAX_RECIPIENTS:
        notes.append(
            f"more than {MAX_RECIPIENTS} recipients: {len(addresses) - MAX_RECIPIENTS} left out"
        )
        addresses = addresses[:MAX_RECIPIENTS]
    if len(manual) > MAX_MANUAL:
        notes.append(
            f"more than {MAX_MANUAL} other notification tasks: {len(manual) - MAX_MANUAL} left out"
        )
        manual = manual[:MAX_MANUAL]
    channels = [IRChannel(kind=ChannelKind.EMAIL, target=a) for a in addresses]
    predicates = _tag_predicates(value, notes)
    destinations = plan_destinations(name, channels, prefix)
    workflow = None
    if destinations and predicates:
        workflow = PlannedWorkflow(
            name=printable(f"{prefix} {name}", MAX_NAME),
            condition_names=[],
            destination_indexes=list(range(len(destinations))),
            predicates=predicates,
        )
    elif destinations:
        notes.append(
            "no issue filter could be mapped, so no workflow is planned: one without a filter "
            "would send every issue; create the workflow by hand and attach these channels"
        )
    return NotificationPlan(
        topic=key,
        destinations=destinations,
        workflow=workflow,
        needs_review=True,
        notes=notes,
        manual_destinations=manual,
    )


def _unique_name(title: str, key: str, n: int) -> str:
    """The title, cut short; a short hash of the key follows when the title repeats or was cut."""
    short = printable(title, NAME_LIMIT)
    if n == 1 and len(title) <= NAME_LIMIT:
        return short
    return f"{short} [{hashlib.sha256(key.encode()).hexdigest()[:6]}]"


def davis_plans(workflows: list[DtWorkflow], prefix: str) -> list[NotificationPlan]:
    """One plan per Davis problem workflow, in order; other workflows are skipped.

    The plan topic is `<id>/<title>`, with `#n` after a repeat, so resource names stay distinct
    when an export repeats an id and title (scrubbed copies do).
    """
    seen: dict[str, int] = {}
    plans: list[NotificationPlan] = []
    if sum(davis_config(wf) is not None for wf in workflows) > MAX_PLANS:
        raise ValueError(f"more than {MAX_PLANS} Davis problem workflows; split the export")
    for wf in workflows:
        value = davis_config(wf)
        if value is None:
            continue
        base = f"{wf.id}/{wf.title}"
        seen[base] = seen.get(base, 0) + 1
        n = seen[base]
        key = base if n == 1 else f"{base}#{n}"
        name = _unique_name(wf.title, key, n)
        plans.append(_plan(wf, value, key, name, prefix))
    return plans


def _title(plan: NotificationPlan) -> str:
    return plan.topic.split("/", 1)[-1]


def render_report(plans: list[NotificationPlan]) -> str:
    """Markdown for the owner. Counts only for recipients: addresses stay in the plan files."""
    lines = [
        *header_lines(
            "Notification conversion report (Dynatrace Davis problem workflows)", "dynatrace"
        ),
        "",
        "Every plan below needs review. Nothing was created. Workflows are planned disabled.",
        "",
    ]
    for plan in plans:
        status = "workflow planned" if plan.workflow else "no workflow planned"
        lines += [
            f"## {md_cell(printable(_title(plan), 80))}",
            "",
            f"needs review: {status}; {len(plan.destinations)} email destinations, "
            f"{len(plan.manual_destinations)} manual destinations.",
        ]
        lines += [f"- note: {md_cell(n, 300)}" for n in plan.notes]
        for m in plan.manual_destinations:
            kind = m.nr_type or "none"
            where = f" ({md_cell(m.label, 80)})" if m.label else ""
            lines.append(f"- manual: {md_cell(m.service)}{where}, New Relic type {md_cell(kind)}")
            lines += [f"  - {md_cell(s, 300)}" for s in m.steps]
        lines.append("")
    return "\n".join(lines) + "\n"
