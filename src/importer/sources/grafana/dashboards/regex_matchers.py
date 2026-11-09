"""Regex label matchers that NRQL `RLIKE` carries exactly, found before the query is read.

PromQL and NRQL `RLIKE` both use RE2 and match the whole value (research note
newrelic-grafana-data-and-query-analogues.md, F02). Two differences are handled here:

- PromQL treats a missing label as an empty value, so `job=~".*"` also matches series without
  the label while `job RLIKE '.*'` does not. A pattern that matches the empty string therefore
  stays manual, with its own reason.
- Only a small, boring syntax is accepted (letters, digits, `_ : / space . * + ? | ( ) [ ] -`,
  no backslash, no `(?`, no `^` or `$`), so that RE2 and Python agree on what it means and no
  escaping question reaches the NRQL string.

`mask` rewrites each accepted matcher to a plain word (`RXMASK<n>`) so the planner and the
alert translator's own handlers read the query as an equality; the converter maps the word back
through the returned table. `label!=""` and `=~".+"` mean "the label is present" and become
the `present` kind (`IS NOT NULL`).
"""

import re
from dataclasses import dataclass

from importer.sources.grafana.translate import QUOTED, WORD

MARK = "RXMASK"
MASKED = re.compile(rf"{MARK}(\d+)")
SAFE = re.compile(r"[A-Za-z0-9_:/ .*+?|()\[\]-]+")
SELECTOR = re.compile(r"\{((?:[^{}\"]|\"(?:[^\"\\]|\\.)*\")*)\}")
MATCHER = re.compile(r'([A-Za-z_][A-Za-z0-9_]*)\s*(=~|!~|!=)\s*"((?:[^"\\]|\\.)*)"')
MAX_PATTERN = 200
PRESENT = ".+"


@dataclass(frozen=True)
class RegexUse:
    """What a masked word stands for."""

    pattern: str  # the RE2 pattern, or PRESENT for "the label has a value"


def _carried(pattern: str) -> bool:
    """True when RLIKE reads `pattern` exactly like PromQL, and it does not match an empty value."""
    if not pattern or len(pattern) > MAX_PATTERN or not SAFE.fullmatch(pattern):
        return False
    if "(?" in pattern or "VARV" in pattern or "VARB" in pattern:  # variable stand-ins
        return False
    try:
        compiled = re.compile(pattern)
    except re.error:
        return False
    return compiled.fullmatch("") is None


def _plain(pattern: str) -> bool:
    """True for the forms the alert translator already reads (words, alternatives, prefix)."""
    if WORD.fullmatch(pattern):
        return True
    parts = pattern.split("|")
    if len(parts) > 1 and all(WORD.fullmatch(p) for p in parts):
        return True
    return re.fullmatch(rf"(?:\.\*)?{WORD.pattern}(?:\.\*)?", pattern) is not None


def mask(text: str) -> tuple[str, dict[str, RegexUse]]:
    """`text` with every matcher RLIKE can carry replaced by a plain stand-in, and the table."""
    table: dict[str, RegexUse] = {}
    spans = [m.span() for m in re.finditer(QUOTED, text)]

    def in_string(position: int) -> bool:
        return any(a < position < b for a, b in spans)

    def one_matcher(found: re.Match[str]) -> str:
        key, op, raw = found[1], found[2], found[3]
        if op == "!=":
            if raw != "":
                return found[0]
            pattern, new_op = PRESENT, "=~"
        else:
            pattern, new_op = raw, op
            if pattern != PRESENT and (_plain(pattern) or not _carried(pattern)):
                return found[0]
        word = f"{MARK}{len(table)}"
        table[word] = RegexUse(pattern)
        return f'{key}{new_op}"{word}"'

    def one_selector(found: re.Match[str]) -> str:
        if in_string(found.start()):
            return found[0]
        return "{" + MATCHER.sub(one_matcher, found[1]) + "}"

    return SELECTOR.sub(one_selector, text), table
