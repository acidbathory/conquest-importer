"""Classify a dashboard query by the tier of conversion it reaches, without converting it.

Tiers follow docs/research/grafana-dashboards.md section 8.2:

- 1: what the alert translator reads (a selector with matchers, one optional avg/sum/min/max).
- 2: tier 1 plus rate, irate, increase, histogram_quantile over buckets, topk, and LogQL rate.
- 3: tier 2 plus arithmetic between convertible terms and numbers, and count.

Parsing is not redone here. The expression is first rewritten to a form the alert translator's
own PromQL and LogQL handlers read (rate calls removed, variables replaced by a plain word),
and those handlers decide whether the rest is a convertible shape. A regex matcher is T1 only in
the forms the alert translator accepts (plain words, a prefix or suffix, alternatives of words);
any other regex needs RLIKE verification and stays manual.
`alert_reach` is the one place that touches the translator's private context and failure types;
the proposal asks for a public function there. The real conversion in the next wave replaces this
module. Whitespace is collapsed first and optional spaces are single, the input is capped at
10,000 characters, and no pattern has a nested quantifier; a long run of word characters is
refused before the translator's own patterns see it.
"""

import re
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, replace
from types import MappingProxyType

from importer.sources.grafana.dashboards import reasons as r
from importer.sources.grafana.dashboards.regex_matchers import mask
from importer.sources.grafana.translate import (
    BODY,
    BOTH,
    COUNTER_SUFFIXES,
    HANDLERS,
    LOKI_MATCHER,
    LOKI_NOT_SIMPLE,
    LOKI_PARSER,
    LOKI_REGEX,
    LOKI_WILDCARD,
    MAX_ALTERNATIVES,
    MAX_EXPRESSION,
    PREFIX,
    PROM_COUNTER,
    PROM_MATCHER,
    PROM_NAME,
    PROM_NOT_SIMPLE,
    QUOTED,
    R_AGG_MIX,
    SUFFIX,
    WORD,
    _Ctx,
    _Fail,
)
from importer.sources.grafana.translate import LOKI_FUNCTION as LOKI_FUNCTION_REASON

MAX_OPERANDS = 50
MAX_NESTING = 20  # parentheses around or inside arithmetic
PLACEHOLDER = "VARV"  # stands for a dashboard variable in the rewritten text
LONG_WORD = re.compile(r"\w{201}")  # no metric name is this long; keeps later patterns fast
AGG_MIX_PREFIX = R_AGG_MIX.split("{", 1)[0]  # the alert-only complaint about aggregate + reducer
LANGUAGES = ("prometheus", "loki")
# metric -> exact type from `grafana-prom-hints`; set by plan_dashboard for the length of one plan
METRIC_TYPES: ContextVar[Mapping[str, str]] = ContextVar(
    "METRIC_TYPES", default=MappingProxyType({})
)
NAME_TOKEN = re.compile(r"(?<![\w:$.])[A-Za-z_:][A-Za-z0-9_:]*")

