"""Tokenizer for the KQL subset (see `kql_parse.py`).

Untrusted text goes in; only plain tokens come out. A string is decoded here (the escapes `\\\\`,
`\\"` and `\\'` only), so no later stage sees a backslash sequence it has to interpret.
"""

import re
from dataclasses import dataclass

MAX_QUERY = 4000
SPAN_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
# operators written with a hyphen; the lexer joins them so `mv-expand` is not `mv minus expand`
HYPHENATED = frozenset(
    {
        "mv-expand",
        "mv-apply",
        "make-series",
        "parse-where",
        "parse-kv",
        "project-away",
        "project-keep",
        "project-rename",
        "project-reorder",
        "project-smart",
        "order-by",
        "as-of",
    }
)
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_SPAN = re.compile(r"(\d+)(s|m|h|d)(?![A-Za-z0-9_])")
_OPS = ("==", "!=", "<>", "<=", ">=", "=~", "!~", "..")
_SINGLE = "<>=(),|.+-*/;[]:%"


class KqlSyntax(Exception):
    """The text is not something this subset reads (the message says what)."""


class KqlUnsupported(Exception):
    """A real KQL construct with no New Relic form; `operator` names it for the reason."""

    def __init__(self, operator: str, detail: str = "") -> None:
        super().__init__(operator)
        self.operator = operator
        self.detail = detail

    def __str__(self) -> str:
        suffix = f" ({self.detail})" if self.detail else ""
        return f"{self.operator} has no New Relic form{suffix}"


@dataclass(frozen=True)
class Token:
    kind: str  # id, num, str, span, op, eof
    text: str
    value: float = 0.0  # a number, or the seconds of a span


def _string(text: str, start: int) -> tuple[str, int]:
    quote = text[start]
    out: list[str] = []
    i = start + 1
    while i < len(text):
        ch = text[i]
        if ch == "\\":
            nxt = text[i + 1 : i + 2]
            if nxt not in ("\\", '"', "'"):
                raise KqlUnsupported("string escape", "only \\\\, \\\" and \\' are read")
            out.append(nxt)
            i += 2
            continue
        if ch == quote:
            return "".join(out), i + 1
        if ch in "\n\r":
            raise KqlSyntax("a string runs over a line end")
        out.append(ch)
        i += 1
    raise KqlSyntax("a string in the query is not closed")


def tokenize(text: str) -> list[Token]:
    if len(text) > MAX_QUERY:
        raise KqlSyntax("the query is longer than the importer reads")
    tokens: list[Token] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch.isspace():
            i += 1
        elif text.startswith("//", i):
            end = text.find("\n", i)
            i = len(text) if end < 0 else end
        elif ch in "\"'":
            value, i = _string(text, i)
            tokens.append(Token("str", value))
        elif ch == "@" and text[i + 1 : i + 2] in ("'", '"'):
            raise KqlUnsupported('verbatim string (@"...")')
        elif ch.isdigit():
            span = _SPAN.match(text, i)
            if span:
                seconds = int(span[1]) * SPAN_UNITS[span[2]]
                tokens.append(Token("span", span[0], float(seconds)))
                i = span.end()
            else:
                num = _NUMBER.match(text, i)
                assert num is not None  # noqa: S101 - a digit starts it
                tokens.append(Token("num", num[0], float(num[0])))
                i = num.end()
        elif ch == "!" and _IDENT.match(text, i + 1):
            word = _IDENT.match(text, i + 1)
            assert word is not None  # noqa: S101
            tokens.append(Token("id", "!" + word[0].lower()))
            i = word.end()
        elif _IDENT.match(text, i):
            word = _IDENT.match(text, i)
            assert word is not None  # noqa: S101
            name = word[0]
            end = word.end()
            joined = _IDENT.match(text, end + 1) if text[end : end + 1] == "-" else None
            if joined and f"{name}-{joined[0]}".lower() in HYPHENATED:
                name, end = f"{name}-{joined[0]}", joined.end()
            tokens.append(Token("id", name))
            i = end
        elif text[i : i + 2] in _OPS:
            tokens.append(Token("op", text[i : i + 2]))
            i += 2
        elif ch in _SINGLE:
            tokens.append(Token("op", ch))
            i += 1
        else:
            raise KqlSyntax(f"the character {ch!r} is not read")
    tokens.append(Token("eof", ""))
    return tokens
