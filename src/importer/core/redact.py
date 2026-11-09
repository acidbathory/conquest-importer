"""Redaction of free-text names that may hold an address, a URL or a token.

Users put addresses and secrets in display names and paths. A name that looks like one is
replaced by a stand-in (usually the resource id tail), never shown in part.
"""

import re

from importer.core.text import printable

# an address or userinfo (@), a URL (://), a query (?), a key=value pair, a hex run, or a
# base64-looking run that mixes letters and digits
SENSITIVE = re.compile(
    r"@|://|\?|[A-Za-z0-9_.-]+=\S|[0-9A-Fa-f]{20,}"
    r"|(?=[A-Za-z0-9+/]*[0-9])(?=[A-Za-z0-9+/]*[A-Za-z])[A-Za-z0-9+/]{20,}"
)
MASK = "***"


def looks_sensitive(text: str) -> bool:
    return SENSITIVE.search(text) is not None


def safe_name(text: str, fallback: str, limit: int = 60) -> str:
    """The name, or `fallback` when it looks like an address, URL or token (or is empty)."""
    shown = printable(text, 10_000)
    if not shown.strip() or looks_sensitive(shown):
        return printable(fallback, limit)
    return shown[:limit]


def safe_path(path: str, limit: int = 200) -> str:
    """A URL path without query or fragment; segments that look like tokens become ***."""
    bare = path.split("?", 1)[0].split("#", 1)[0]
    segments = [MASK if looks_sensitive(s) else s for s in bare.split("/")]
    return printable("/".join(segments), limit)
