"""`log alert` monitors that count log lines: `logs("search").index("*").rollup("count")...`.

The Datadog search is turned into ANDed filters on the `Log` event (an OR is allowed only between
plain values of one attribute). Other rollups (`avg` of a measure, `cardinality`, percentiles) and
the multi-query `formula()` form are manual with their own reasons.
"""

from typing import Any, Literal

from importer.core.emit.nrql import identifier
from importer.core.ir import (
    Confidence,
    CountFilter,
    CountSignal,
    EmitOptions,
    IRMonitor,
    Occurrences,
    Signal,
    SignalKind,
)
from importer.sources.datadog import reasons as R
from importer.sources.datadog.common import (
    Fail,
    base_fields,
    manual,
    message_parts,
    options_for,
    priority_note,
    thresholds,
    timing,
)
from importer.sources.datadog.logsearch import LAnd, LNode, LNot, LOr, LTerm, parse_log_search
from importer.sources.datadog.models import DatadogMonitor
from importer.sources.datadog.names import NameMap, resolve_attribute
from importer.sources.datadog.query import ChainQuery, event_window_seconds, parse_chain
from importer.sources.datadog.sanitize import shown

DATA_PATH = "needs-log-forwarding"
BELOW = ("<", "<=", "==")
Compare = Literal["gt", "gte", "lt", "lte", "exists", "missing"]
COMPARE_OPS: dict[str, Compare] = {">": "gt", ">=": "gte", "<": "lt", "<=": "lte"}


def log_attribute(names: NameMap, field: str) -> str:
    """The New Relic attribute for a log attribute or tag (`@evt.name` becomes `evt.name`)."""
    bare = field.removeprefix("@")
    mapped = resolve_attribute(names, field) if field in names.attributes else None
    if mapped is None and bare in names.attributes:
        mapped = resolve_attribute(names, bare)
    if mapped is not None:
        return mapped
    try:
        return identifier(bare)
    except ValueError:
        raise Fail(f"{R.M_LOG_SEARCH}: attribute {shown(field, 60)} is not a valid name") from None


def _term_filter(term: LTerm, names: NameMap, negate: bool) -> list[CountFilter]:
    if term.field is None:
        if "?" in term.value:
            raise Fail(f"{R.M_LOG_SEARCH}: a ? wildcard in free text")
        return [CountFilter(key="message", value=f"*{term.value}*", wildcard=True, negate=negate)]
    key = log_attribute(names, term.field)
    if term.kind == "exists":
        return [CountFilter(key=key, value="*", compare="missing" if negate else "exists")]
    if term.kind == "eq":
        if "?" in term.value and term.wildcard:
            raise Fail(f"{R.M_LOG_SEARCH}: a ? wildcard in {shown(term.field, 40)}")
        if term.wildcard and "%" in term.value:
            raise Fail(f"{R.M_LOG_SEARCH}: a % in a wildcard value cannot be escaped")
        return [CountFilter(key=key, value=term.value, wildcard=term.wildcard, negate=negate)]
    if negate:
        raise Fail(f"{R.M_LOG_SEARCH}: a negated comparison or range")
    if term.kind == "cmp":
        return [CountFilter(key=key, value=_number(term.value), compare=COMPARE_OPS[term.op])]
    low: Compare = "gte" if term.op == "[]" else "gt"
    high: Compare = "lte" if term.op == "[]" else "lt"
    return [
        CountFilter(key=key, value=_number(term.value), compare=low),
        CountFilter(key=key, value=_number(term.hi), compare=high),
    ]


def _number(text: str) -> str:
    try:
        float(text)
    except ValueError:
        raise Fail(
            f"{R.M_LOG_SEARCH}: comparison with {shown(text, 30)}, which is not a number"
        ) from None
    return text


def search_filters(node: LNode | None, names: NameMap) -> list[CountFilter]:
    if node is None:
        return []
    if isinstance(node, LTerm):
        return _term_filter(node, names, False)
    if isinstance(node, LNot):
        if isinstance(node.item, LTerm):
            return _term_filter(node.item, names, True)
        raise Fail(f"{R.M_LOG_SEARCH}: NOT over a group of terms")
    if isinstance(node, LAnd):
        out: list[CountFilter] = []
        for item in node.items:
            out += search_filters(item, names)
        return out
    return [_or_values(node, names)]


