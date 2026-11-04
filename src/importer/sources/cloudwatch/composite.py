"""Understand a CloudWatch composite alarm rule well enough to say how to rebuild it by hand.

New Relic has no composite condition, so nothing is converted. What we can do is parse the rule
(ALARM("a") AND (ALARM("b") OR NOT OK("c")) ...), classify its shape, and give concrete steps.
Parsing never raises: an unreadable rule is reported as such.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from importer.core.ir import Confidence
from importer.core.text import printable

_TOKEN = re.compile(
    r"""\s*(?:
        (?P<ref>(?:ALARM|OK|INSUFFICIENT_DATA)\(\s*(?:"[^"]*"|[^()"\s][^()]*?)\s*\))
      | (?P<op>\bAND\b|\bOR\b|\bNOT\b)
      | (?P<const>\bTRUE\b|\bFALSE\b)
      | (?P<paren>[()])
    )""",
    re.VERBOSE,
)
_REF = re.compile(r'^(ALARM|OK|INSUFFICIENT_DATA)\(\s*"?([^"]*?)"?\s*\)$')


class Shape(StrEnum):
    ANY = "any"  # ALARM(a) OR ALARM(b) ...
    ALL = "all"  # ALARM(a) AND ALARM(b) ...
    COMPLEX = "complex"  # mixes AND/OR, uses NOT, OK() or INSUFFICIENT_DATA(), or constants
    UNPARSED = "unparsed"


@dataclass(frozen=True)
class RuleInfo:
    shape: Shape
    children: tuple[str, ...]  # alarm names, in order of first use


def child_name(ref: str) -> str:
    """An alarm name, from either a plain name or an alarm ARN."""
    return ref.split(":alarm:", 1)[1] if ":alarm:" in ref else ref


def _tokens(rule: str) -> list[tuple[str, str]] | None:
    pos, out = 0, []
    rule = rule.strip()
    while pos < len(rule):
        m = _TOKEN.match(rule, pos)
        if m is None or m.end() == pos:
            return None
        kind = m.lastgroup or ""
        out.append((kind, m.group(kind)))
        pos = m.end()
    return out


class _Bad(Exception):
    """The rule is not valid composite-alarm syntax."""


class _Parser:
    """expr := operand ((AND | OR) operand)* ; operand := NOT operand | ( expr ) | ref | const."""

    def __init__(self, tokens: list[tuple[str, str]]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.ops: set[str] = set()
        self.children: list[str] = []
        self.simple = True  # only ALARM() refs joined by one kind of operator, no grouping

    def _next(self) -> tuple[str, str]:
        if self.pos >= len(self.tokens):
            raise _Bad
        self.pos += 1
        return self.tokens[self.pos - 1]

    def _peek(self) -> tuple[str, str]:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else ("", "")

    def expr(self) -> None:
        self.operand()
        while self._peek()[0] == "op" and self._peek()[1] in ("AND", "OR"):
            self.ops.add(self._next()[1])
            self.operand()

    def operand(self) -> None:
        kind, text = self._next()
        if kind == "op" and text == "NOT":
            self.simple = False
            self.operand()
        elif kind == "paren" and text == "(":
            self.simple = False
            self.expr()
            if self._next() != ("paren", ")"):
                raise _Bad
        elif kind == "ref":
            m = _REF.match(text)
            if m is None:
                raise _Bad
            self.simple &= m.group(1) == "ALARM"
            name = child_name(m.group(2))
            if name not in self.children:
                self.children.append(name)
        elif kind == "const":
            self.simple = False
        else:
            raise _Bad


def analyse_rule(rule: str) -> RuleInfo:
    tokens = _tokens(rule)
    if not tokens:
        return RuleInfo(Shape.UNPARSED, ())
    parser = _Parser(tokens)
    try:
        parser.expr()
    except _Bad:
        return RuleInfo(Shape.UNPARSED, ())
    if parser.pos != len(tokens):
        return RuleInfo(Shape.UNPARSED, ())
    children = tuple(parser.children)
    if not parser.simple or len(parser.ops) > 1:
        return RuleInfo(Shape.COMPLEX, children)
    return RuleInfo(Shape.ALL if parser.ops == {"AND"} else Shape.ANY, children)


def _show(names: list[str] | tuple[str, ...]) -> str:
    return ", ".join(printable(n, 60) for n in names)


def remediation(
    info: RuleInfo, child_status: dict[str, Confidence], actions: list[str]
) -> list[str]:
    """Manual steps for one composite alarm. `child_status` maps alarm name -> its confidence."""
    steps: list[str] = []
    converted = [c for c in info.children if child_status.get(c) in _OK]
    missing = [c for c in info.children if c in child_status and c not in converted]
    unknown = [c for c in info.children if c not in child_status]
    if info.shape is Shape.UNPARSED:
        return [
            "The composite rule could not be read automatically. Read it in the AWS console "
            "(CloudWatch > Alarms > the composite alarm > Rule), write down the intended logic "
            "in one sentence, then rebuild it using the options for AND / OR rules.",
        ]
    if info.shape is Shape.ANY:
        steps.append(
            "An OR of ALARM() states fires when any child fires, which separate conditions "
            "already do. No combined condition is needed."
        )
    elif info.shape is Shape.ALL:
        steps += [
            "An AND needs all children in ALARM at once; separate conditions would notify when "
            "any ONE fires (a different, noisier behaviour).",
            "If the children measure the same entity: rebuild as ONE NRQL condition whose query "
            "returns a value only while every child's threshold is breached, and alert on that.",
            "If they measure different entities: keep the child conditions and use workflow "
            "filters or issue correlation so a notification goes out only when all are open "
            "(check what your New Relic plan supports; this is not generated automatically).",
        ]
    else:
        steps += [
            "The rule uses NOT, OK(), INSUFFICIENT_DATA(), constants or mixed AND/OR grouping. "
            "New Relic conditions evaluate one signal each and cannot express this directly.",
            "Write the intended behaviour in one sentence, split it into AND-groups and "
            "OR-groups, then apply the AND / OR guidance to each group.",
        ]
    if converted:
        steps.append(f"Child alarms that convert to conditions: {_show(converted)}.")
    if missing:
        steps.append(
            f"Child alarms that do NOT convert (fix these first, see their own entries): "
            f"{_show(missing)}."
        )
    if unknown:
        steps.append(
            f"Child alarms not found in this export (another region or account, or deleted): "
            f"{_show(unknown)}. Export that region too, or remove the reference."
        )
    if actions:
        steps.append(
            f"Notifications: this composite used {len(actions)} action target(s). Add the same "
            "targets to the workflow that covers the child conditions, or the rebuilt condition."
        )
    return steps


_OK = (Confidence.EXACT, Confidence.APPROXIMATE)
