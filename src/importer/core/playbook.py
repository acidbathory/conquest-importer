"""Playbooks: the full way to finish by hand what the importer could not convert.

A remediation rule (`remediation.py`) says what kind of problem a reason is and gives a few
generic steps. A playbook adds what a person needs to actually finish the job: what the item
does in the source tool, the closest New Relic construct, concrete steps, a ready-to-edit query
or configuration template, what will behave differently, the effort, and public documentation.

A playbook is keyed by the title of a remediation rule, so the rule engine stays the single place
that decides which kind of problem a reason is. A playbook may also be keyed `title|variant` for
one variant of a problem (a contact point type, a panel type). Nothing here is carried out by the
tool and nothing here reads untrusted text: the template is filled only from names that pass a
strict pattern (see `fill_template`).
"""

import re
from dataclasses import dataclass, field

# Documentation links are checked by hand against the public site; `verified` says the page was
# fetched and the claim next to it was read in it. A link with verified=False is a lead.
CHECKED_ON = "2026-11-10"

SAFE_NAME = re.compile(r"^[A-Za-z_:][A-Za-z0-9_:]{0,199}$")
TOKEN = re.compile(r"<(metric|labels|label)>")


@dataclass(frozen=True)
class Link:
    url: str
    note: str
    verified: bool = True


@dataclass(frozen=True)
class Playbook:
    """How to finish one kind of unconverted item. All text is fixed; none comes from an export."""

    source: str  # what the item does in the source tool
    newrelic: str  # the closest New Relic construct, or "none" and the best workaround
    steps: tuple[str, ...]
    differs: str  # what will behave differently
    effort_minutes: int  # per item, a rough planning figure
    links: tuple[Link, ...] = ()
    template: str = ""  # NRQL or configuration with <metric>, <labels> tokens
    no_equivalent: bool = False  # New Relic has no equivalent; `newrelic` names the workaround
    measured: str = ""  # numbers measured on real data, when we have them
    titles: tuple[str, ...] = field(default=(), compare=False)

    @property
    def effort(self) -> str:
        return effort_label(self.effort_minutes)


def effort_label(minutes: int) -> str:
    if minutes < 60:
        return f"{minutes} minutes"
    hours = minutes / 60
    return f"{hours:g} hour" + ("" if hours == 1 else "s")


_BOOK: dict[str, Playbook] = {}


def register_playbook(titles: tuple[str, ...] | str, playbook: Playbook) -> None:
    """Attach one playbook to one or more remediation rule titles (or `title|variant` keys)."""
    for title in (titles,) if isinstance(titles, str) else titles:
        if title in _BOOK:
            raise ValueError(f"two playbooks for {title!r}")
        _BOOK[title] = playbook


def playbook_for(title: str, variant: str = "") -> Playbook | None:
    """The playbook for a rule title; a variant-specific one wins when there is one."""
    _load()
    if variant and f"{title}|{variant}" in _BOOK:
        return _BOOK[f"{title}|{variant}"]
    return _BOOK.get(title)


def all_playbooks() -> dict[str, Playbook]:
    _load()
    return dict(_BOOK)


def fill_template(
    template: str, metric: str = "", labels: tuple[str, ...] = (), label: str = ""
) -> str:
    """Put the item's own names into a template; a name that is not safe stays a token.

    `<label>` is the label a variable filters on when the item has one, else the first grouping
    label; `<labels>` is the list of grouping labels.
    """

    def pick(match: re.Match[str]) -> str:
        if match.group(1) == "metric":
            return metric if SAFE_NAME.match(metric) else match.group(0)
        good = [x for x in labels if SAFE_NAME.match(x)]
        if match.group(1) == "label":
            one = label if SAFE_NAME.match(label) else ""
            pick_one = one or (good[0] if good else "")
            return pick_one or match.group(0)
        return ", ".join(good) if good else match.group(0)

    return TOKEN.sub(pick, template)


_loaded = False


def _load() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        import importer.core.playbooks  # noqa: F401  (registers every playbook)
