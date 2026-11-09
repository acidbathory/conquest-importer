"""What a DQL query would need to become NRQL, as a list of fixed findings.

`analyse` reads the query's structure (see dql_scan.py) and records, per clause, whether it maps
to NRQL unchanged, maps with a change that a draft must state, or cannot map. The rules follow
section 2.9 of docs/research/dynatrace-dashboards-inventory-datapath.md:

- exact: one `timeseries` or `fetch security.events` source; plain aggregations; `by` on plain
  fields; filters of `==`, `!=`, `in()`, `matchesValue()`, `contains()`, null checks and `not`
  joined by and/or, with literals or dashboard variables; at most one sort key; a numeric limit;
  renames.
- draft: exact except for a clause that only selects Dynatrace entities (a Smartscape id or the
  primary tag), a second sort key, link columns, a variable in the limit, or an average of
  averages. The clause is dropped and the finding says so.
- manual: anything else, such as join, lookup, append, expand, parse, dedup, Smartscape or Davis
  sources, `shift`, array arithmetic, computed fields.

Every finding is a constant from reasons.py; no text of the query is copied.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from importer.sources.dynatrace.dashboards import reasons as r
from importer.sources.dynatrace.dashboards.dql_scan import (
    ScanError,
    Scanned,
    call_names,
    head,
    rest,
    scan,
    split_at,
    split_word,
    unwrap,
)

FIELD = r"(?:[A-Za-z_]\w*|`[^`]*`)(?:\.(?:[A-Za-z_]\w*|`[^`]*`))*"
LITERAL = r'(?:"[^"]*"|-?\d+(?:\.\d+)?|true|false|null)'
VARIABLE = r"\$[A-Za-z_]\w*(?::\w+)?"
ARGUMENT = rf"(?:{LITERAL}|{VARIABLE}|array\(\s*{VARIABLE}\s*\))"
COMPARE = re.compile(rf"^(?:{FIELD}|{VARIABLE})\s*(?:==|!=|<=|>=|<|>)\s*(?:{LITERAL}|{VARIABLE})$")
CALL = re.compile(
    rf"^(?:matchesValue|in|contains|startsWith|endsWith|isNull|isNotNull)"
    rf"\(\s*{FIELD}\s*(?:,\s*{ARGUMENT}\s*)*\)$"
)
NOT = re.compile(r"^(?:not\b\s*)+", re.IGNORECASE)  # a run of negations is read at once
PARAMETER = re.compile(
    r"^(by|filter|shift|interval|from|to|timeframe|nonempty|bins|union)\s*:\s*", re.IGNORECASE
)
NAMED = re.compile(rf"^(?:{FIELD})\s*=(?!=)\s*")
VARIABLE_USE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
# parts of a query that only choose which Dynatrace entities to show
ENTITY_MARKERS = ("dt.smartscape_source.id", "$PrimaryTag", "$SmartscapeId", "smartscapeNodes")
DYNATRACE_ONLY = (  # metric key prefix -> label (a fixed set, shown in reports)
    ("dt.sfm.", "Dynatrace self-monitoring metrics"),
    ("dt.kubernetes.", "Dynatrace Kubernetes metrics"),
    ("dt.davis", "Davis"),
)
TIMESERIES_FUNCTIONS = frozenset({"sum", "avg", "min", "max", "count", "median", "percentile"})
SUMMARY_FUNCTIONS = frozenset(
    {
        "count",
        "sum",
        "avg",
        "min",
        "max",
        "median",
        "percentile",
        "countDistinct",
        "countDistinctExact",
        "countDistinctApprox",
        "countIf",
        "takeMax",
        "takeMin",
        "takeFirst",
        "takeLast",
        "toDouble",
        "toLong",
    }
)
ARRAY_FUNCTION = re.compile(r"\barray\w*\(|\biCollectArray\(|\[\]")


@dataclass
class Analysis:
    """Findings for one query. Lists keep the order of first appearance, without repeats."""

    blockers: list[str] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)
    dynatrace_only: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    limit_variables: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)

    def block(self, reason: str) -> None:
        if reason not in self.blockers:
            self.blockers.append(reason)

    def draft(self, reason: str) -> None:
        if reason not in self.drafts:
            self.drafts.append(reason)

    def data(self, label: str) -> None:
        if label not in self.dynatrace_only:
            self.dynatrace_only.append(label)

    def metric(self, key: str) -> None:
        if key not in self.metrics:
            self.metrics.append(key)
        for prefix, label in DYNATRACE_ONLY:
            if key.startswith(prefix):
                self.data(label)


def collect_variables(text: str, found: list[str]) -> None:
    for name in VARIABLE_USE.findall(text):
        if name not in found:
            found.append(name)


def _is_entity_clause(s: Scanned) -> bool:
    return any(marker in s.text for marker in ENTITY_MARKERS)


def _atom(s: Scanned, a: Analysis) -> None:
    text = " ".join(s.text.split())
    if COMPARE.match(text) or CALL.match(text):
        return
    if re.search(r"\blookup\s*\(", s.mask):
        a.block(r.JOIN)
    else:
        a.block(r.FILTER)


def _expression(s: Scanned, a: Analysis) -> None:
    s = unwrap(s)
    for word in ("or", "and"):
        parts = split_word(s, word)
        if len(parts) > 1:
            for part in parts:
                _expression(part, a)
            return
    negated = NOT.match(s.mask)
    if negated:
        _expression(s.sub(negated.end(), len(s.mask)), a)
        return
    _atom(s, a)


def filter_clause(s: Scanned, a: Analysis) -> None:
    """A `filter` argument: each top-level `and` operand is plumbing, or must be translatable."""
    for operand in split_word(unwrap(s), "and"):
        if _is_entity_clause(operand):
            a.draft(r.D_ENTITY)
        else:
            collect_variables(operand.text, a.variables)
            _expression(operand, a)


def _plain_field(s: Scanned) -> str:
    """The field name when the text is one field (optionally `alias =` first), else ''."""
    body = NAMED.sub("", s.text, count=1) if NAMED.match(s.text) else s.text
    return body.strip("`") if re.fullmatch(FIELD, body) else ""


def _aggregation(s: Scanned, a: Analysis) -> None:
    """One `[name =] function(metric[, options])` of a timeseries command."""
    named = NAMED.match(s.text)
    body = s.sub(named.end(), len(s.text)) if named else s
    name = head(body)
    inner = rest(body)
    if name not in TIMESERIES_FUNCTIONS or not inner.mask.startswith("("):
        a.block(r.AGGREGATION)
        return
    arguments = split_at(unwrap(inner), ",")
    metric = _plain_field(arguments[0]) if arguments else ""
    if metric:
        a.metric(metric)
    else:
        a.block(r.AGGREGATION)


def _by(s: Scanned, a: Analysis) -> None:
    for item in split_at(unwrap(s), ","):
        if "dt.smartscape_source.id" in item.text:
            a.draft(r.D_ENTITY)
        elif not _plain_field(item):
            a.block(r.COMPUTED)


def _timeseries_parameter(name: str, value: Scanned, a: Analysis) -> None:
    if name == "by":
        _by(value, a)
    elif name == "filter":
        filter_clause(value, a)
    elif name == "shift":
        a.block(r.SHIFT)


def read_timeseries(args: Scanned, a: Analysis) -> None:
    for part in split_at(args, ","):
        named = PARAMETER.match(part.text)
        if named:
            _timeseries_parameter(named.group(1).lower(), part.sub(named.end(), len(part.text)), a)
        elif re.match(r"^\w+\s*:", part.mask):
            a.block(r.PARAMETER)
        elif part.mask.startswith("{"):
            for item in split_at(unwrap(part), ","):
                _aggregation(item, a)
        else:
            _aggregation(part, a)


def _fetch(args: Scanned, a: Analysis) -> None:
    dataset = head(args)
    if dataset == "security.events":
        a.data("Grail security events")
    elif dataset.startswith("dt.davis"):
        a.data("Davis")
        a.block(r.SRC_DAVIS)
    else:
        a.block(r.SRC_OTHER)


def _source(command: Scanned, a: Analysis) -> None:
    name = head(command)
    if name == "timeseries":
        read_timeseries(rest(command), a)
    elif name == "fetch":
        _fetch(rest(command), a)
    elif name in ("smartscapeNodes", "smartscapeEdges"):
        a.data("Smartscape")
        a.block(r.SRC_SMARTSCAPE)
    else:
        a.block(r.SRC_OTHER)


def _filter(args: Scanned, a: Analysis) -> None:
    filter_clause(args, a)


def _sort(args: Scanned, a: Analysis) -> None:
    if len(split_at(args, ",")) > 1:
        a.draft(r.D_SORT)


def _limit(args: Scanned, a: Analysis) -> None:
    if re.fullmatch(r"\d+", args.text):
        return
    used = VARIABLE_USE.findall(args.text)
    if used and re.fullmatch(rf"(?:toLong\(\s*)?{VARIABLE}(?:\s*\))?", args.text):
        a.draft(r.D_LIMIT)
        for name in used:
            if name not in a.limit_variables:
                a.limit_variables.append(name)
    else:
        a.block(r.LIMIT)


def _summarize(args: Scanned, a: Analysis) -> None:
    for part in split_at(args, ","):
        if PARAMETER.match(part.text):
            continue  # by: {...}
        for name in call_names(part):
            if name == "arrayAvg":
                a.draft(r.D_AVERAGE)
            elif name not in SUMMARY_FUNCTIONS:
                a.block(r.AGGREGATION)


def _fields(args: Scanned, a: Analysis) -> None:
    if "(" in args.mask:
        a.block(r.COMPUTED)


def _fields_add(args: Scanned, a: Analysis) -> None:
    text = args.text
    if "/ui/" in text or '"["' in text or '"]("' in text:
        a.draft(r.D_LINK)
    elif ARRAY_FUNCTION.search(args.mask):
        a.block(r.ARRAY_MATH)
    else:
        a.block(r.COMPUTED)


def _flag(reason: str) -> Callable[[Scanned, Analysis], None]:
    def mark(_args: Scanned, a: Analysis) -> None:
        a.block(reason)

    return mark


_UNKNOWN = _flag(r.COMMAND)
COMMANDS: dict[str, Callable[[Scanned, Analysis], None]] = {
    "filter": _filter,
    "filterOut": _filter,
    "sort": _sort,
    "limit": _limit,
    "summarize": _summarize,
    "fields": _fields,
    "fieldsKeep": _fields,
    "fieldsRemove": _fields,
    "fieldsRename": _fields,
    "fieldsAdd": _fields_add,
    "join": _flag(r.JOIN),
    "lookup": _flag(r.JOIN),
    "append": _flag(r.JOIN),
    "expand": _flag(r.EXPAND),
    "parse": _flag(r.EXPAND),
    "fieldsFlatten": _flag(r.EXPAND),
    "dedup": _flag(r.DEDUP),
}


def analyse(query: str) -> Analysis:
    """Findings for one DQL query; never raises on hostile text."""
    a = Analysis()
    if not query.strip():
        a.block(r.Q_EMPTY)
        return a
    try:
        scanned = scan(query)
        commands = split_at(scanned, "|")
    except ScanError:
        a.block(r.Q_UNREADABLE)
        return a
    collect_variables(scanned.text, a.variables)
    _source(commands[0], a)
    for command in commands[1:]:
        COMMANDS.get(head(command), _UNKNOWN)(rest(command), a)
    return a
