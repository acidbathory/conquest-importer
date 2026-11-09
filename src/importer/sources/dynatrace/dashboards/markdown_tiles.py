"""Markdown tiles: they convert to a New Relic markdown widget (research note section 2.9).

Three things need handling and none of them stops the conversion:
- a spacer (an empty tile or a bare rule) is dropped;
- a link into the Dynatrace interface (`/ui/...`, a `*.apps.dynatrace.com` address or a
  `dt-url.net` short link) does not resolve in New Relic and must be removed or rewritten;
- an image served from `dt-cdn.net` depends on Dynatrace's content network.
"""

import re
from urllib.parse import urlsplit

from importer.sources.dynatrace.dashboards.plan_models import Verdict

SPACER = re.compile(r"^[\s\-_*=]*$")
LINK = re.compile(r"\]\(\s*<?([^)\s>]+)")
IMAGE = re.compile(r"!\[[^\]\n]{0,200}\]\(\s*<?([^)\s>]+)")
DYNATRACE_LINK_HOSTS = ("dt-url.net",)
DYNATRACE_UI_SUFFIX = ".apps.dynatrace.com"
IMAGE_HOSTS = ("dt-cdn.net",)
MAX_LINKS = 500  # links examined per tile


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def _is_dynatrace_link(url: str) -> bool:
    host = _host(url)
    return (
        url.startswith("/ui/") or host.endswith(DYNATRACE_UI_SUFFIX) or host in DYNATRACE_LINK_HOSTS
    )


def read_markdown(content: str) -> tuple[Verdict, str, list[str]]:
    """The verdict (always CONVERT), the action ("keep" or "drop") and the flags."""
    if SPACER.match(content):
        return Verdict.CONVERT, "drop", []
    flags: list[str] = []
    links = LINK.findall(content)[:MAX_LINKS]
    if any(_is_dynatrace_link(u) for u in links):
        flags.append("dynatrace-link")
    if any(_host(u) in IMAGE_HOSTS for u in IMAGE.findall(content)[:MAX_LINKS]):
        flags.append("image")
    return Verdict.CONVERT, "keep", flags