NAME = r"[A-Za-z_:][A-Za-z0-9_:]*"
NUMBER = re.compile(rf"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|{PLACEHOLDER}")
VARIABLE = re.compile(r"\$\{(\w+)(:[^}]*)?\}|\$(\w+)|\[\[(\w+)(?::\w+)?\]\]")
RANGE = re.compile(r"\[ ?(?:(?:\d+[smhdwy])+|\$\{\w+\}|\$\w+|\[\[\w+\]\]) ?\]")
RANGE_NORMAL = "[5m]"
RATE_CALL = re.compile(rf"\b(?:rate|irate|increase) ?\( ?({NAME}) ?(\{{{BODY}\}})? ?\[5m\] ?\)")
RATE_LEFT = re.compile(r"\b(?:rate|irate|increase) ?\(")
LOKI_RATE = re.compile(r"\brate ?\(")
SELECTOR_GROUP = re.compile(rf"\{{({BODY})\}}")
REGEX_MATCHER = re.compile(r'(=~|!~) ?"((?:[^"\\]|\\.)*)"')
RE2_UNSUPPORTED = re.compile(r"\(\?[!=<]|\\[1-9]|\(\?P=|[\x00-\x1f\x7f]")
TOPK = re.compile(r"topk ?\( ?([^,]*?) ?, ?(.*)\)")
HISTOGRAM = re.compile(r"histogram_quantile ?\( ?(?:0?\.\d+|[01](?:\.\d+)?) ?, ?(.*)\)")
SUM_BY = re.compile(r"sum ?(?:by ?\(([^()]*)\) ?)?\((.*?)\)(?: ?by ?\(([^()]*)\))?")
COUNT_AGG = re.compile(r"count ?(?:by ?\([^()]*\) ?)?\(")
BUCKET_NAME = re.compile(r"[A-Za-z0-9_:]+_bucket\b")
GROUP_VARIABLE = re.compile(rf"\bby ?\([^()]*{PLACEHOLDER}")
AGG_START = re.compile(r"(?:sum|avg|min|max) ?(?:by ?\(([^()]*)\) ?)?\(")
TRAIL_BY = re.compile(r" ?by ?\(([^()]*)\) ?$")
ALL_LABELS = frozenset({"*"})  # an unaggregated selector keeps every label
AGGREGATOR_CALL = re.compile(r"\b(?:sum|avg|min|max|count) ?(?:by ?\([^()]*\) ?)?\(")
COMPARISON = re.compile(r"==|!=|>=|<=|[<>%^]|\b(?:and|or|unless|bool)\b")
SKELETON_BRACES = re.compile(r"\{[^{}]*\}")

# checked in order on the whole expression; a hit gives that reason
PROM_CHECKS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"\boffset\b"), r.Q_OFFSET),
    (re.compile(r"\[[^\]\[]*:[^\]\[]*\]"), r.Q_SUBQUERY),
    (re.compile(r"\bgroup_(?:left|right)\b|\b(?:on|ignoring) ?\("), r.Q_VECTOR),
    (re.compile(r"\b(?:bottomk|limitk|limit_ratio)\b"), r.Q_BOTTOMK),
    (re.compile(r"\babsent(?:_over_time)? ?\("), r.Q_ABSENT),
    (re.compile(r"(?<=\w)_over_time ?\("), r.Q_OVER_TIME),
    (re.compile(r"\b(?:delta|idelta|deriv|predict_linear|changes|resets) ?\("), r.Q_RANGE_FN),
    (re.compile(r"\bwithout\b"), r.Q_WITHOUT),
    (re.compile(r"\blabel_(?:replace|join) ?\("), r.Q_LABEL_FN),
    (
        re.compile(
            r"\b(?:time|vector|scalar|round|clamp|clamp_min|clamp_max|ceil|floor|abs|sqrt|exp"
            r"|ln|log2|log10|sort|sort_desc|stddev|stdvar|quantile|group|count_values"
            r"|timestamp|sgn|days_in_month|day_of_\w+|hour|minute|month|year) ?\("
            r"|\bhistogram_(?!quantile)\w+ ?\("
        ),
        r.Q_FUNCTION,
    ),
]
# what an alert-translator failure means for a dashboard (checked as a prefix, then by equality)
HANDLER_REASONS: dict[str, str] = {
    PROM_MATCHER: r.Q_MATCHER,
    PROM_COUNTER: r.Q_COUNTER,
    PROM_NAME: r.Q_NAME,
    PROM_NOT_SIMPLE: r.Q_NOT_SIMPLE,
    LOKI_PARSER: r.QL_PARSER,
    LOKI_FUNCTION_REASON: r.QL_FUNCTION,
    LOKI_REGEX: r.QL_REGEX,
    LOKI_WILDCARD: r.QL_WILDCARD,
    LOKI_MATCHER: r.QL_MATCHER,
    LOKI_NOT_SIMPLE: r.QL_NOT_SIMPLE,
}
BINARY_PREFIX = "promql uses a binary operator"


