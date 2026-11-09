"""Refuse deeply nested JSON before it is parsed, whatever the Python version.

How `json.loads` copes with 100,000 open brackets differs between interpreters (a
RecursionError, a decode error, or a parse). This scan does not: it is iterative, linear and
needs no recursion, so the limit is ours.
"""

from __future__ import annotations

MAX_JSON_DEPTH = 200  # real exports nest a few dozen levels at most

_MESSAGE = "JSON is nested too deeply to read"


class TooDeepError(ValueError):
    """The JSON nests brackets deeper than the limit."""

    def __init__(self, max_depth: int = MAX_JSON_DEPTH) -> None:
        super().__init__(f"{_MESSAGE} (more than {max_depth} levels)")


def check_depth(data: str | bytes, max_depth: int = MAX_JSON_DEPTH) -> None:
    """Raise TooDeepError when `[`/`{` nest deeper than `max_depth`; string contents are skipped."""
    raw = data.encode("utf-8", "surrogatepass") if isinstance(data, str) else data
    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:  # backslash
                escaped = True
            elif byte == 0x22:  # closing quote
                in_string = False
        elif byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):  # [ {
            depth += 1
            if depth > max_depth:
                raise TooDeepError(max_depth)
        elif byte in (0x5D, 0x7D) and depth:  # ] }
            depth -= 1
