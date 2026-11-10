"""Say where a report came from: the "X to New Relic" pair, as text and as inline SVG.

One place for every report writer. Markdown gets a heading suffix and a text badge line (no remote
images). HTML gets inline SVG marks from the Simple Icons project (CC0, see docs/brand/README.md);
nothing is fetched at view time. All text here is constant, never source-controlled.

The marks are trademarks of their owners, used only to identify the source product. Where no mark
exists (AWS CloudWatch) a neutral text badge is drawn instead of imitating a logo.
"""

from importer.core.brand_marks import MARKS

# source id -> (display name, mark key or None, brand colour used only as a thin accent)
PAIRS: dict[str, tuple[str, str | None, str]] = {
    "cloudwatch": ("AWS CloudWatch", None, "#8c4fff"),
    "gcp": ("Google Cloud Monitoring", "gcp", "#4285f4"),
    "dynatrace": ("Dynatrace", "dynatrace", "#1496ff"),
    "dynatrace-classic": ("Dynatrace", "dynatrace", "#1496ff"),
    "grafana": ("Grafana", "grafana", "#f46800"),
    "datadog": ("Datadog", "datadog", "#632ca6"),
}
TARGET = "New Relic"
TARGET_COLOUR = "#1ce783"
_ARROW = "M3 11h14.2l-4.6-4.6L14 5l7 7-7 7-1.4-1.4 4.6-4.6H3z"


def pair_name(source: str) -> str:
    """'Grafana to New Relic'; just 'New Relic' for a source this module does not know."""
    known = PAIRS.get(source)
    return f"{known[0]} to {TARGET}" if known else TARGET


def badge_line(source: str) -> str:
    """A shields-style text badge for markdown: code spans only, no images."""
    known = PAIRS.get(source)
    if known is None:
        return f"`target: {TARGET}`"
    return f"`source: {known[0]}` → `target: {TARGET}`"


def header_lines(title: str, source: str) -> list[str]:
    """The first lines of a markdown report: heading naming the pair, blank, badge line."""
    if source not in PAIRS:
        return [f"# {title}"]
    return [f"# {title}: {pair_name(source)}", "", badge_line(source)]


def _mark(key: str) -> str:
    return (
        '<svg class="mark-logo" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" '
        f'focusable="false" xmlns="http://www.w3.org/2000/svg"><path fill="currentColor" '
        f'd="{MARKS[key]}"/></svg>'
    )


def _chip(name: str, key: str | None, colour: str) -> str:
    mark = _mark(key) if key else ""
    return (
        f'<span class="chip" style="border-left-color:{colour}">'
        f'{mark}<span class="chip-name">{name}</span></span>'
    )


def logo_strip(source: str) -> str:
    """Source mark, arrow, New Relic mark as inline SVG (or a text badge when no mark exists)."""
    known = PAIRS.get(source)
    if known is None:
        return ""
    name, key, colour = known
    arrow = (
        '<svg class="arrow" viewBox="0 0 24 24" width="28" height="28" aria-hidden="true" '
        'focusable="false" xmlns="http://www.w3.org/2000/svg">'
        f'<path fill="currentColor" d="{_ARROW}"/></svg>'
    )
    return (
        f'<div class="brand" role="img" aria-label="{pair_name(source)}">'
        f"{_chip(name, key, colour)}{arrow}{_chip(TARGET, 'newrelic', TARGET_COLOUR)}</div>"
    )


BRAND_CSS = (
    ".brand{display:flex;align-items:center;gap:16px;flex-wrap:wrap;margin:20px 0 0;"
    "color:var(--ink)}"
    ".chip{display:inline-flex;align-items:center;gap:10px;padding:4px 0 4px 10px;"
    "border-left:4px solid var(--rule)}"
    ".chip-name{font:600 14px/1.2 var(--mono);letter-spacing:.04em}"
    ".mark-logo,.arrow{flex:none;color:var(--ink)}.arrow{color:var(--ink2)}"
)