@dataclass(frozen=True)
class Verdict:
    tier: int | None  # 1, 2 or 3; None when the query stays manual
    reason: str = ""
    variables: frozenset[str] = frozenset()  # dashboard variables the query uses


@dataclass(frozen=True)
class _Result:
    tier: int  # 0 for a plain number
    reason: str = ""
    group: frozenset[str] | None = None  # labels the result is grouped by; None for a number


def _fail(reason: str) -> _Result:
    return _Result(0, reason)


def alert_reach(expr: str, language: str) -> str:
    """ "" when the alert translator's own handler reads `expr`, else the reason it gives.

    The alert-only complaint about an aggregate across series combined with a reducer does not
    apply to a chart, so it counts as read.
    """
    handler = HANDLERS.get(language)
    if handler is None:
        return r.Q_NOT_SIMPLE
    try:
        handler(expr, _Ctx(reducer="last", instant=True, range_seconds=None))
    except _Fail as failure:
        return "" if failure.reason.startswith(AGG_MIX_PREFIX) else failure.reason
    except (ValueError, KeyError, IndexError):  # a parsing surprise is a manual query
        return PROM_NOT_SIMPLE if language == "prometheus" else LOKI_NOT_SIMPLE
    return ""


def _skeleton(text: str) -> str:
    return SKELETON_BRACES.sub("{}", re.sub(QUOTED, '""', text))


def _handler_failure(text: str, language: str) -> str:
    """The dashboard reason for what the alert handler said about `text` ("" when it reads it)."""
    reason = alert_reach(text, language)
    if not reason:
        return ""
    if reason.startswith(BINARY_PREFIX):
        return r.Q_COMPARE
    mapped = HANDLER_REASONS.get(reason, r.QL_NOT_SIMPLE if language == "loki" else r.Q_NOT_SIMPLE)
    if mapped == r.Q_NOT_SIMPLE and len(AGGREGATOR_CALL.findall(_skeleton(text))) >= 2:
        return r.Q_NESTED
    return mapped


# --- rewriting ---


def _substitute(text: str, known: frozenset[str] | None) -> tuple[str, set[str], bool]:
    """Variables to a plain word and ranges to [5m]; returns the text, names used, formatted?"""
    used: set[str] = set()
    formatted = False

    def note(match: re.Match[str]) -> str:
        nonlocal formatted
        name = match[1] or match[3] or match[4]
        if match[2]:
            formatted = True
        if name.startswith("__"):  # a built-in such as $__rate_interval
            return PLACEHOLDER
        if known is not None and name not in known:
            return match[0]
        used.add(name)
        return PLACEHOLDER

    def ranged(match: re.Match[str]) -> str:
        VARIABLE.sub(note, match[0])
        return RANGE_NORMAL

    return VARIABLE.sub(note, RANGE.sub(ranged, text)), used, formatted


def plain_regex(value: str) -> bool:
    """True for the regex forms the alert translator accepts as a matcher."""
    if WORD.fullmatch(value):
        return True
    parts = value.split("|")
    if 1 < len(parts) <= MAX_ALTERNATIVES and all(WORD.fullmatch(p) for p in parts):
        return True
    return any(p.fullmatch(value) for p in (BOTH, PREFIX, SUFFIX))


def _regex_reason(text: str, language: str) -> str:
    """The reason a regex matcher cannot be carried over, or "" when every one is plain."""
    for group in SELECTOR_GROUP.finditer(text):
        for found in REGEX_MATCHER.finditer(group[1]):
            if RE2_UNSUPPORTED.search(found[2]):
                return r.QL_MATCHER if language == "loki" else r.Q_MATCHER
            if not plain_regex(found[2]):
                return r.Q_REGEX
    return ""


