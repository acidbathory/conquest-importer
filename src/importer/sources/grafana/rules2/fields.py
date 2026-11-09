"""Rule fields that are not part of the query: where the rule lives, its labels and its runbook.

Reasons never carry arbitrary text from a rule. The few values below appear only when they are
short, made of plain characters and not secret-looking, and a label is shown only when its key is
on a short allow-list of routing words; every other label is counted, not shown. A runbook link
is passed on only when it is an https address without credentials, control characters or
token-like parts.
"""

import re
import unicodedata
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from importer.core.emit.common import RUNBOOK_URL
from importer.core.redact import looks_sensitive

MAX_LABELS = 10
MAX_VALUE = 40
MAX_PORT = 65535
# label keys that name where an alert goes or what it is about; anything else is only counted
LABEL_ALLOW_LIST = frozenset(
    {
        "severity",
        "team",
        "env",
        "environment",
        "service",
        "component",
        "tier",
        "cluster",
        "namespace",
        "region",
    }
)
VALUE = re.compile(rf"[A-Za-z0-9_.-]{{1,{MAX_VALUE}}}")
LONG_RUN = re.compile(r"[A-Za-z0-9]{24,}")
# part of a name that suggests a credential; matched inside query keys
SECRET_WORDS = re.compile(
    r"token|secret|pass|pwd|jwt|code|bearer|session|cookie|cred|auth|key|sig|dsn|private",
    re.IGNORECASE,
)
# the same words as whole words, for path segments (so "design" or "author" stay allowed)
SECRET_TOKENS = frozenset(
    {
        "token",
        "secret",
        "password",
        "passwd",
        "pass",
        "pwd",
        "jwt",
        "code",
        "bearer",
        "session",
        "cookie",
        "cred",
        "creds",
        "credential",
        "credentials",
        "auth",
        "key",
        "apikey",
        "sig",
        "signature",
        "dsn",
        "private",
    }
)
UNSAFE_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"})

N_ORIGIN = "grafana origin: {where}{labels}"
N_RUNBOOK_IGNORED = (
    "the runbook link was ignored because it is not an https address, or it holds credentials, "
    "control characters or a token-like part"
)


def _token_like(text: str) -> bool:
    """A long alphanumeric run, a sensitive-looking word, or letters and digits split by
    dashes into something long (a dashed token or a UUID)."""
    if LONG_RUN.search(text) or looks_sensitive(text):
        return True
    squeezed = re.sub(r"[-_.]", "", text)
    mixed = any(c.isdigit() for c in squeezed) and any(c.isalpha() for c in squeezed)
    return len(squeezed) >= 20 and squeezed.isalnum() and mixed


def _plain(value: object) -> str | None:
    """The value when it is short, plain and not secret-looking; else None."""
    if not isinstance(value, str) or not VALUE.fullmatch(value) or _token_like(value):
        return None
    return value


def _label_pairs(labels: Mapping[str, Any]) -> tuple[list[str], int]:
    shown: list[str] = []
    for key in sorted(labels):
        value = _plain(labels[key])
        if len(shown) < MAX_LABELS and key in LABEL_ALLOW_LIST and value:
            shown.append(f"{key}={value}")
    return shown, len(labels) - len(shown)


def origin_note(folder: str, group: str, labels: Mapping[str, Any]) -> str | None:
    """Folder, group and allow-listed labels as one note, or None when there is nothing."""
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


def _bad_path(path: str) -> bool:
    if ";" in path:
        return True
    for segment in path.split("/"):
        words = {w.lower() for w in re.split(r"[^A-Za-z0-9]+", segment) if w}
        if words & SECRET_TOKENS or _token_like(segment):
            return True
    return False


def _bad_query(query: str) -> bool:
    return any(SECRET_WORDS.search(k) or _token_like(v) for k, v in parse_qsl(query))


def _acceptable(url: str) -> bool:
    if any(unicodedata.category(c) in UNSAFE_CATEGORIES for c in url):
        return False
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        return False
    if port is not None and not 0 < port <= MAX_PORT:
        return False
    return (
        parts.scheme == "https"
        and bool(parts.hostname)
        and parts.username is None
        and parts.password is None
        and "@" not in parts.netloc
        and not parts.fragment
        and not _bad_path(parts.path)
        and not _bad_query(parts.query)
        and RUNBOOK_URL.fullmatch(url) is not None
    )


def runbook(annotations: Mapping[str, Any]) -> tuple[str | None, bool]:
    """(the link to pass on, whether a link was present but refused)."""
    url = annotations.get("runbook_url")
    if not isinstance(url, str) or not url.strip():
        return None, False
    return (url, False) if _acceptable(url) else (None, True)
