"""What a DQL query would need to become NRQL, as a list of fixed findings.

`analyse` reads the query's structure (see dql_scan.py) and records, per clause, whether it maps
to NRQL with the same meaning, maps with a change that a draft must state, or cannot map. The
rules follow section 2.9 of docs/research/dynatrace-dashboards-inventory-datapath.md, tightened
so that the plan never overstates:

- exact: every clause maps to NRQL with the same meaning and nothing is dropped: one `timeseries`
  or `fetch security.events` source; plain aggregations with no options; `by` on plain fields;
  filters of `==`, `!=`, comparisons, `in()`, null checks and `not`, joined by and/or, with
  literals or dashboard variables; one plain sort key; a numeric limit; plain renames; commands in
  an order NRQL keeps.
- draft: maps, but a clause is dropped or its meaning is not verified, and the finding says which:
  an entity-selection clause (Smartscape id or primary tag), a second sort key, link columns, a
  variable in the limit, an average of averages, `matchesValue`/`contains` (wildcard and case
  rules), an aggregation option, `rate`, an interval, a time range in the query, the select-all
  test of a variable, a sort by a series average.
- manual: anything else, such as join, lookup, append, expand, parse, dedup, Smartscape or Davis
  sources, `shift`, `scalar`, array arithmetic, computed fields, sort expressions, a sort or
  filter after a limit, `bins`, `nonempty`, `union`.
Dropping a clause under `filterOut`, which negates it, is manual, not a draft.

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
    closes_at_end,
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
    rf"^(matchesValue|in|contains|startsWith|endsWith|isNull|isNotNull)"
    rf"\(\s*{FIELD}\s*(?:,\s*{ARGUMENT}\s*)*\)$"
)
MATCHING = frozenset({"matchesValue", "contains", "startsWith", "endsWith"})
NOT = re.compile(r"^(?:not\b\s*)+", re.IGNORECASE)  # a run of negations is read at once
PARAMETER = re.compile(
    r"^(by|filter|shift|interval|from|to|timeframe|nonempty|bins|union)\s*:\s*", re.IGNORECASE
)
OPTION = re.compile(r"^(\w+)\s*:\s*(.*)$", re.DOTALL)
NAMED = re.compile(rf"^(?:{FIELD})\s*=(?!=)\s*")
SORT_KEY = re.compile(rf"^{FIELD}(?:\s+(?:asc|desc|ascending|descending))?$", re.IGNORECASE)
SERIES_SORT = re.compile(
    rf"^arrayAvg\(\s*({FIELD})\s*\)(?:\s+(?:asc|desc|ascending|descending))?$", re.IGNORECASE
)
SENTINEL = re.compile(r'^\$([A-Za-z_]\w*)\s*==\s*"\*"$')
VARIABLE_USE = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)")
RATE_VALUE = re.compile(r"^\d+\s*[smh]$")
# Parts of a query that only choose which Dynatrace entities to show. Matched on the masked text,
# so a string that merely contains one of these words is not a marker.
ENTITY = re.compile(
    r"(?<![\w.])dt\.smartscape_source\.id(?![\w.])|\$(?:PrimaryTag|SmartscapeId)\w*"
    r"|(?<![\w.])smartscapeNodes\b"
)
# Metric key prefixes that name data only Dynatrace has. cloud.aws.* and cloud.azure.* are left
# unflagged on purpose: the research note (section 2.9) lists documented New Relic counterparts
# (CloudWatch Metric Streams, AzureAppServicePlanSample), though not every metric is known to exist.
DYNATRACE_ONLY = (  # prefix -> label (a fixed set, shown in reports)
    ("dt.sfm.", "Dynatrace self-monitoring metrics"),
    ("dt.kubernetes.", "Dynatrace Kubernetes metrics"),
    ("dt.davis", "Davis"),
    ("dt.host.", "Dynatrace OneAgent host metrics"),
    ("dt.service.", "Dynatrace OneAgent service metrics"),
    ("dt.process.", "Dynatrace OneAgent process metrics"),
    ("dt.cloud.", "Dynatrace cloud metrics"),
    ("dt.entity", "Dynatrace entities"),
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
AFTER_LIMIT = frozenset({"sort", "filter", "filterOut", "summarize"})


@dataclass
class Analysis:
    """Findings for one query. Lists keep the order of first appearance, without repeats."""

    blockers: list[str] = field(default_factory=list)
    drafts: list[str] = field(default_factory=list)
    dynatrace_only: list[str] = field(default_factory=list)
    variables: list[str] = field(default_factory=list)
    limit_variables: list[str] = field(default_factory=list)
    sentinel_variables: list[str] = field(default_factory=list)
    metrics: list[str] = field(default_factory=list)
    aliases: set[str] = field(default_factory=set)
    limited: bool = False
    _seen: set[str] = field(default_factory=set, repr=False)

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

    def add_variables(self, text: str) -> None:
        """Remember each `$name` once; a set keeps this linear however many operands there are."""
        for name in VARIABLE_USE.findall(text):
            if name not in self._seen:
                self._seen.add(name)
                self.variables.append(name)


def _has_entity(s: Scanned) -> bool:
    return ENTITY.search(s.mask) is not None


def _drop_entity(a: Analysis, negated: bool) -> None:
    """An entity-selection clause is dropped (a draft); under filterOut that widens the result."""
    if negated:
        a.block(r.FILTER)
    else:
        a.draft(r.D_ENTITY)


def _atom(s: Scanned, a: Analysis) -> None:
    text = " ".join(s.text.split())
    a.add_variables(text)  # only clauses that are kept count as uses of a variable
    call = CALL.match(text)
    if call:
        if call.group(1) in MATCHING:
            a.draft(r.D_MATCH)
    elif not COMPARE.match(text):
        a.block(r.JOIN if re.search(r"\blookup\s*\(", s.mask) else r.FILTER)


def _or_group(parts: list[Scanned], a: Analysis, negated: bool) -> None:
    """Operands of an `or`: entity operands are dropped, a select-all test is replaced."""
    kept: list[Scanned] = []
    for part in parts:
        sentinel = SENTINEL.match(" ".join(part.text.split()))
        if sentinel:
            a.draft(r.D_SELECT_ALL)
            if sentinel.group(1) not in a.sentinel_variables:
                a.sentinel_variables.append(sentinel.group(1))
        elif _has_entity(part):
            _drop_entity(a, negated)
        else:
            kept.append(part)
    for part in kept:
        _expression(part, a, negated)


def _expression(s: Scanned, a: Analysis, negated: bool = False) -> None:
    s = unwrap(s)
    ors = split_word(s, "or")
    if len(ors) > 1:
        _or_group(ors, a, negated)
        return
    ands = split_word(s, "and")
    if len(ands) > 1:
        for part in ands:
            _operand(part, a, negated)
        return
    leading = NOT.match(s.mask)
    if leading:
        _expression(s.sub(leading.end(), len(s.mask)), a, negated)
        return
    _atom(s, a)


def _operand(op: Scanned, a: Analysis, negated: bool) -> None:
    if len(split_word(unwrap(op), "or")) <= 1 and _has_entity(op):
        _drop_entity(a, negated)
    else:
        _expression(op, a, negated)


def filter_clause(s: Scanned, a: Analysis, negated: bool = False) -> None:
    """A `filter` argument: each top-level `and` operand is plumbing, or must be translatable."""
    for operand in split_word(unwrap(s), "and"):
        _operand(operand, a, negated)


def _plain_field(s: Scanned) -> str:
    """The field name when the text is one field (optionally `alias =` first), else ''."""
    body = NAMED.sub("", s.text, count=1) if NAMED.match(s.text) else s.text
    return body.strip("`") if re.fullmatch(FIELD, body) else ""


def _option(option: Scanned, function: str, a: Analysis) -> None:
    """An argument after the metric: `rate:`, `scalar:`, `default:` ... or a bare percentile."""
    named = OPTION.match(option.text)
    if not named:
        if not (function == "percentile" and re.fullmatch(r"\d+(?:\.\d+)?", option.text)):
            a.block(r.AGGREGATION)
        return
    key, value = named.group(1).lower(), named.group(2).strip()
    if key == "scalar":
        a.block(r.SCALAR)
    elif key == "rate":
        a.draft(r.D_RATE if RATE_VALUE.match(value) else r.D_OPTION)
    else:
        a.draft(r.D_OPTION)


def _aggregation(s: Scanned, a: Analysis) -> None:
    """One `[name =] function(metric[, options])` of a timeseries command."""
    named = NAMED.match(s.text)
    body = s.sub(named.end(), len(s.text)) if named else s
    name = head(body)
    inner = rest(body)
    if (
        name not in TIMESERIES_FUNCTIONS
        or not inner.mask.startswith("(")
        or not closes_at_end(inner.mask)
    ):
        a.block(r.AGGREGATION)
        return
    arguments = split_at(unwrap(inner), ",")
    metric = _plain_field(arguments[0]) if arguments else ""
    if not metric:
        a.block(r.AGGREGATION)
        return
    a.metric(metric)
    if named:
        a.aliases.add(named.group(0).split("=")[0].strip().strip("`"))
    for option in arguments[1:]:
        _option(option, name, a)


def _by(s: Scanned, a: Analysis) -> None:
    for item in split_at(unwrap(s), ","):
        if ENTITY.search(item.mask):
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
    elif name == "interval":
        a.draft(r.D_INTERVAL)
    elif name in ("from", "to", "timeframe"):
        a.draft(r.D_TIMEFRAME)
    else:  # bins, nonempty, union change which series or points exist
        a.block(r.PARAMETER)


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
        if dataset.startswith("dt.entity"):
            a.data("Dynatrace entities")
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


def _filter_out(args: Scanned, a: Analysis) -> None:
    """filterOut keeps the rows that do NOT match, so the whole expression is negated."""
    filter_clause(args, a, negated=True)


def _sort_key(key: Scanned, a: Analysis) -> None:
    text = key.text
    if key.mask.startswith("{"):  # {field, direction:"descending"}
        items = split_at(unwrap(key), ",")
        text = items[0].text if items else ""
    text = " ".join(text.split())
    series = SERIES_SORT.match(text)
    if SORT_KEY.match(text):
        return
    if series and series.group(1).strip("`") in a.aliases:
        a.draft(r.D_SORT_SERIES)
    else:
        a.block(r.SORT_EXPR)


def _sort(args: Scanned, a: Analysis) -> None:
    keys = split_at(args, ",")
    if len(keys) > 1:
        a.draft(r.D_SORT)
    for key in keys[:1]:
        _sort_key(key, a)


def _limit(args: Scanned, a: Analysis) -> None:
    a.limited = True
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


def _summary_item(item: Scanned, a: Analysis) -> None:
    named = NAMED.match(item.text)
    body = item.sub(named.end(), len(item.text)) if named else item
    inner = rest(body)
    if not head(body) or not inner.mask.startswith("(") or not closes_at_end(inner.mask):
        a.block(r.COMPUTED)  # arithmetic between functions, or not a function at all
        return
    for name in call_names(body):
        if name == "arrayAvg":
            a.draft(r.D_AVERAGE)
        elif name not in SUMMARY_FUNCTIONS:
            a.block(r.AGGREGATION)


def _summarize(args: Scanned, a: Analysis) -> None:
    for part in split_at(args, ","):
        named = PARAMETER.match(part.text)
        if named:
            if named.group(1).lower() == "by":
                _by(part.sub(named.end(), len(part.text)), a)
            else:
                a.block(r.PARAMETER)
            continue
        items = split_at(unwrap(part), ",") if part.mask.startswith("{") else [part]
        for item in items:
            _summary_item(item, a)


def _fields(args: Scanned, a: Analysis) -> None:
    """fields, fieldsKeep, fieldsRemove, fieldsRename: only plain names and plain renames."""
    for item in split_at(args, ","):
        if not _plain_field(item):
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
    "filterOut": _filter_out,
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
    try:
        scanned = scan(query)
        commands = split_at(scanned, "|")
    except ScanError:
        a.block(r.Q_UNREADABLE)
        return a
    if not commands:  # empty, or only comments and separators
        a.block(r.Q_EMPTY)
        return a
    a.add_variables(scanned.text)
    _source(commands[0], a)
    for command in commands[1:]:
        name = head(command)
        if a.limited and name in AFTER_LIMIT:
            a.block(r.REORDER)
        COMMANDS.get(name, _UNKNOWN)(rest(command), a)
    return a