def _sub_outside_strings(
    pattern: re.Pattern[str], replace: Callable[[re.Match[str]], str], text: str
) -> str:
    """`pattern.sub`, but a match that starts inside a quoted string is left alone."""
    spans = [m.span() for m in re.finditer(QUOTED, text)]
    out: list[str] = []
    last = 0
    for found in pattern.finditer(text):
        if any(a <= found.start() < b for a, b in spans):
            continue
        out += [text[last : found.start()], replace(found)]
        last = found.end()
    return "".join([*out, text[last:]])


def _has_outside_strings(pattern: re.Pattern[str], text: str) -> bool:
    """True when `pattern` matches somewhere that is not inside a quoted string."""
    spans = [m.span() for m in re.finditer(QUOTED, text)]
    return any(not any(a <= f.start() < b for a, b in spans) for f in pattern.finditer(text))


def _unrate(text: str) -> str:
    """`rate(name{..}[5m])` becomes `name{..}` with the counter suffix removed."""

    def one(match: re.Match[str]) -> str:
        name = match[1]
        for suffix in COUNTER_SUFFIXES:
            if name.endswith(suffix):
                name = name[: -len(suffix)]
                break
        return name + (match[2] or "")

    return _sub_outside_strings(RATE_CALL, one, text)


# --- structure of the expression ---


def _mask(text: str) -> str:
    """The text with everything inside quotes or brackets blanked, so only depth 0 is visible."""
    out: list[str] = []
    depth, quote, escaped = 0, False, False
    for ch in text:
        if quote:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                quote = False
            out.append(" ")
        elif ch == '"':
            quote = True
            out.append(" ")
        elif ch in "([{":
            out.append(ch if depth == 0 else " ")
            depth += 1
        elif ch in ")]}":
            depth = max(0, depth - 1)
            out.append(ch if depth == 0 else " ")
        else:
            out.append(ch if depth == 0 else " ")
    return "".join(out)


def _split(text: str, masked: str) -> list[str] | str:
    """The operands around binary + - * / at depth 0, or a reason when other operators appear."""
    if COMPARISON.search(masked):
        return r.Q_COMPARE
    parts: list[str] = []
    start, previous = 0, ""
    for i, ch in enumerate(masked):
        if ch in "+-*/" and previous and (previous.isalnum() or previous in "_)]}"):
            if ch in "+-" and previous in "eE" and _exponent(masked, i):
                continue
            parts.append(text[start:i])
            start = i + 1
        if not ch.isspace():
            previous = ch
    parts.append(text[start:])
    return parts


def _exponent(masked: str, sign: int) -> bool:
    """True when the sign at `sign` belongs to a number such as 1e-3, not to a subtraction."""
    start = sign - 1
    while start > 0 and (masked[start - 1].isalnum() or masked[start - 1] in "_."):
        start -= 1
    return NUMBER.fullmatch(masked[start:sign] + "0") is not None


def _enclosed(masked: str) -> bool:
    """True when the whole text is one parenthesised group."""
    body = masked.strip()
    return body.startswith("(") and body.endswith(")") and body.count("(") == 1


# --- evaluation ---


def _expr(text: str, language: str, depth: int) -> _Result:
    text = text.strip()
    if depth > MAX_NESTING or not text:
        return _fail(r.Q_NOT_SIMPLE)
    masked = _mask(text)
    if _enclosed(masked):
        return _expr(text[1:-1], language, depth + 1)
    parts = _split(text, masked)
    if isinstance(parts, str):
        return _fail(parts)
    if len(parts) > MAX_OPERANDS:
        return _fail(r.Q_NOT_SIMPLE)
    if len(parts) > 1:
        return _arithmetic(parts, language, depth)
    if text[0] in "+-":  # a sign in front is arithmetic too
        inner = _expr(text[1:], language, depth + 1)
        if inner.reason or inner.tier == 0:
            return inner
        return _Result(max(3, inner.tier), group=inner.group)
    return _atom(text, language, depth)


