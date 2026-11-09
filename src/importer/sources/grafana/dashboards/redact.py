"""Remove secrets from dashboard JSON before it is written to disk.

Dashboards and library panels can carry credentials: data source URLs with `user:pass@`,
`password` and `secureJsonData` blocks, request headers, panel links with `?token=`, annotation
tokens. Two rules apply to every value, at any depth: a key that names a credential has its value
blanked, and every string has URL user info, credential-looking query parameters, bearer or basic
values and token-shaped words replaced by `***`. The walk is iterative, so deep JSON cannot
overflow the stack. This is a safety net, not a guarantee: a secret in a free-text title is only
caught if it looks like one of the patterns.
"""

import re
from typing import Any

SECRET_KEY = re.compile(
    r"password|passwd|passphrase|secret|token|authorization|api[_-]?key|private[_-]?key"
    r"|credential|headers?$|httpheader|basicauth|oauth|bearer|cookie|signature|session",
    re.IGNORECASE,
)
USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^/\s?#@]*@")
QUERY_SECRET = re.compile(
    r"(?i)([?&;](?:[a-z0-9_.-]*(?:token|key|password|passwd|pwd|secret|auth|sig|signature|"
    r"session|sid|jwt|credential)[a-z0-9_.-]*)=)[^&\s\"'#]*"
)
SCHEME_VALUE = re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{4,}")
TOKEN_WORD = re.compile(
    r"gl(?:sa|c|cp)_[A-Za-z0-9_=.-]+|dt0[a-z][0-9]{2}\.[A-Za-z0-9._-]+|AKIA[0-9A-Z]{16}"
    r"|gh[pousr]_[A-Za-z0-9]{20,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]*"
)
MASK = "***"
MAX_STRING = 1_000_000  # longer strings are masked only in their first part, to bound the work


class Count:
    """How many values were changed."""

    def __init__(self) -> None:
        self.value = 0


def clean_text(text: str, count: Count) -> str:
    """The string with URL user info, query secrets, bearer values and tokens masked."""
    out = text[:MAX_STRING]
    for pattern, replacement in (
        (USERINFO, rf"\1{MASK}@"),
        (QUERY_SECRET, rf"\1{MASK}"),
        (SCHEME_VALUE, rf"\1 {MASK}"),
        (TOKEN_WORD, MASK),
    ):
        out, changed = pattern.subn(replacement, out)
        count.value += changed
    return out


def redact(value: Any, count: Count) -> Any:
    """A copy of `value` with credentials blanked and strings cleaned."""
    root: list[Any] = [None]
    work: list[tuple[Any, Any, Any]] = [(root, 0, value)]  # (container, key, original)
    while work:
        container, key, item = work.pop()
        if isinstance(item, dict):
            clone: dict[str, Any] = {}
            container[key] = clone
            for name, inner in item.items():
                if isinstance(name, str) and SECRET_KEY.search(name):
                    count.value += inner not in ("", None, [], {})
                    clone[name] = ""
                else:
                    clone[name] = None
                    work.append((clone, name, inner))
        elif isinstance(item, list):
            copy: list[Any] = [None] * len(item)
            container[key] = copy
            work.extend((copy, i, inner) for i, inner in enumerate(item))
        elif isinstance(item, str):
            container[key] = clean_text(item, count)
        else:
            container[key] = item
    return root[0]
