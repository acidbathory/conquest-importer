"""The shapes of the dashboard plan (plan.json). Plans are advice; nothing here creates anything.

Fields that hold text from a dashboard (`title`, `name`, `id`, `key`) are data for a person to
look at, JSON-escaped in plan.json and passed through `md_cell` in the report. Reasons and notes
are fixed sentences from reasons.py.
"""

from enum import StrEnum

from pydantic import BaseModel


class Verdict(StrEnum):
    EXACT = "exact"
    DRAFT = "draft"
    MANUAL = "manual"
    CONVERT = "convert"  # a markdown tile: it converts, possibly after the listed changes


class LayoutPlan(BaseModel):
    """New Relic grid position (12 columns) from the Dynatrace one (24 columns).

    The row-height factor is a proposal: UNVERIFIED (research note section 2.8).
    """

    column: int
    width: int
    row: int
    height: int


class TilePlan(BaseModel):
    id: str
    kind: str
    title: str = ""
    verdict: Verdict
    widget: str = ""  # the New Relic visualization id the tile would use
    reasons: list[str] = []  # why it is manual
    notes: list[str] = []  # what a draft changes, and data caveats
    no_data_source: list[str] = []  # Dynatrace-only data the tile reads
    variables: list[str] = []  # dashboard variables the query refers to
    limit_variables: list[str] = []  # the ones used in a LIMIT
    metrics: list[str] = []  # metric keys the query reads (Dynatrace names)
    action: str = ""  # markdown tiles: "keep" or "drop"
    flags: list[str] = []  # markdown tiles: "dynatrace-link", "image"
    layout: LayoutPlan | None = None


class VariablePlan(BaseModel):
    key: str
    type: str
    verdict: Verdict
    reasons: list[str] = []
    notes: list[str] = []
    no_data_source: list[str] = []
    depends_on: list[str] = []
    multiple: bool = False
    select_all: bool = False  # the default is the select-all value (* or a UUID followed by *)


class Counts(BaseModel):
    data: int = 0
    exact: int = 0
    draft: int = 0
    manual: int = 0
    markdown: int = 0
    no_data_source: int = 0  # tiles (any verdict) that read Dynatrace-only data
    variables_exact: int = 0
    variables_draft: int = 0
    variables_manual: int = 0

    def add(self, other: "Counts") -> None:
        for name in type(self).model_fields:
            setattr(self, name, getattr(self, name) + getattr(other, name))


class DashboardPlan(BaseModel):
    ref: str
    name: str
    kind: str  # "platform", "classic" or "unreadable"
    app: str = ""  # originAppId of a document (tells ready-made dashboards from user ones)
    private: bool | None = None
    reasons: list[str] = []  # why the whole dashboard is manual
    notes: list[str] = []
    counts: Counts = Counts()
    tiles: list[TilePlan] = []
    variables: list[VariablePlan] = []
    unreadable_tiles: int = 0


class DashboardPlans(BaseModel):
    dashboards: list[DashboardPlan] = []
    totals: Counts = Counts()
    copies_skipped: int = 0
    copy_names: list[str] = []  # names of the skipped copies (at most 50)
