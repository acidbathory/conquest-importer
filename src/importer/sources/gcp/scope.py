"""What a Cloud Monitoring time series filter means in New Relic.

`metric_filter` reads a filter such as
`metric.type = "x" AND resource.labels.zone = one_of("a", "b") AND metric.label."code" != "200"`
and returns the metric type, the label restrictions as `TagFilter`s (all joined with AND, which is
what a scope is) and notes. Anything the scope list cannot say stays manual with the reason, never
a query that means something else. Values reach NRQL only through the shared escaping in
`core.emit.nrql` (`quote_string`, `identifier`); this module checks the pieces that module cannot
(a `%` in a LIKE pattern, the attribute name).
"""

from dataclasses import dataclass, field

from importer.core.emit.nrql import identifier
from importer.core.ir import TagFilter
from importer.sources.gcp.filter import (
    FilterError,
    Group,
    Term,
    conjuncts,
    parse_expression,
    placeholders,
    unquote_field,
)

PROJECT_LABELS = {"project_id": "gcp.projectId"}
LABEL_PREFIXES = (
    ("resource.labels.", "resource"),
    ("resource.label.", "resource"),
    ("metric.labels.", "metric"),
    ("metric.label.", "metric"),
    ("metadata.user_labels.", "user"),
)
ORDERINGS = {">", ">=", "<", "<="}
LIKE_OPS = {"starts_with", "ends_with", "has_substring", ":"}
REGEX_META = set(".^$*+?()[]{}|\\")
MAX_LIST = 200
PLACEHOLDER_NOTE = (
    "the filter holds template placeholders ({names}), which are not real values: replace them "
    "with the values you use before relying on the query, or it matches nothing"
)
MULTI_METRIC = (
    "filter not supported: {what} selects several metrics; New Relic names each metric as its "
    "own attribute, so make one condition or chart series per metric"
)


class ScopeFail(Exception):
    """The filter cannot be converted; the message is the reason shown to the person."""


@dataclass(frozen=True)
class Scope:
    metric_type: str
    filters: list[TagFilter]
    notes: list[str]


def label_name(field: str) -> str | None:
    """New Relic's attribute for a Cloud Monitoring label (confirmed on live data).

    Resource labels become `gcp.<label>` (`gcp.zone`, `gcp.instance_id`, `gcp.topic_id`); the
    project is `gcp.projectId`; metric labels become `metric.<label>`; user labels of the
    resource (`metadata.user_labels.env`) become `gcp.labels.<key>` (confirmed in
    docs/research/gcp-metric-names-verified.md). System labels (`metadata.system_labels.*`) have
    no confirmed attribute and stay manual. Quoted label names
    (`resource.label."project_id"`) are the same label as the unquoted form. None when the field
    is not a resource or metric label.
    """
    try:
        return label_attribute(unquote_field(field))
    except FilterError:
        return None


def label_attribute(field: str) -> str | None:
    """Like `label_name` for a field that is already unquoted (as a parsed Term has it)."""
    for prefix, kind in LABEL_PREFIXES:
        if field.startswith(prefix):
            label = field[len(prefix) :]
            if kind == "user":
                return _valid_attribute(f"gcp.labels.{label}", field)
            if kind == "resource":
                return _valid_attribute(PROJECT_LABELS.get(label, f"gcp.{label}"), field)
            return _valid_attribute(f"metric.{label}", field)
    return None


def _valid_attribute(attribute: str, field: str) -> str:
    """The attribute when NRQL accepts it as a name; otherwise the condition stays manual."""
    try:
        identifier(attribute)
    except ValueError:
        raise ScopeFail(f"label {field!r} does not make a valid New Relic attribute name") from None
    return attribute


def _like(pattern: str, field: str) -> str:
    """A LIKE pattern text is only safe without `*` (it would become a wildcard) or `%`."""
    if "*" in pattern or "%" in pattern:
        raise ScopeFail(
            f"filter term on {field!r} matches a text containing * or %, which NRQL LIKE would "
            "read as a wildcard"
        )
    return pattern


def regex_to_match(pattern: str, *, full: bool) -> tuple[str, list[str]] | None:
    """A regular expression as ('eq', [value]), ('in', values) or ('like', [pattern with *]).

    Only shapes with an exact equivalent convert: a literal, a list of literals joined by `|`,
    and a literal with `.*` at either end. None for anything else (character classes, groups,
    repeats), which needs RLIKE. A search regex (`full` False) matches anywhere unless anchored.
    """
    if not full:
        pattern = pattern[1:] if pattern.startswith("^") else ".*" + pattern
        if pattern.endswith("$") and not pattern.endswith("\\$"):
            pattern = pattern[:-1]
        else:
            pattern += ".*"
    shapes = [_regex_part(p) for p in pattern.split("|")]
    if any(s is None for s in shapes):
        return None
    found = [s for s in shapes if s is not None]
    if len(found) == 1:
        return found[0][0], [found[0][1]]
    if all(kind == "eq" for kind, _ in found):
        return "in", [text for _, text in found]
    return None


def _regex_part(part: str) -> tuple[str, str] | None:
    """('eq', text) or ('like', pattern) for a literal with optional `.*` ends; else None."""
    lead = part.startswith(".*")
    part = part[2:] if lead else part
    trail = part.endswith(".*") and not part.endswith("\\.*")
    part = part[:-2] if trail else part
    chars: list[str] = []
    escaped = False
    for char in part:
        if escaped:
            if char.isalnum():  # \d, \w, \b and the like are not literals
                return None
            chars.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif char in REGEX_META:
            return None
        else:
            chars.append(char)
    text = "".join(chars)
    if escaped or "*" in text or "%" in text or (not text and (lead or trail)):
        return None
    if lead or trail:
        return "like", ("*" if lead else "") + text + ("*" if trail else "")
    return "eq", text


