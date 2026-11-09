"""Rule fields that are not part of the query: where the rule lives, its labels and its runbook.

Reasons never carry arbitrary text from a rule. The few values below (folder, group, label keys
and values) appear only when they are short and made of plain characters, and only when they do
not look like a secret; anything else is counted, not shown. A runbook link is passed on only
when it is an https address without credentials or token-like query parameters.
"""

import re
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from importer.core.emit.common import RUNBOOK_URL

MAX_LABELS = 10
MAX_VALUE = 40
KEY = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,40}")
VALUE = re.compile(rf"[A-Za-z0-9_.:/-]{{1,{MAX_VALUE}}}")
LONG_TOKEN = re.compile(r"[A-Za-z0-9]{24,}")
SECRET_WORDS = re.compile(r"token|secret|passw|credential|auth|private|key|sig", re.IGNORECASE)

N_ORIGIN = "grafana origin: {where}{labels}"
N_RUNBOOK_IGNORED = (
    "the runbook link was ignored because it is not an https address, or it holds credentials "
    "or a token-like parameter"
)


def _plain(value: object) -> str | None:
    """The value when it is short, plain and not secret-looking; else None."""
    if not isinstance(value, str) or not VALUE.fullmatch(value) or LONG_TOKEN.search(value):
        return None
    return value


def _label_pairs(labels: Mapping[str, Any]) -> tuple[list[str], int]:
    shown: list[str] = []
    for key in sorted(labels):
        value = _plain(labels[key])
        if (
            len(shown) < MAX_LABELS
            and KEY.fullmatch(key)
            and value
            and not SECRET_WORDS.search(key)
        ):
            shown.append(f"{key}={value}")
    return shown, len(labels) - len(shown)


def origin_note(folder: str, group: str, labels: Mapping[str, Any]) -> str | None:
    """Folder, group and labels as one note, or None when the rule has none of them."""
    places = [
        f"{name} {text}"
        for name, value in (("folder", folder), ("group", group))
        if (text := _plain(value))
    ]
    pairs, hidden = _label_pairs(labels)
    if not (places or pairs or hidden):
        return None
    tail = ""
    if pairs:
        tail = "; labels: " + ", ".join(pairs)
    if hidden:
        tail += f"{'; ' if not pairs else ', '}{hidden} more not shown"
    return N_ORIGIN.format(where=", ".join(places) or "(not shown)", labels=tail)


def runbook(annotations: Mapping[str, Any]) -> tuple[str | None, bool]:
    """(the link to pass on, whether a link was present but refused)."""
    url = annotations.get("runbook_url")
    if not isinstance(url, str) or not url.strip():
        return None, False
    try:
        parts = urlsplit(url)
    except ValueError:
        return None, True
    token_like = any(SECRET_WORDS.search(k) for k, _ in parse_qsl(parts.query))
    ok = (
        parts.scheme == "https"
        and bool(parts.hostname)
        and not parts.username
        and not parts.password
        and not parts.fragment
        and not token_like
        and RUNBOOK_URL.fullmatch(url) is not None
    )
    return (url, False) if ok else (None, True)