def _or_values(node: LOr, names: NameMap) -> CountFilter:
    terms = [i for i in node.items if isinstance(i, LTerm)]
    same = len(terms) == len(node.items) and len({t.field for t in terms}) == 1
    if not same or terms[0].field is None or any(t.kind != "eq" or t.wildcard for t in terms):
        raise Fail(f"{R.M_LOG_SEARCH}: OR is converted only between plain values of one attribute")
    return CountFilter(
        key=log_attribute(names, terms[0].field), value="", values=[t.value for t in terms]
    )


def _facets(chain: ChainQuery, names: NameMap) -> list[str]:
    by = chain.call("by")
    if by is None:
        return []
    parts = [p.strip() for arg in by.args for p in str(arg).split(",")]
    return [log_attribute(names, p) for p in parts if p]


def _rollup(chain: ChainQuery) -> None:
    rollup = chain.call("rollup")
    if rollup is None or not rollup.args:
        return
    what = str(rollup.args[0])
    if what != "count":
        raise Fail(R.M_LOG_ROLLUP.format(what=shown(f"rollup({what})", 40)))
    if len(rollup.args) > 1 and rollup.args[1] not in ("", "*"):
        raise Fail(R.M_LOG_ROLLUP.format(what="a count over a measure"))


def translate_log(m: DatadogMonitor) -> IRMonitor:
    base = base_fields(m)
    chain = parse_chain(m.query_text)
    priority = m.priority_level
    try:
        if not chain.ok:
            first = chain.diagnostics[0]
            raise Fail(f"{R.M_UNREADABLE}: {shown(first.code, 40)}: {shown(first.message, 100)}")
        if chain.head == "formula":
            raise Fail(R.M_FORMULA_MONITOR)
        if chain.head != "logs" or not chain.head_args or not isinstance(chain.head_args[0], str):
            raise Fail(f'{R.M_UNREADABLE}: the query does not start with logs("...")')
        _rollup(chain)
        last = chain.call("last")
        window = event_window_seconds(last.args[0]) if last and last.args else None
        if window is None:
            raise Fail(R.M_LOG_WINDOW)
        timed = timing(window)
        search = parse_log_search(chain.head_args[0])
        if not search.ok or not search.lossless:
            problem = search.diagnostics[0].message if search.diagnostics else "unreadable"
            raise Fail(f"{R.M_LOG_SEARCH}: {shown(problem, 100)}")
        filters = search_filters(search.node, m.names)
        group_by = _facets(chain, m.names)
        if chain.threshold is None:
            raise Fail(R.M_THRESHOLD)
        limits = thresholds(m, chain.op, chain.threshold, 1.0, priority)
    except Fail as fail:
        return manual(base, [fail.reason], fail.steps)
    options = options_for(m, window, event_style=True)
    note, description, message_notes = message_parts(m)
    reasons = [
        R.N_LOG_ATTRS_UNVERIFIED.format(
            names=shown(", ".join([f.key for f in filters] + group_by), 120) or "none"
        ),
        R.N_LOG_CASE,
    ]
    if any(f.key == "message" for f in filters):
        reasons.append(R.N_LOG_FREE_TEXT)
    index = chain.call("index")
    if index and index.args and index.args[0] not in ("*", ""):
        reasons.append(R.N_INDEX.format(index=shown(index.args[0], 40)))
    below = chain.op in BELOW and bool(filters)
    if below:
        reasons.append(R.N_LOG_NULL)
    reasons += [*timed.notes, *limits.notes, *options.notes, *message_notes]
    unmapped: dict[str, Any] = {**options.unmapped, **priority_note(priority)}
    return IRMonitor(
        **base,
        signal=Signal(
            kind=SignalKind.LOG,
            raw_query=shown(m.query_text, 400),
            window_seconds=window,
            aggregation_window_seconds=window,
            aggregation_delay_seconds=options.delay,
            occurrences=Occurrences.ALL,
            count=CountSignal(filters=filters, group_by=group_by),
        ),
        thresholds=limits.terms,
        no_data=options.no_data,
        notification=note,
        confidence=Confidence.APPROXIMATE,
        reasons=list(dict.fromkeys(reasons)),
        unmapped=unmapped,
        emit=EmitOptions(
            slide_by_seconds=timed.slide,
            description_note=description or None,
            null_safe_count=True if below else None,
        ),
        data_path=DATA_PATH,
    )
