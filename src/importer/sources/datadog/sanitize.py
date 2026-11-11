"""Untrusted Datadog text on its way to an output: shortened, printable, secrets masked.

A monitor message holds `@handles` (a webhook, a pager service, an e-mail address), sometimes
copied commands with tokens, URLs with query strings or key blocks. None of that may reach a
condition description, an IR file or a report. `mask_message` keeps the words and replaces every
token that is, or follows, something that looks like a secret.
"""

import re

from importer.core.redact import MASK, looks_sensitive
from importer.core.text import printable

HANDLE = re.compile(r"(?<![\w.])@[^\s,;)\]]+")
URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://\S+")
# header-like or assignment-like text whose next token is a value to hide
SCHEME = re.compile(r"(?i)\b(bearer|basic)\s+\S+")
LABELLED = re.compile(
    r"(?i)\b(bearer|basic|authorization|api[-_ ]?key|dd-api-key|dd-application-key|token|"
    r"secret|password|passwd|pwd)\b(\s*[:=]?\s*)(\S+)"
)
PEM = re.compile(r"-----BEGIN [^-]*-----.*?-----END [^-]*-----", re.DOTALL)
SECRETISH_WORD = re.compile(r"(?i)secret|token|passw|credential|apikey|api-key|bearer")
MAX_MESSAGE = 500


def shown(text: object, limit: int = 120) -> str:
    """Untrusted text for a reason or a name: printable, short, on one line."""
    return " ".join(printable(str(text), limit).split())


def count_handles(message: str) -> int:
    return len(HANDLE.findall(message))


def mask_message(message: str, limit: int = MAX_MESSAGE) -> str:
    """The message with handles, URLs, labelled values and token-like words replaced by ***."""
    text = PEM.sub(MASK, message)
    text = HANDLE.sub("@" + MASK, text)
    text = URL.sub(MASK, text)
    text = SCHEME.sub(lambda m: f"{m[1]} {MASK}", text)
    text = LABELLED.sub(lambda m: f"{m[1]}{m[2]}{MASK}", text)
    words = []
    for word in text.split():
        core = word.strip("'\",.;:()[]{}")
        if word.strip("'\"") in (MASK, "@" + MASK) or core.startswith("{{"):
            words.append(word)
        elif looks_sensitive(core) or SECRETISH_WORD.search(core):
            words.append(MASK)
        else:
            words.append(word)
    return printable(" ".join(words), limit)


def safe_tags(tags: list[str], limit: int = 50) -> list[str]:
    """Datadog tags for the IR: printable, short, no tag that looks like it holds a secret."""
    out: list[str] = []
    for tag in tags[:limit]:
        text = shown(tag, 100)
        if text and not looks_sensitive(text.replace(":", " ")) and not SECRETISH_WORD.search(text):
            out.append(text)
    return out
