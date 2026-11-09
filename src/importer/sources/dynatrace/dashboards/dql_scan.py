"""A small scanner for DQL text: comments, strings, nesting and top-level splitting.

It does not parse DQL. It finds the structure the planner needs (pipeline commands, comma
separated arguments, `and`/`or` operands) without being fooled by `|`, `,` or `and` inside a
string, a backtick name, a comment or a nested bracket. Input is untrusted, so length and
nesting are capped and an unclosed string or bracket is a `ScanError` with a fixed message.
"""

import re
from dataclasses import dataclass

MAX_QUERY = 100_000
MAX_DEPTH = 64
OPEN = "([{"
CLOSE = ")]}"
WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*")


class ScanError(ValueError):
    """The text is too large or deep, or has an unclosed string, comment or bracket."""


@dataclass(frozen=True)
class Scanned:
    """DQL text with comments blanked (`text`) and string and backtick contents blanked too
    (`mask`). Both have the same length, so an index found in one slices the other."""

    text: str
    mask: str

    def sub(self, start: int, end: int) -> "Scanned":
        return Scanned(self.text[start:end].strip(), self.mask[start:end].strip())

    def __bool__(self) -> bool:
        return bool(self.text)


def scan(raw: str) -> Scanned:
    """Blank comments and the inside of strings; check the text is bounded and balanced."""
    if len(raw) > MAX_QUERY:
        raise ScanError("the query is too large")
    text: list[str] = []
    mask: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        ch = raw[i]
        nxt = raw[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            end = raw.find("\n", i)
            end = n if end < 0 else end
            text.append(" " * (end - i))
            mask.append(" " * (end - i))
            i = end
        elif ch == "/" and nxt == "*":
            end = raw.find("*/", i + 2)
            if end < 0:
                raise ScanError("a comment is not closed")
            end += 2
            text.append(" " * (end - i))
            mask.append(" " * (end - i))
            i = end
        elif ch in '"`':
            end = _string_end(raw, i)
            text.append(raw[i:end])
            mask.append(ch + "_" * (end - i - 2) + ch)
            i = end
        else:
            text.append(ch)
            mask.append(ch)
            i += 1
    scanned = Scanned("".join(text), "".join(mask))
    _check_balanced(scanned.mask)
    return scanned


def _string_end(raw: str, start: int) -> int:
    quote = raw[start]
    i = start + 1
    while i < len(raw):
        if raw[i] == "\\" and quote == '"':
            i += 2
            continue
        if raw[i] == quote:
            return i + 1
        i += 1
    raise ScanError("a string is not closed")


def _check_balanced(mask: str) -> None:
    stack: list[str] = []
    for ch in mask:
        if ch in OPEN:
            stack.append(ch)
            if len(stack) > MAX_DEPTH:
                raise ScanError("the query is nested too deeply")
        elif ch in CLOSE:
            if not stack or OPEN.index(stack.pop()) != CLOSE.index(ch):
                raise ScanError("brackets do not match")
    if stack:
        raise ScanError("a bracket is not closed")


def split_at(s: Scanned, separators: str) -> list[Scanned]:
    """Split at the characters in `separators` that are outside any bracket; drop empty parts."""
    parts: list[Scanned] = []
    depth, start = 0, 0
    for i, ch in enumerate(s.mask):
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth -= 1
        elif depth == 0 and ch in separators:
            parts.append(s.sub(start, i))
            start = i + 1
    parts.append(s.sub(start, len(s.mask)))
    return [p for p in parts if p]


def split_word(s: Scanned, word: str) -> list[Scanned]:
    """Split at the word `and`/`or` (any case) that stands outside every bracket."""
    pattern = re.compile(rf"(?<![\w.$])({word})(?![\w.])", re.IGNORECASE)
    depths = _depths(s.mask)
    parts: list[Scanned] = []
    start = 0
    for m in pattern.finditer(s.mask):
        if depths[m.start()] == 0:
            parts.append(s.sub(start, m.start()))
            start = m.end()
    parts.append(s.sub(start, len(s.mask)))
    return [p for p in parts if p]


def _depths(mask: str) -> list[int]:
    """Bracket depth at every index, computed once so a split stays linear."""
    depths: list[int] = []
    depth = 0
    for ch in mask:
        if ch in CLOSE:
            depth -= 1
        depths.append(depth)
        if ch in OPEN:
            depth += 1
    return depths


def unwrap(s: Scanned) -> Scanned:
    """Remove outer brackets that enclose the whole text, repeatedly."""
    while len(s.mask) >= 2 and s.mask[0] in OPEN and closes_at_end(s.mask):
        s = s.sub(1, len(s.mask) - 1)
    return s


def closes_at_end(mask: str) -> bool:
    depth = 0
    for i, ch in enumerate(mask):
        if ch in OPEN:
            depth += 1
        elif ch in CLOSE:
            depth -= 1
            if depth == 0:
                return i == len(mask) - 1
    return False


def head(s: Scanned) -> str:
    """The first word of the text (a command or function name), or ''."""
    m = WORD.match(s.mask)
    return m.group(0) if m else ""


def rest(s: Scanned) -> Scanned:
    """The text after its first word."""
    m = WORD.match(s.mask)
    return s.sub(m.end(), len(s.mask)) if m else s


def call_names(s: Scanned) -> list[str]:
    """Names of functions called anywhere in the text (a word directly before `(`)."""
    return re.findall(r"(?<![A-Za-z0-9_])([A-Za-z_][A-Za-z0-9_]*)\s*\(", s.mask)