def _arithmetic(parts: list[str], language: str, depth: int) -> _Result:
    tiers: list[int] = []
    groups: set[frozenset[str]] = set()
    for part in parts:
        found = _expr(part, language, depth + 1)
        if found.reason:
            return found
        tiers.append(found.tier)
        if found.group is not None:
            groups.add(found.group)
    if len(groups) > 1:  # the series would not match one to one
        return _fail(r.Q_BINARY_LABELS)
    group = next(iter(groups), None)
    return _Result(max(3, *tiers) if any(tiers) else 0, group=group)


def _atom(text: str, language: str, depth: int) -> _Result:
    if NUMBER.fullmatch(text):
        return _Result(0)
    if language == "loki":
        return _loki_atom(text)
    found = _topk(text, depth) or _histogram(text) or _count(text)
    return found or _selector_query(text, floor=1)


def _group_of(plain: str) -> frozenset[str]:
    """The labels a plain selector query is grouped by (everything when it is not aggregated)."""
    start = AGG_START.match(plain)
    if start is None:
        return ALL_LABELS
    trailing = TRAIL_BY.search(plain)
    labels = start[1] if start[1] is not None else (trailing[1] if trailing else "")
    return frozenset(x.strip() for x in labels.split(",") if x.strip())


def _hinted(text: str, types: Mapping[str, str]) -> tuple[str, bool]:
    """(text with every hinted gauge made free of a counter suffix, True if a hinted counter is
    read without rate). Hints replace the name-suffix guess of the alert handler."""
    bare = _sub_outside_strings(RATE_CALL, lambda _m: " ", text)
    counter = False
    for found in NAME_TOKEN.finditer(re.sub(QUOTED, '""', bare)):
        counter = counter or types.get(found[0], "gauge") != "gauge"

    def one(match: re.Match[str]) -> str:
        name = match[0]
        gauge = types.get(name) == "gauge" and name.endswith(COUNTER_SUFFIXES)
        return name + "_gauge" if gauge else name

    return _sub_outside_strings(NAME_TOKEN, one, text), counter


def _selector_query(text: str, floor: int) -> _Result:
    """A selector, optionally aggregated, possibly with rate calls inside; tier 2 if it had any."""
    types = METRIC_TYPES.get()
    if types:
        text, counter = _hinted(text, types)
        if counter:
            return _fail(r.Q_COUNTER)
    plain = _unrate(text)
    if _has_outside_strings(RATE_LEFT, plain):
        return _fail(r.Q_RATE_SHAPE)
    reason = _handler_failure(plain, "prometheus")
    if reason:
        return _fail(reason)
    return _Result(max(floor, 2 if plain != text else 1), group=_group_of(plain))


def _topk(text: str, depth: int) -> _Result | None:
    found = TOPK.fullmatch(text)
    if found is None:
        return None
    if not found[1].isdigit():
        return _fail(r.Q_TOPK_ARG)
    inner = _expr(found[2], "prometheus", depth + 1)
    if inner.reason:
        return inner
    if not inner.tier:
        return _fail(r.Q_NOT_SIMPLE)
    return _Result(max(2, inner.tier), group=inner.group)


def _histogram(text: str) -> _Result | None:
    found = HISTOGRAM.fullmatch(text)
    if found is None:
        return None
    inner = found[1]
    if BUCKET_NAME.search(_skeleton(inner)) is None:
        return _fail(r.Q_HISTOGRAM)
    grouped = SUM_BY.fullmatch(_unrate(inner))
    if grouped is None:
        return _selector_query(inner, 2)
    labels = [x.strip() for x in (grouped[1] or grouped[3] or "").split(",") if x.strip()]
    if "le" not in labels:
        return _fail(r.Q_HISTOGRAM)
    rest = ", ".join(label for label in labels if label != "le")
    plain = f"sum by ({rest}) ({grouped[2]})" if rest else grouped[2]
    result = _selector_query(plain, 2)
    return result if result.reason else _Result(2, group=result.group)


