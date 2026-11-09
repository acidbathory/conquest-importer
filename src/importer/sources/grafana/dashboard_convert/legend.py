"""`legendFormat` to a New Relic series name (research note, section 5.5).

Static text becomes an `AS` alias; exactly one `{{label}}` means the facet value is the legend, so
the label is faceted and nothing else is written; text mixed with labels keeps the facet and
loses the text. A dashboard variable inside a legend is dropped because `AS` takes none.
"""

import re
from dataclasses import dataclass

LABEL = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
VARIABLE = re.compile(r"\$\{\w+(?::[^}]*)?\}|\$\w+|\[\[\w+(?::\w+)?\]\]")
MAX_LABELS = 5


@dataclass(frozen=True)
class Legend:
    alias: str | None  # static text for AS
    labels: tuple[str, ...]  # labels to facet by
    lost_text: bool  # text around labels that has no New Relic form


def read_legend(text: str) -> Legend:
    """What a Grafana legend format asks for."""
    cleaned = VARIABLE.sub("", text).strip()
    if not cleaned:
        return Legend(None, (), False)
    labels = tuple(dict.fromkeys(LABEL.findall(cleaned)))[:MAX_LABELS]
    if "{{" not in cleaned:
        return Legend(" ".join(cleaned.split()), (), False)
    rest = LABEL.sub("", cleaned).strip()
    if "{{" in rest or "}}" in rest:  # a malformed placeholder is text
        return Legend(None, labels, True)
    return Legend(None, labels, bool(rest))
