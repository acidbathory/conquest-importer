"""Markdown tiles: they convert to a New Relic markdown widget (research note section 2.9).

Things that need handling, none of which stops the conversion:
- a spacer (an empty tile or a bare rule) is dropped;
- a link into the Dynatrace interface (`/ui/...`, `*.apps.dynatrace.com`, `*.live.dynatrace.com`,
  `*.sprint.dynatracelabs.com` or a `dt-url.net` short link) does not resolve in New Relic and
  must be removed or rewritten;
- an image served from `dt-cdn.net` depends on Dynatrace's content network;
- an unsafe link (a `javascript:`, `data:` or `vbscript:` scheme, also when split by whitespace
  or control characters, or hidden by entity encoding) or raw HTML (`a`, `script`, `img`,
  `iframe` ...) is flagged so it is not carried over.
The flags come from fixed checks. A tile without a flag was not found unsafe by these checks;
that is not a proof that it is safe.
"""

import html
import re
from itertools import islice
from urllib.parse import urlsplit

from importer.sources.dynatrace.dashboards.plan_models import Verdict

SPACER = re.compile(r"^[\s\-_*=]*$")
# link targets: inline `](url)`, reference definitions `[x]: url`, `<scheme:...>` autolinks and
# href or src attributes of raw HTML. The target runs to the closing bracket, so whitespace or
# control characters inside it are kept and removed later.
INLINE = re.compile(r"\]\(\s*<?([^)>]{0,2000})")
REFERENCE = re.compile(r"^[ \t]{0,3}\[[^\]\n]{1,200}\]:[ \t]*<?(\S+)", re.MULTILINE)
AUTOLINK = re.compile(r"<([A-Za-z][A-Za-z0-9+.-]{1,30}:[^>\s]{0,2000})>")
ATTRIBUTE = re.compile(r"""(?:href|src)\s*=\s*["']?([^"'\s>]+)""", re.IGNORECASE)
IMAGE = re.compile(r"!\[[^\]\n]{0,200}\]\(\s*<?([^)\s>]+)")
HTML_TAG = re.compile(
    r"<\s*/?\s*(?:a|script|img|iframe|object|embed|form|link|svg|style|meta|base)\b", re.IGNORECASE
)
INVISIBLE = re.compile(r"[\s\x00-\x1f\x7f]+")
DYNATRACE_LINK_HOSTS = ("dt-url.net",)
DYNATRACE_UI_SUFFIXES = (".apps.dynatrace.com", ".live.dynatrace.com", ".sprint.dynatracelabs.com")
IMAGE_HOSTS = ("dt-cdn.net",)
UNSAFE_SCHEMES = ("javascript:", "data:", "vbscript:")
MAX_LINKS = 500  # links examined per tile


def _targets(content: str) -> list[str]:
    """Every link target in the text, decoded and with whitespace and control characters removed
    (so `java\\tscript:` and `&#106;avascript:` read as `javascript:`)."""
    found: list[str] = []
    for pattern in (INLINE, REFERENCE, AUTOLINK, ATTRIBUTE):
        found += [m.group(1) for m in islice(pattern.finditer(content), MAX_LINKS)]
    return [INVISIBLE.sub("", html.unescape(url)) for url in found[:MAX_LINKS]]


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_dynatrace_link(url: str) -> bool:
    host = _host(url)
    return (
        url.startswith("/ui/")
        or host.endswith(DYNATRACE_UI_SUFFIXES)
        or host in DYNATRACE_LINK_HOSTS
    )


def read_markdown(content: str) -> tuple[Verdict, str, list[str]]:
    """The verdict (always CONVERT), the action ("keep" or "drop") and the flags."""
    if SPACER.match(content):
        return Verdict.CONVERT, "drop", []
    flags: list[str] = []
    links = _targets(content)
    if any(_is_dynatrace_link(u) for u in links):
        flags.append("dynatrace-link")
    if any(u.lower().startswith(UNSAFE_SCHEMES) for u in links):
        flags.append("unsafe-link")
    if HTML_TAG.search(content):
        flags.append("html")
    images = [m.group(1) for m in islice(IMAGE.finditer(content), MAX_LINKS)]
    if any(_host(html.unescape(u)) in IMAGE_HOSTS for u in images):
        flags.append("image")
    return Verdict.CONVERT, "keep", flags
