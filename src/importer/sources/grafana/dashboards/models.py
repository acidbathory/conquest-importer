"""Grafana dashboards as small models, read from untrusted JSON.

A loader never keeps the raw JSON: it reads a dashboard, copies what the coverage plan needs
into these models (strings capped, numbers clamped) and drops the rest. Anything it cannot
read becomes an `Unreadable` item with a fixed reason, so one bad dashboard never stops the
others.
"""

from pydantic import BaseModel, Field, PrivateAttr

MAX_DASHBOARDS = 5000
MAX_FILES = 10_000
MAX_LIBRARY = 5000  # library panels and folders read from an export
MAX_PANELS = 10_000  # per dashboard; the rest are counted and left out
MAX_DEPTH = 200  # nested rows or layouts; deeper levels are left out
MAX_TARGETS = 200  # per panel
MAX_VARIABLES = 500
MAX_ANNOTATIONS = 200
MAX_TEXT = 300  # titles, names, legends
MAX_EXPR = 10_001  # one more than the alert translator's MAX_EXPRESSION, so "too long" shows
MAX_QUERY = 2000  # a variable's query text
GRID_COLUMNS = 24
MAX_Y = 100_000

# why an item could not be read (fixed text; a person never sees raw parser messages)
U_BAD_JSON = "grafana dashboard file is not valid JSON or is nested too deeply"
U_TOO_BIG = "grafana dashboard file is larger than the importer accepts"
U_NOT_OBJECT = "grafana dashboard entry is not a JSON object"
U_UNKNOWN_SHAPE = "grafana dashboard entry has no panels, rows or layout, so it is not a dashboard"
U_INVALID = "grafana dashboard could not be read (a field has an unexpected type or value)"
U_ENCODING = "grafana dashboard file is not UTF-8 text (encoding)"

# fixed notes about a dashboard that was read but not completely
W_DEEP = f"nesting deeper than {MAX_DEPTH} levels was not read"
W_PANELS = f"more than {MAX_PANELS} panels; the rest were not read"
W_TARGETS = f"a panel has more than {MAX_TARGETS} queries; the rest were not read"
W_VARIABLES = f"more than {MAX_VARIABLES} variables; the rest were not read"
W_NOT_OBJECTS = "{count} entries in the panel list are not objects and were skipped"
W_UNPLACED = "{count} elements are not placed in the layout and were added at the end"
W_SCHEMA_NEWER = "schemaVersion {version} is newer than 42; read with the field names of 42"
W_BOTH_SHAPES = (
    "the file has panels and also v2 elements and a layout; it was read as a classic dashboard"
)
W_PLACED_TWICE = (
    "{count} elements are placed more than once in the layout; each placement is a panel"
)
W_DUPLICATE_UID = "{count} dashboards repeat the uid of an earlier one and were left out"
W_TOO_MANY_DASHBOARDS = "more than {limit} dashboards; the rest were not read"
W_TOO_MANY_FILES = "more than {limit} files; the rest were not read"
W_PLAN_FILE = "{count} plan files written by plan-grafana-dashboards were skipped"
PLAN_MARKER = "grafana-dashboards-plan"  # the `kind` of a plan.json
W_ANNOTATIONS = f"more than {MAX_ANNOTATIONS} annotation queries; the rest were not read"


class DsRef(BaseModel):
    """A data source reference as the dashboard wrote it (type and uid or name), not resolved."""

    type: str = ""
    uid: str = ""


class Target(BaseModel):
    ref_id: str = ""
    expr: str = ""
    ds: DsRef | None = None
    hidden: bool = False
    legend: str = ""
    fmt: str = ""
    instant: bool = False
    interval: str = ""  # a minimum interval set on the query
    malformed: bool = False  # the query text was present but not a string
    panel_id: int | None = None  # `-- Dashboard --` reuse: the panel whose result is used
    hint: str = ""  # data source family guessed from the keys, when the reference says nothing
    expr_type: str = ""  # a server-side expression's type (math, reduce, ...)


class Panel(BaseModel):
    id: int | None = None
    type: str = ""
    title: str = ""
    ds: DsRef | None = None
    targets: list[Target] = Field(default_factory=list)
    transformations: list[str] = Field(default_factory=list)  # ids, capped
    library_uid: str = ""
    library_found: bool = False
    repeat: str = ""
    x: int = 0
    y: int = 0
    w: int = GRID_COLUMNS
    h: int = 1
    section: str = ""  # title of the row, or of the tab and row, the panel sits in
    depth: int = 0
    mappings: int = 0
    overrides: int = 0
    links: int = 0
    text_mode: str = ""
    text_chars: int = 0
    time_shift: str = ""  # panel time override: same meaning as offset
    time_from: str = ""
    calcs: list[str] = Field(default_factory=list)  # how a stat reduces the series
    unit: str = ""


class Variable(BaseModel):
    name: str = ""
    kind: str = ""  # query, custom, constant, textbox, interval, datasource, adhoc, switch, ...
    query: str = ""
    ds: DsRef | None = None
    multi: bool = False
    include_all: bool = False
    regex: str = ""
    hidden: bool = False


class Annotation(BaseModel):
    name: str = ""
    ds_type: str = ""
    enabled: bool = True
    builtin: bool = False


class Dashboard(BaseModel):
    uid: str = ""
    title: str = ""
    folder: str = ""
    shape: str = "classic"  # classic, v1 or v2
    api_version: str = ""
    schema_version: int | None = None
    tags: int = 0
    panels: list[Panel] = Field(default_factory=list)
    variables: list[Variable] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    links: int = 0
    rows: int = 0
    collapsed_rows: int = 0
    row_repeats: int = 0
    tabs: int = 0
    inputs: dict[str, str] = Field(default_factory=dict)  # `${DS_X}` name -> plugin id
    warnings: list[str] = Field(default_factory=list)


class Unreadable(BaseModel):
    source: str  # file name, capped
    position: int  # index of the entry in the file
    reason: str


class Estate(BaseModel):
    dashboards: list[Dashboard] = Field(default_factory=list)
    unreadable: list[Unreadable] = Field(default_factory=list)
    library_panels: int = 0
    folders: dict[str, str] = Field(default_factory=dict)  # folder uid -> title
    warnings: list[str] = Field(default_factory=list)
    _seen: set[str] = PrivateAttr(default_factory=set)
    _repeated: set[str] = PrivateAttr(default_factory=set)

    def add(self, dashboard: Dashboard) -> bool:
        """Keep the dashboard unless an earlier one has its uid; a uid is counted once."""
        if dashboard.uid and dashboard.uid in self._seen:
            self._repeated.add(dashboard.uid)
            return False
        self._seen.add(dashboard.uid)
        self.dashboards.append(dashboard)
        return True

    def finish(self) -> None:
        if self._repeated:
            self.warnings.append(W_DUPLICATE_UID.format(count=len(self._repeated)))
            self._repeated = set()