def _count(text: str) -> _Result | None:
    if not COUNT_AGG.match(text):
        return None
    result = _selector_query("sum" + text[len("count") :], 1)
    return result if result.reason else _Result(max(3, result.tier), group=result.group)


def _loki_atom(text: str) -> _Result:
    plain = _sub_outside_strings(LOKI_RATE, lambda _m: "count_over_time(", text)
    reason = _handler_failure(plain, "loki")
    if reason:
        return _fail(reason)
    return _Result(2 if plain != text else 1, group=ALL_LABELS)


# --- public ---


def variables_in(text: str) -> set[str]:
    """Names of the dashboard variables (not built-ins like $__interval) a text refers to."""
    names = (m[1] or m[3] or m[4] for m in VARIABLE.finditer(text[:MAX_EXPRESSION]))
    return {n for n in names if not n.startswith("__")}


def _prepare(expr: str, language: str, known: frozenset[str] | None) -> tuple[str, set[str], str]:
    """The rewritten text, the variables used and a reason when the query cannot be read."""
    text, used, formatted = _substitute(" ".join(expr.split()), known)
    if language == "prometheus":
        text, _carried = mask(text)  # matchers RLIKE carries exactly read as plain words
    if formatted:
        return text, used, r.Q_VAR_FORMAT
    if LONG_WORD.search(text):
        return text, used, r.Q_NOT_SIMPLE
    if language == "prometheus":
        skeleton = _skeleton(text)
        for pattern, reason in PROM_CHECKS:
            if pattern.search(skeleton):
                return text, used, reason
    if GROUP_VARIABLE.search(_skeleton(text)):
        return text, used, r.Q_GROUP_VARIABLE
    return text, used, _regex_reason(text, language)


UNARY_MATH = re.compile(r"(round|ceil|floor|abs) ?\((.*)\)")


def peel_math(expr: str) -> tuple[str, str]:
    """(function, inner text) when `expr` is one `round(...)`, `ceil(...)`, `floor(...)` or
    `abs(...)` call around everything; else ("", expr). NRQL has all four with one argument.
    `round(x, 0.5)` is not peeled (NRQL rounds to a multiple the other way round)."""
    found = UNARY_MATH.fullmatch(expr.strip())
    if found is None:
        return "", expr
    depth = 0
    for ch in re.sub(QUOTED, '""', found[2]):
        depth += ch == "("
        depth -= ch == ")"
        if depth < 0:
            return "", expr
    if depth != 0 or "," in _mask(found[2]):
        return "", expr
    return found[1], found[2]


def classify_query(expr: str, language: str, known: frozenset[str] | None = None) -> Verdict:
    """The tier `expr` reaches, or a reason. `known` lists the dashboard's variable names; with
    None every `$name` counts as a variable. Never raises."""
    if language not in LANGUAGES:
        return Verdict(None, r.Q_NOT_SIMPLE)
    if len(expr) > MAX_EXPRESSION:
        return Verdict(None, r.Q_TOO_LONG)
    function, inner = peel_math(expr) if language == "prometheus" else ("", expr)
    if function:
        verdict = classify_query(inner, language, known)
        return replace(verdict, tier=max(2, verdict.tier) if verdict.tier else None)
    text, used, reason = _prepare(expr, language, known)
    variables = frozenset(used)
    if reason:
        return Verdict(None, reason, variables)
    found = _expr(text, language, 0)
    if found.reason or found.tier == 0:
        return Verdict(None, found.reason or r.Q_NOT_SIMPLE, variables)
    return Verdict(found.tier, "", variables)


def classify_log_lines(expr: str, known: frozenset[str] | None = None) -> Verdict:
    """A raw LogQL stream (`{...} |= "x"`) as the query of a logs panel: tier 1 when readable."""
    text = " ".join(expr.split())
    if not text.startswith("{") or len(text) > MAX_EXPRESSION:
        return Verdict(None, r.QL_NOT_SIMPLE)
    wrapped = f"count_over_time({text} [5m])"
    return classify_query(wrapped, "loki", known)
