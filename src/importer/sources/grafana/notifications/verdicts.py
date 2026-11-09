"""What each Grafana contact point type becomes in New Relic, from the verdict data table.

A pure function of the contact point: no network, no New Relic IR. The destination is a plain
string (the New Relic destination type name, or "none"); the manual steps are fixed sentences
from `contact_point_verdicts.json`. Nothing from the export (names, labels, hosts) is copied
into a step or a reason.
"""

import json
from dataclasses import dataclass
from enum import StrEnum
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.models import ContactPoint

VERDICT_FILE = Path(__file__).with_name("contact_point_verdicts.json")


class Verdict(StrEnum):
    CONVERTS = "CONVERTS"
    WEBHOOK_TEMPLATE = "WEBHOOK TEMPLATE"
    MANUAL_STEP = "MANUAL STEP"
    NO_EQUIVALENT = "NO EQUIVALENT"


class WhenSet(BaseModel):
    fields: list[str]
    step: str


class OptionNote(BaseModel):
    field: str
    not_equal: list[str]
    step: str


class Fallback(BaseModel):
    verdict: Verdict
    target: str
    manual_step: str


class Row(BaseModel):
    verdict: Verdict
    target: str
    manual_step: str
    when_set: list[WhenSet] = []
    option_notes: list[OptionNote] = []
    requires_any: list[str] = []
    otherwise: Fallback | None = None


@dataclass(frozen=True)
class ContactPointVerdict:
    verdict: Verdict
    target: str  # a New Relic destination type name, or "none"
    manual_steps: tuple[str, ...]
    reasons: tuple[str, ...]


REASON_BY_VERDICT = {
    Verdict.WEBHOOK_TEMPLATE: R.R_CP_WEBHOOK_TEMPLATE,
    Verdict.MANUAL_STEP: R.R_CP_MANUAL,
    Verdict.NO_EQUIVALENT: R.R_CP_NONE,
}
UNKNOWN_STEP = (
    "This integration type is not one the importer knows. Recreate the notification by hand."
)
CUSTOM_TEMPLATE_STEP = (
    "A title or message uses a custom Go template, which is not converted. New Relic sends its "
    "own message; recreate the wording in the channel by hand."
)


@lru_cache(maxsize=1)
def verdict_table() -> dict[str, Row]:
    raw = json.loads(VERDICT_FILE.read_text())
    return {name: Row.model_validate(row) for name, row in raw["verdicts"].items()}


def _extra_steps(row: Row, cp: ContactPoint) -> list[str]:
    steps = [w.step for w in row.when_set if set(w.fields) & set(cp.fields_set)]
    for note in row.option_notes:
        value = cp.options.get(note.field)
        if value is not None and value not in note.not_equal:
            steps.append(note.step)
    return steps


def contact_point_verdict(cp: ContactPoint) -> ContactPointVerdict:
    """The verdict, destination type and manual steps for one contact point."""
    row = verdict_table().get(cp.type)
    if row is None:
        return ContactPointVerdict(
            Verdict.NO_EQUIVALENT, "none", (UNKNOWN_STEP,), (R.R_CP_UNKNOWN,)
        )
    verdict, target, step = row.verdict, row.target, row.manual_step
    if row.requires_any and not set(row.requires_any) & set(cp.fields_set) and row.otherwise:
        verdict, target, step = (
            row.otherwise.verdict,
            row.otherwise.target,
            row.otherwise.manual_step,
        )
    steps = [step] if step else []
    extra = _extra_steps(row, cp) if verdict is row.verdict else []
    reasons = [REASON_BY_VERDICT[verdict]] if verdict in REASON_BY_VERDICT else []
    if extra:
        steps += extra
        reasons.append(R.R_CP_WEBHOOK_SETTINGS)
    if "custom" in cp.templates.values():
        steps.append(CUSTOM_TEMPLATE_STEP)
        reasons.append(R.R_CUSTOM_TEMPLATE)
    return ContactPointVerdict(verdict, target, tuple(steps), tuple(reasons))