@dataclass(frozen=True)
class Match:
    """What a term matches: the fields of a TagFilter or CountFilter other than the key."""

    value: str = ""
    values: list[str] = field(default_factory=list)
    wildcard: bool = False
    negate: bool = False


def match_of(term: Term) -> Match:
    """The match a term makes."""
    field = term.field
    op = term.op
    if any(c in text for text in (term.value, *term.values) for c in "\n\r\x00"):
        raise ScopeFail(
            f"filter term on {field!r} has a line break or control character in a value"
        )
    if op == "=":
        return Match(value=term.value, negate=term.negate)
    if op == "in":
        if len(term.values) > MAX_LIST:
            raise ScopeFail(f"filter term on {field!r} lists more than {MAX_LIST} values")
        return Match(values=list(term.values), negate=term.negate)
    if op in LIKE_OPS:
        text = _like(term.value, field)
        pattern = {"starts_with": f"{text}*", "ends_with": f"*{text}"}.get(op, f"*{text}*")
        return Match(value=pattern, wildcard=True, negate=term.negate)
    if op in ("regex_full", "=~"):
        match = regex_to_match(term.value, full=op == "regex_full")
        if match is None:
            raise ScopeFail(
                f"filter term on {field!r} uses a regular expression ({term.value!r}) that "
                + (
                    "holds a template placeholder, so it is not a real pattern yet"
                    if "${" in term.value
                    else "is not a plain prefix, suffix, substring or list of values; NRQL has "
                    "RLIKE for this, which the converter does not emit yet"
                )
            )
        kind, values = match
        if kind == "in":
            return Match(values=values, negate=term.negate)
        return Match(value=values[0], wildcard=kind == "like", negate=term.negate)
    if op in ORDERINGS:
        raise ScopeFail(f"filter term on {field!r} compares with {op}, which is not supported")
    raise ScopeFail(f"filter term on {field!r} is not supported")


def term_filter(term: Term, attribute: str) -> TagFilter:
    """The restriction a label term puts on a series."""
    m = match_of(term)
    return TagFilter(
        key=attribute, value=m.value, values=m.values, wildcard=m.wildcard, negate=m.negate
    )


def _or_group(group: Group, notes: list[str]) -> list[TagFilter]:
    """An OR of restrictions on one label: one IN list, when that is what it says."""
    items = group.items
    if not all(isinstance(i, Term) for i in items):
        raise ScopeFail(
            "filter not supported: an OR between groups, which a New Relic scope cannot hold"
        )
    terms = [i for i in items if isinstance(i, Term)]
    fields = {t.field for t in terms}
    if len(fields) > 1:
        raise ScopeFail(
            "filter not supported: an OR between different labels ("
            + ", ".join(sorted(fields))
            + "): a New Relic scope is a list joined by AND, so write the OR by hand in the "
            "WHERE clause of the query"
        )
    (name,) = fields
    if name == "metric.type":
        raise ScopeFail(MULTI_METRIC.format(what="an OR of several metric types"))
    if name == "resource.type":
        notes.append("resource type is not part of the query (an OR of types)")
        return []
    attribute = label_attribute(name)
    if attribute is None:
        raise ScopeFail(f"filter term on {name!r} is not supported")
    if any(t.negate or t.op not in ("=", "in") for t in terms):
        raise ScopeFail(
            f"filter not supported: an OR over {name!r} that is not a list of equal values, "
            "which a scope list cannot hold"
        )
    values: list[str] = []
    for t in terms:
        for v in t.values if t.op == "in" else (t.value,):
            if v not in values:
                values.append(v)
    return [TagFilter(key=attribute, values=values)]


def metric_filter(text: str) -> Scope:
    """Metric type, label restrictions and notes from a time series filter."""
    try:
        tree = parse_expression(text)
    except FilterError as err:
        raise ScopeFail(f"filter not supported: {err}") from err
    parts = conjuncts(tree)
    metric_type = ""
    filters: list[TagFilter] = []
    notes: list[str] = []
    if names := placeholders(tree):
        notes.append(PLACEHOLDER_NOTE.format(names=", ".join("${" + n + "}" for n in names)))
    for part in parts:
        if isinstance(part, Group):
            filters += _or_group(part, notes)
            continue
        found = _term(part, notes)
        if isinstance(found, str):
            metric_type = found
        elif found is not None:
            filters.append(found)
    if not metric_type:
        raise ScopeFail("the filter has no metric.type")
    return Scope(metric_type, filters, notes)


def _term(term: Term, notes: list[str]) -> TagFilter | str | None:
    """A TagFilter for a label term, the metric type for `metric.type`, or None for a note."""
    if term.op == "variable":
        notes.append(
            f"the dashboard variable ${{{term.field}}} in the filter is not applied; the query "
            "is not narrowed by it (add a dashboard variable in New Relic)"
        )
        return None
    if term.field == "metric.type":
        if term.op == "=" and not term.negate:
            return term.value
        raise ScopeFail(MULTI_METRIC.format(what=f"metric.type {_describe(term)}"))
    if term.field == "resource.type":
        if term.op in ("=", "in") and not term.negate:
            shown = term.values if term.op == "in" else (term.value,)
            notes.append(f"resource type {', '.join(map(repr, shown))} is not part of the query")
            return None
        raise ScopeFail(f"filter term on 'resource.type' ({_describe(term)}) is not supported")
    attribute = label_attribute(term.field)
    if attribute is None:
        raise ScopeFail(f"filter term on {term.field!r} is not supported")
    return term_filter(term, attribute)


def _describe(term: Term) -> str:
    return ("not " if term.negate else "") + term.op
