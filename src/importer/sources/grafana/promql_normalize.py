"""Settle the spelling of a PromQL expression before any reader looks at it.

Three things Prometheus accepts are written in more than one way, and the readers (the regular
expression reader of the dashboards, the alert handlers, the planner) know one spelling each:

- `# comment` runs to the end of the line, unless the `#` sits inside a string;
- a string may be `"..."`, `'...'` (the same escapes) or a backtick raw string; the readers
  know the double-quoted one, so the other two are rewritten into it with the same value;
- aggregation operators and `by` / `without` are keywords, which Prometheus reads in any case
  (`SUM(x) BY (job)`). Function names are case sensitive there, so `RATE(` is left alone and
  stays refused. (Prometheus's lexer lower-cases a word before it looks it up in the keyword
  table; this was read from memory of that lexer, not run against a Prometheus here.)

Only PromQL goes through here: LogQL uses backticks for line filters. Text that is not closed
properly is returned as it came, so the reader refuses it as before.
"""

import re

from importer.sources.grafana.promql_ast import AGGREGATORS

_AGGREGATE = re.compile(
    r"(?<![\w:$.{\[])(" + "|".join(sorted(AGGREGATORS)) + r")(?=\s*(?:\(|by\b|without\b))(?![\w:])",
    re.IGNORECASE,
)
_GROUPING = re.compile(r"(?<![\w:$.])(by|without)(?=\s*\()(?![\w:])", re.IGNORECASE)


def _double_quoted(body: str, quote: str) -> str:
    """The value of a `'...'` or backtick string body, written as a `"..."` string body."""
    out: list[str] = []
    i = 0
    while i < len(body):
        ch = body[i]
        if ch == "\\" and quote == "'" and i + 1 < len(body):
            nxt = body[i + 1]
            out.append(nxt if nxt == "'" else ch + nxt)  # \' needs no backslash in "..."
            i += 2
            continue
        if ch in '\\"':
            out.append("\\" + ch)
        elif ch == "\n":
            out.append("\\n")
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _string_end(text: str, start: int) -> int:
    """Index of the quote closing the string opened at `start`, or -1."""
    quote, j = text[start], start + 1
    while j < len(text):
        if text[j] == quote:
            return j
        j += 2 if text[j] == "\\" and quote != "`" else 1
    return -1


def _keywords(code: str) -> str:
    code = _AGGREGATE.sub(lambda m: m[1].lower(), code)
    return _GROUPING.sub(lambda m: m[1].lower(), code)


def normalize_promql(expr: str) -> str:
    """`expr` without comments, with every string double-quoted and keywords in lower case."""
    out: list[str] = []
    code_start = 0
    i = 0
    while i < len(expr):
        ch = expr[i]
        if ch not in "#\"'`":
            i += 1
            continue
        out.append(_keywords(expr[code_start:i]))
        if ch == "#":
            end = expr.find("\n", i)
            i = len(expr) if end < 0 else end
        else:
            end = _string_end(expr, i)
            if end < 0:
                return expr
            body = expr[i + 1 : end]
            out.append(expr[i : end + 1] if ch == '"' else f'"{_double_quoted(body, ch)}"')
            i = end + 1
        code_start = i
    out.append(_keywords(expr[code_start:]))
    return "".join(out)
