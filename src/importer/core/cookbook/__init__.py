"""The remediation cookbook: ranked methods for the things New Relic cannot do one-to-one.

A *blocker* is a kind of problem that stops a Grafana panel or alert rule from converting (for
example "sum() of a gauge"). Each blocker holds a ranked list of *methods*, drawn from six
families:

    A  change at ingest (collector, relabel, recording rules)
    B  rewrite in NRQL
    C  restructure (widgets, tables, variables, linked pages)
    D  redesign the alert
    E  replace with a New Relic native feature
    F  accept and document the difference

All text is fixed content kept in `data/*.toml` (no TOML value ever comes from an export). The
user guide `docs/user/remediation-cookbook.md` is rendered from the same data (see `render.py`),
and `plan-remediation` prints the recommended method and the alternatives next to each item.

Status of a method, set by a person after running `scripts/improve/verify_cookbook.py`:

    VERIFIED-VALUE   every NRQL in it was accepted and a result matched the arithmetic on test data
    VERIFIED-VALID   every NRQL in it was accepted by New Relic (read-only), no value was checked
    DOC-ONLY         read in documentation; nothing was run (collector files, Terraform, mutations)
    UNVERIFIED       from memory or inference; says so in `evidence`
"""

import re
import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

FAMILIES = {
    "A": "Change at ingest",
    "B": "Rewrite in NRQL",
    "C": "Restructure",
    "D": "Redesign the alert",
    "E": "Use a New Relic native feature",
    "F": "Accept and document the difference",
}
STATUSES = ("VERIFIED-VALUE", "VERIFIED-VALID", "DOC-ONLY", "UNVERIFIED")
# NRQL kinds: "nrql" is a full query without a time range (a dashboard widget or a condition
# query); "nrql-full" carries SINCE / COMPARE WITH / UNTIL; "nrql-alert" is a condition query and
# must obey the condition rules (no TIMESERIES, LIMIT, subquery, JOIN, SINCE).
NRQL_KINDS = ("nrql", "nrql-full", "nrql-alert")
OTHER_KINDS = ("promql", "yaml", "hcl", "graphql", "json", "shell", "text")


@dataclass(frozen=True)
class Example:
    kind: str
    title: str
    text: str
    # name -> literal text that stands in for a {{variable}} when the query is run read-only
    subst: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class Method:
    id: str
    family: str
    name: str
    when: str  # when to choose it (the decision rule)
    prerequisites: str
    needs_collector: bool  # needs access to the collector, Prometheus or Alloy config
    needs_reingest: bool  # the data must be sent again, or sent differently from now on
    latency: str  # what it does to freshness or alert delay
    changes: str  # what changes semantically: numbers, delay, cost
    effort: str
    risk: str
    verify: str  # how to prove it, with the command or the query
    status: str
    evidence: str  # what was run or read to earn the status
    sources: tuple[str, ...]
    examples: tuple[Example, ...]

    @property
    def label(self) -> str:
        return f"{self.family}: {FAMILIES[self.family]}"


@dataclass(frozen=True)
class Blocker:
    key: str
    title: str
    titles: tuple[str, ...]  # remediation rule titles this blocker explains
    match: str  # regex over variant, reasons and query; "" means the default for its titles
    scope: str  # how often it was measured
    summary: str
    decision: tuple[str, ...]
    ask: tuple[str, ...]  # what to ask the customer
    cannot: str  # what New Relic cannot do at all, if anything
    methods: tuple[Method, ...]

    @property
    def recommended(self) -> Method:
        return self.methods[0]


def _list(value: object) -> list[object]:
    if not isinstance(value, list):
        raise TypeError("cookbook data: expected a list")
    return value


def _example(raw: dict[str, object]) -> Example:
    subst = raw.get("subst", {})
    if not isinstance(subst, dict):
        raise TypeError("cookbook data: subst must be a table")
    return Example(
        str(raw["kind"]),
        str(raw["title"]),
        str(raw["text"]).strip("\n"),
        tuple(sorted((str(k), str(v)) for k, v in subst.items())),
    )


def _method(raw: dict[str, object]) -> Method:
    examples = _list(raw.get("example", []))
    sources = _list(raw.get("sources", []))
    return Method(
        id=str(raw["id"]),
        family=str(raw["family"]),
        name=str(raw["name"]),
        when=str(raw["when"]).strip(),
        prerequisites=str(raw["prerequisites"]).strip(),
        needs_collector=bool(raw["needs_collector"]),
        needs_reingest=bool(raw["needs_reingest"]),
        latency=str(raw["latency"]).strip(),
        changes=str(raw["changes"]).strip(),
        effort=str(raw["effort"]),
        risk=str(raw["risk"]).strip(),
        verify=str(raw["verify"]).strip(),
        status=str(raw["status"]),
        evidence=str(raw["evidence"]).strip(),
        sources=tuple(str(s) for s in sources),
        examples=tuple(_example(e) for e in examples),  # type: ignore[arg-type]
    )


def _blocker(raw: dict[str, object]) -> Blocker:
    methods = _list(raw.get("method", []))
    decision, ask, titles = _list(raw["decision"]), _list(raw["ask"]), _list(raw["titles"])
    return Blocker(
        key=str(raw["key"]),
        title=str(raw["title"]),
        titles=tuple(str(t) for t in titles),
        match=str(raw.get("match", "")),
        scope=str(raw["scope"]).strip(),
        summary=str(raw["summary"]).strip(),
        decision=tuple(str(d) for d in decision),
        ask=tuple(str(a) for a in ask),
        cannot=str(raw.get("cannot", "")).strip(),
        methods=tuple(_method(m) for m in methods),  # type: ignore[arg-type]
    )


@cache
def blockers() -> tuple[Blocker, ...]:
    """Every blocker, in the order of the files (dashboards first, then alerts, then routing)."""
    folder = resources.files("importer.core.cookbook") / "data"
    found: list[Blocker] = []
    for entry in sorted(folder.iterdir(), key=lambda p: p.name):
        if entry.name.endswith(".toml"):
            data = tomllib.loads(entry.read_text(encoding="utf-8"))
            found += [_blocker(b) for b in data.get("blocker", [])]
    return tuple(found)


def blocker_for(title: str, text: str = "") -> Blocker | None:
    """The blocker that explains a remediation rule title.

    Several blockers may share a title (one Grafana reason covers joins and comparisons); the
    ones with a `match` pattern are tried first against `text` (variant, reasons and query),
    then the one without a pattern. Returns None when the title has no cookbook entry.
    """
    own = [b for b in blockers() if title in b.titles]
    for b in own:
        if b.match and re.search(b.match, text, re.IGNORECASE):
            return b
    plain = [b for b in own if not b.match]
    chosen = plain or own
    return chosen[0] if chosen else None
