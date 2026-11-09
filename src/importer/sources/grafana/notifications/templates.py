"""Notification templates: only Grafana's default templates are translated.

Go templates in contact point fields and named template groups are untrusted text. Go and
Handlebars differ in escaping and in what a missing value does, so a custom template is never
converted: it is reported, and the plan carries the fixed New Relic body instead. The variable
map below is used to tell a person which New Relic variable is closest; it never builds output
from the template text.
"""

import hashlib
import re
from dataclasses import dataclass
from typing import Literal

from importer.sources.grafana.notifications.models import MAX_TEMPLATE_CHARS, Template

TemplateStatus = Literal["default", "empty", "custom"]

R_CUSTOM_TEMPLATE = "grafana custom notification template not converted"

# Reviewed Handlebars bodies for Grafana's default templates. Every variable is on the list in
# docs/research/newrelic-notifications-and-nrql-reference.md. A webhook payload wraps the text in
# the JSON string escaper before it is used; nothing here is JSON.
DEFAULT_BODIES: dict[str, str] = {
    "default.title": "{{ issueTitle }}",
    "default.message": (
        "{{ issueTitle }} is {{ stateText }} (priority {{ priorityText }}). {{ issuePageUrl }}"
    ),
}
NEW_RELIC_VARIABLES = frozenset(
    {"issueTitle", "stateText", "priorityText", "issuePageUrl", "state", "workflowName"}
)

_DEFAULT_CALL = re.compile(r'\{\{-?\s*template\s+"(default\.[a-z]+)"\s+\.\s*-?\}\}')

# (Go name, closest New Relic variable or "" when there is none, note). Report-only.
VARIABLE_MAP: list[tuple[str, str, str]] = [
    ("Status", "stateText", "active or closed instead of firing or resolved"),
    ("Alerts", "", "an issue groups incidents; there is no list of alerts"),
    ("Labels", "accumulations.tag", "only labels that are in the query's FACET"),
    ("Annotations", "annotations.description", "depends on how the description is written"),
    ("StartsAt", "issueActivatedAtUtc", ""),
    ("EndsAt", "issueClosedAtUtc", ""),
    ("GeneratorURL", "issuePageUrl", "leads into New Relic"),
    ("PanelURL", "violationChartUrl", "leads into New Relic"),
    ("DashboardURL", "issuePageUrl", "leads into New Relic"),
    ("SilenceURL", "", "a silence is a muting rule in New Relic"),
    ("Fingerprint", "issueId", ""),
    ("GroupKey", "issueId", ""),
    ("Receiver", "workflowName", "approximate"),
    ("ExternalURL", "issuePageUrl", ""),
    ("OrgID", "nrAccountId", ""),
    ("CommonLabels", "", "no equivalent"),
    ("GroupLabels", "", "no equivalent"),
    ("CommonAnnotations", "", "no equivalent"),
    ("ValueString", "", "alert values are lost unless an enrichment query re-reads the metric"),
    ("Values", "", "alert values are lost unless an enrichment query re-reads the metric"),
]
_GO_NAME = re.compile(r"[.$](" + "|".join(name for name, _, _ in VARIABLE_MAP) + r")\b")


@dataclass(frozen=True)
class TemplateResult:
    status: TemplateStatus
    handlebars: str  # the fixed body for a default; "" otherwise
    reason: str  # a fixed sentence for a custom template; "" otherwise
    hints: tuple[str, ...]  # "GoName -> new relic name (note)", only from the map above


def _hints(text: str) -> tuple[str, ...]:
    found = sorted({m.group(1) for m in _GO_NAME.finditer(text)})
    by_name = {name: (nr, note) for name, nr, note in VARIABLE_MAP}
    lines = []
    for name in found:
        nr, note = by_name[name]
        lines.append(f"{name} -> {nr or 'no equivalent'}" + (f" ({note})" if note else ""))
    return tuple(lines)


def translate_template(text: str) -> TemplateResult:
    """The fixed body for an empty or default template; a custom one is reported, not converted."""
    stripped = text.strip()
    if not stripped:
        return TemplateResult("empty", DEFAULT_BODIES["default.message"], "", ())
    call = _DEFAULT_CALL.fullmatch(stripped)
    if call and call.group(1) in DEFAULT_BODIES:
        return TemplateResult("default", DEFAULT_BODIES[call.group(1)], "", ())
    return TemplateResult("custom", "", R_CUSTOM_TEMPLATE, _hints(text[:MAX_TEMPLATE_CHARS]))


def make_template(name: str, text: str) -> Template:
    """A named template group. Text may hold a pasted secret: only a hash and the size stay."""
    digest = hashlib.sha256(text.encode()).hexdigest()
    return Template(name=name, sha256=digest, length=len(text))
