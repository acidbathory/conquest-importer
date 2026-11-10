"""Render the cookbook data as the user guide `docs/user/remediation-cookbook.md`."""

from importlib import resources

from importer.core import cookbook
from importer.core.cookbook import Blocker, Example, Method

GUIDE_PATH = "docs/user/remediation-cookbook.md"
FENCE = {
    "nrql": "nrql", "nrql-full": "nrql", "nrql-alert": "nrql", "promql": "promql", "yaml": "yaml",
    "hcl": "hcl", "graphql": "graphql", "json": "json", "shell": "shell", "text": "text",
}  # fmt: skip


def anchor(heading: str) -> str:
    """The GitHub-style anchor of a heading (matches tests/test_docs_user_links.py)."""
    import re

    text = re.sub(r"[`*_]", "", heading.strip().lower())
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


def _example(e: Example) -> list[str]:
    note = " (condition query)" if e.kind == "nrql-alert" else ""
    return [f"*{e.title}*{note}", "", f"```{FENCE[e.kind]}", e.text, "```", ""]


def _method(n: int, m: Method) -> list[str]:
    sources = ", ".join(f"[{s.split('//')[1].split('/')[0]}]({s})" for s in m.sources)
    lines = [
        f"#### Method {n}: {m.name}",
        "",
        f"**Family:** {m.label}. **Status:** {m.status}. **Effort:** {m.effort}.",
        "",
        f"- **When to choose it:** {m.when}",
        f"- **Prerequisites:** {m.prerequisites}",
        f"- **Needs collector access:** {'yes' if m.needs_collector else 'no'}. "
        f"**Needs data re-ingested or sent differently:** {'yes' if m.needs_reingest else 'no'}. "
        f"**Latency:** {m.latency}",
        f"- **What changes (numbers, delay, cost):** {m.changes}",
        f"- **Risk:** {m.risk}",
        f"- **How to verify:** {m.verify}",
        f"- **Evidence:** {m.evidence}",
        f"- **Sources:** {sources}",
        "",
    ]
    for e in m.examples:
        lines += _example(e)
    return lines


def _blocker(b: Blocker) -> list[str]:
    lines = [f"### {b.title}", "", f"**Measured:** {b.scope}", "", b.summary, "", "**Decision tree**", ""]
    lines += [f"- {d}" for d in b.decision]
    lines += ["", "**What to ask the customer**", ""] + [f"- {a}" for a in b.ask]
    if b.cannot:
        lines += ["", f"**What New Relic cannot do:** {b.cannot}"]
    lines += ["", "**Ranked methods**", ""]
    for n, m in enumerate(b.methods, 1):
        lines += _method(n, m)
    return lines


def summary_table() -> str:
    rows = ["| Blocker | First method | Family | Status | Alternatives |", "|---|---|---|---|---|"]
    for b in cookbook.blockers():
        first = b.recommended
        others = ", ".join(m.name for m in b.methods[1:])
        link = f"[{b.title}](#{anchor(b.title)})"
        rows.append(f"| {link} | {first.name} | {first.family} | {first.status} | {others} |")
    return "\n".join(rows)


def render_markdown() -> str:
    guide = (resources.files("importer.core.cookbook") / "data" / "guide.md").read_text("utf-8")
    toc = "\n".join(
        f"{n}. [{b.title}](#{anchor(b.title)}) ({len(b.methods)} methods)"
        for n, b in enumerate(cookbook.blockers(), 1)
    )
    text = guide.replace("{{SUMMARY_TABLE}}", summary_table()).replace("{{TOC}}", toc)
    body: list[str] = ["", "## Blockers", ""]
    for b in cookbook.blockers():
        body += _blocker(b)
    return text.rstrip("\n") + "\n" + "\n".join(body).rstrip("\n") + "\n"
