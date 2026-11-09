"""Models for Grafana notification objects, the shapes the loaders produce and the plans read.

Nothing here holds a secret. A contact point keeps its type, name, uid, the names of the
settings that hold a value and a few plain options; see `contact_points.py` for what is dropped.
Limits are named constants so a hostile export cannot make a loader run for long or grow large.
"""

import hashlib
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from importer.core.redact import looks_sensitive
from importer.core.text import printable

MAX_CONTACT_POINTS = 10_000
MAX_POLICY_DEPTH = 20
MAX_POLICY_NODES = 10_000
MAX_MATCHERS = 100
MAX_MATCHER_CHARS = 1000
MAX_GROUP_BY = 50
MAX_MUTE_TIMINGS = 1000
MAX_INTERVALS = 100
MAX_RANGES = 100
MAX_TEMPLATES = 1000
MAX_TEMPLATE_CHARS = 100_000
MAX_DATASOURCES = 1000
MAX_RULE_GROUPS = 10_000
MAX_RULES = 50_000
MAX_NAME = 200
MAX_ADDRESSES = 500
MAX_SETTING_KEYS = 200
MAX_FILES = 10_000
RULE_FILE_FORMAT_HELP = (
    "rule files must be JSON: PyYAML is not a dependency of the importer. Convert a rules.yml "
    "with `yq -o=json rules.yml > rules.json` (or read the rules through the Grafana ruler API "
    "with `importer extract-grafana-notifications`)"
)


class NotificationError(ValueError):
    """A fixed, safe sentence about input that cannot be read."""


class ContactPoint(BaseModel):
    """One integration of a contact point. Secret values never reach this model."""

    model_config = ConfigDict(extra="ignore")

    uid: str = ""
    name: str
    type: str
    fields_set: list[str] = []  # names of settings that hold a value
    redacted: list[str] = []  # the subset Grafana returned as [REDACTED] (a secret is stored)
    other_fields: int = 0  # settings left out because their name was not a plain identifier
    options: dict[str, str] = {}  # values of a known type: a boolean, an integer in range, an enum
    dropped_options: list[str] = []  # option fields whose value was not of the expected type
    url_host: str = ""  # host of a non-secret url field; never a path, query or credentials
    templates: dict[str, str] = {}  # template field -> default | empty | custom
    disable_resolve_message: bool = False
    # Email addresses are personal data: kept for type email only, never put in a report.
    addresses: list[str] = Field(default=[], repr=False, json_schema_extra={"sensitive": True})


class Matcher(BaseModel):
    name: str
    op: str  # = != =~ !~
    value: str


class PolicyNode(BaseModel):
    receiver: str = ""
    group_by: list[str] | None = None
    matchers: list[Matcher] = []
    unparsed_matchers: int = 0  # matcher strings that could not be read; the node is undecidable
    continue_: bool = False
    mute_time_intervals: list[str] = []
    active_time_intervals: list[str] = []
    group_wait_seconds: int | None = None
    group_interval_seconds: int | None = None
    repeat_interval_seconds: int | None = None
    routes: list["PolicyNode"] = []


class PolicyTree(BaseModel):
    root: PolicyNode
    node_count: int = 1
    incomplete: bool = False  # routes were left out (too deep or too many): do not plan from it
    problems: list[str] = []  # fixed sentences; no text from the export


class TimeRange(BaseModel):
    start: str
    end: str


class TimeInterval(BaseModel):
    times: list[TimeRange] = []
    weekdays: list[str] = []
    days_of_month: list[str] = []
    months: list[str] = []
    years: list[str] = []
    location: str = ""


class MuteTiming(BaseModel):
    name: str
    intervals: list[TimeInterval] = []


class Template(BaseModel):
    name: str
    sha256: str
    length: int
    defines: list[str] = []  # names the template defines; a redefined default makes calls custom
    text: str | None = None  # always None when read by the importer; see templates.py


class DataSource(BaseModel):
    type: str
    uid: str


class PromRule(BaseModel):
    """A rule of a Prometheus, Mimir or Loki rule file, group labels already merged."""

    kind: str  # alert | record
    name: str
    expr: str
    for_seconds: int = 0
    keep_firing_for_seconds: int = 0
    labels: dict[str, str] = {}
    notes: list[str] = []  # fixed sentences about values replaced by a placeholder


class PromRuleGroup(BaseModel):
    name: str
    namespace: str = ""
    interval_seconds: int | None = None
    kind: str = "prometheus"  # prometheus | loki; decides the query language
    rules: list[PromRule] = []
    skipped: int = 0  # entries in this group that could not be read


class NotificationExport(BaseModel):
    contact_points: list[ContactPoint] = []
    policy: PolicyTree | None = None
    mute_timings: list[MuteTiming] = []
    templates: list[Template] = []
    datasources: list[DataSource] = []
    rule_groups: list[PromRuleGroup] = []
    problems: list[str] = []


def safe_ref(text: str, limit: int = MAX_NAME) -> str:
    """The text, or a stable reference when it looks like an address, URL or token.

    The same text always gives the same reference, so a policy that names a contact point still
    finds it after the name was replaced.
    """
    shown = printable(text, 10_000)
    if looks_sensitive(shown):
        return "redacted-" + hashlib.sha256(text.encode()).hexdigest()[:12]
    return shown[:limit]


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []
