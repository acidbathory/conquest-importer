"""The remediation cookbook as one self-contained HTML page, in the look of the migration report.

Two modes. With a remediation plan the page is *relevant only*: it holds just the blockers that
occurred in this run (counts taken from the plan groups), each fully expanded, largest first, and
a decision guide trimmed to what applies. Without a plan it is the full cookbook (every blocker,
collapsed, with a table of contents). No scripts, no external assets, a Content-Security-Policy that
forbids the network. Cookbook text is fixed data, but everything still goes through `e()`; item
names and queries come from the customer's export and are escaped the same way.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from html import escape
from importlib import resources

from importer.core import cookbook
from importer.core.cookbook import Blocker, Example, Method
from importer.core.remediation_plan import Plan
from importer.core.report_brand import BRAND_CSS, logo_strip, pair_name
from importer.core.report_html import CSS
from importer.core.report_html import e as _e
from importer.core.text import printable


def e(text: str, limit: int = 300) -> str:
    """Escape for HTML; line breaks in prose become spaces (control characters are removed)."""
    return _e(" ".join(text.split()), limit)


def rich(text: str, limit: int = 600) -> str:
    """Escape, then turn `backtick` spans into <code>."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text, limit))


def pre(text: str) -> str:
    """Code text for a <pre>: keeps line breaks and indentation, escapes markup."""
    keep = text.replace("\t", "  ").replace("\r", "")
    return escape("\n".join(printable(line, 2000) for line in keep.split("\n"))[:BIG], quote=True)


MAX_ITEMS_SHOWN = 40
BIG = 20000
STATUS_CLASS = {
    "VERIFIED-VALUE": ("sv", "✓✓"),
    "VERIFIED-VALID": ("sw", "✓"),
    "DOC-ONLY": ("sd", "○"),
    "UNVERIFIED": ("su", "?"),
}
STATUS_TEXT = {
    "VERIFIED-VALUE": "a result matched the arithmetic on test data",
    "VERIFIED-VALID": "New Relic accepted the query; no value was compared",
    "DOC-ONLY": "read in documentation; nothing was run",
    "UNVERIFIED": "from memory or inference",
}
EXTRA_CSS = """
:root{--sv:#0a6b0a;--sw:#0a5f8a;--sd:#7a5a00;--su:#a02525}
@media (prefers-color-scheme:dark){:root{--sv:#5fd35f;--sw:#6cc0ee;--sd:#f0c040;--su:#ff8a80}}
html{scroll-behavior:smooth}
nav.toc{border:1px solid var(--rule);padding:12px 16px;margin:32px 0 0;font:14px/1.6 var(--mono)}
nav.toc ol{margin:8px 0 0;padding-left:28px;columns:2;column-gap:32px}
nav.toc li{break-inside:avoid}
@media (min-width:1100px){nav.toc.sticky{position:sticky;top:0;background:var(--paper);z-index:2}}
a{color:var(--accent)}
code{overflow-wrap:anywhere}
.blocker{border-top:1px solid var(--ink);padding:8px 0}
.blocker>summary{font:700 22px/1.3 var(--serif);padding:12px 0}
.blocker>summary .count{margin-left:12px}
.blocker h4{font:600 12px var(--mono);letter-spacing:.1em;text-transform:uppercase;
color:var(--ink2);margin:24px 0 8px}
.blocker ul,.blocker ol{max-width:70ch;padding-left:22px}
.measured{font:13px/1.5 var(--mono);color:var(--ink2);max-width:80ch}
.method{border-left:3px solid var(--rule);padding:4px 0 4px 16px;margin:20px 0}
.method.first{border-left-color:var(--accent)}
.method h5{font:700 18px/1.3 var(--serif);margin:0 0 6px}
.badges{display:flex;flex-wrap:wrap;gap:6px 8px;margin:0 0 8px}
.badge{display:inline-block;font:600 11px/1.4 var(--mono);letter-spacing:.06em;
text-transform:uppercase;padding:1px 7px;border:1px solid var(--ink2);border-radius:2px}
.badge.fam{color:var(--ink2)}
.badge.first{background:var(--accent);border-color:var(--accent);color:#fff}
.badge.sv{border:2px solid var(--sv);color:var(--sv)}
.badge.sw{border:2px dashed var(--sw);color:var(--sw)}
.badge.sd{border:2px dotted var(--sd);color:var(--sd)}
.badge.su{border:2px double var(--su);color:var(--su);text-decoration:underline wavy}
dl.facts{display:grid;grid-template-columns:max-content minmax(0,1fr);gap:4px 16px;
font-size:15px;margin:8px 0;max-width:80ch}
dl.facts dt{font:600 12px/1.8 var(--mono);color:var(--ink2);text-transform:uppercase;
letter-spacing:.06em}
dl.facts dd{margin:0;overflow-wrap:anywhere}
.ex{margin:14px 0}
.ex .label{display:block;margin-bottom:4px;overflow-wrap:anywhere}
.ex,.method,details.blocker{min-width:0}
pre.code{font:13px/1.5 var(--mono);margin:0;padding:12px 14px;overflow-x:auto;
white-space:pre;border:1px solid var(--rule);
background:color-mix(in srgb,var(--ink) 5%,transparent);user-select:all;
-webkit-user-select:all;tab-size:2}
ul.sources{list-style:none;padding:0;margin:0}
ul.sources li{overflow-wrap:anywhere}
ul.sources .num{white-space:normal;display:block;font-size:12px}
.items{font:13px/1.6 var(--mono);list-style:none;padding:0;margin:0;columns:2;column-gap:32px}
.items li{overflow-wrap:anywhere;break-inside:avoid}
.legend2{list-style:none;padding:0;margin:8px 0 0;font-size:15px}
.legend2 li{margin:6px 0}
.guide{max-width:72ch}
.guide li{margin-bottom:8px}
.sum{display:block;overflow-x:auto}
@media (max-width:720px){main{padding:32px 16px 64px}h2{font-size:24px}
nav.toc ol,.items{columns:1}dl.facts{grid-template-columns:minmax(0,1fr);gap:0}
dl.facts dd{margin-bottom:8px}.blocker>summary{font-size:19px}}
@media print{:root{color-scheme:light;--paper:#fff;--ink:#000;--ink2:#333;--rule:#bbb;
--accent:#8a2f10;--sv:#0a6b0a;--sw:#0a5f8a;--sd:#7a5a00;--su:#a02525}
body{background:#fff;color:#000;font-size:11pt}main{max-width:none;padding:0}
nav.toc.sticky{position:static}
pre.code{white-space:pre-wrap;overflow-wrap:anywhere;break-inside:avoid;
background:#f4f4f4;border-color:#999}
.method,.ex,dl.facts{break-inside:avoid}.blocker>summary,h4,h5{break-after:avoid}
a{color:#000;text-decoration:underline}}
"""


@dataclass
class Found:
    """One blocker that occurred in this run, with the plan groups and items behind the count."""

    blocker: Blocker
    count: int = 0
    groups: dict[str, int] = field(default_factory=dict)
    items: list[tuple[str, str]] = field(default_factory=list)  # (kind, name)


def found_in(plan: Plan) -> tuple[list[Found], int]:
    """Blockers hit by the plan's groups (largest first) and the count that has no blocker."""
    hits: dict[str, Found] = {}
    other = 0
    for group in plan.groups:
        for item in group.items:
            b = item.cookbook
            if b is None:
                other += item.count
                continue
            hit = hits.setdefault(b.key, Found(b))
            hit.count += item.count
            hit.groups[group.title] = hit.groups.get(group.title, 0) + item.count
            hit.items.append((item.kind, item.name))
    order = {b.key: n for n, b in enumerate(cookbook.blockers())}
    ranked = sorted(hits.values(), key=lambda h: (-h.count, order[h.blocker.key]))
    return ranked, other


def _inline(text: str) -> str:
    return re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", rich(text, 2000))


def _guide_parts() -> tuple[list[str], list[str]]:
    """The five decision questions and the family sentence, from the shared guide file."""
    raw = (resources.files("importer.core.cookbook") / "data" / "guide.md").read_text("utf-8")
    start = raw.index("## One-page decision guide")
    body = raw[start : raw.index("### Status labels")]
    items: list[str] = []
    for line in body.splitlines():
        if re.match(r"\d+\. ", line):
            items.append(re.sub(r"^\d+\. ", "", line))
        elif items and line.startswith("   "):
            items[-1] += " " + line.strip()
    return items, [body.split("Families:")[1].strip()]


def _guide(alerts: bool) -> str:
    questions, families = _guide_parts()
    keep = [q for n, q in enumerate(questions) if alerts or n not in (0, 3)]
    lis = "".join(f"<li>{_inline(q)}</li>" for q in keep)
    return (
        '<h2><span class="no">01</span>Decision guide</h2>'
        '<p class="intro">Ask these in order. They pick the family of method before you look at a '
        f'single query.</p><ol class="guide">{lis}</ol>'
        f'<p class="intro">Families: {e(families[0], 600)}</p>'
        '<h3>Status labels</h3><ul class="legend2">'
        + "".join(
            f'<li><span class="badge {STATUS_CLASS[s][0]}">{STATUS_CLASS[s][1]} {s}</span> '
            f"{STATUS_TEXT[s]}</li>"
            for s in cookbook.STATUSES
        )
        + "</ul>"
    )


def _code(ex: Example) -> str:
    note = " (condition query)" if ex.kind == "nrql-alert" else ""
    return (
        f'<div class="ex"><span class="label">{e(ex.title, 300)} · '
        f"{e(ex.kind.split('-')[0].upper(), 20)}{note}</span>"
        f'<pre class="code" tabindex="0">{pre(ex.text)}</pre></div>'
    )


def _link(url: str) -> str:
    if not url.startswith("https://"):
        return f"<li>{e(url, 200)}</li>"
    host = url.split("//", 1)[1].split("/", 1)[0]
    return (
        f'<li><a href="{e(url, 500)}" rel="noopener noreferrer">{e(host, 100)}</a> '
        f'<span class="num">{e(url, 500)}</span></li>'
    )


def _method(n: int, m: Method) -> str:
    cls, glyph = STATUS_CLASS.get(m.status, ("su", "?"))
    first = n == 1
    rows = [
        ("When to choose it", m.when),
        ("Prerequisites", m.prerequisites),
        ("Needs collector access", "yes" if m.needs_collector else "no"),
        ("Needs data sent again or differently", "yes" if m.needs_reingest else "no"),
        ("Latency", m.latency),
        ("What changes", m.changes),
        ("Risk", m.risk),
        ("How to verify", m.verify),
        ("Evidence", m.evidence),
    ]
    facts = "".join(f"<dt>{k}</dt><dd>{rich(v, 2000)}</dd>" for k, v in rows)
    sources = "".join(_link(s) for s in m.sources)
    return (
        f'<div class="method{" first" if first else ""}"><h5>{n}. {e(m.name, 200)}</h5>'
        '<div class="badges">'
        + ('<span class="badge first">Recommended first</span>' if first else "")
        + f'<span class="badge fam">Family {e(m.label, 100)}</span>'
        f'<span class="badge {cls}">{glyph} {e(m.status, 30)}</span>'
        f'<span class="badge fam">Effort: {e(m.effort, 60)}</span>'
        f'</div><dl class="facts">{facts}</dl>'
        + "".join(_code(x) for x in m.examples)
        + (f'<h4>Sources</h4><ul class="sources">{sources}</ul>' if sources else "")
        + "</div>"
    )


def _items(hit: Found, plan_path: str) -> str:
    shown = hit.items[:MAX_ITEMS_SHOWN]
    href = e(plan_path, 300)

    def one(kind: str, name: str) -> str:
        label = f"{e(name, 120)}"
        return (
            f'<li>{e(kind, 40)}: <a href="{href}">{label}</a></li>'
            if plan_path
            else (f"<li>{e(kind, 40)}: {label}</li>")
        )

    more = len(hit.items) - len(shown)
    tail = f"<li>and {more} more, all listed in the remediation plan</li>" if more > 0 else ""
    return (
        f'<h4>Items in your run ({hit.count})</h4><ul class="items">'
        + "".join(one(k, n) for k, n in shown)
        + tail
        + "</ul>"
    )


def _blocker(b: Blocker, hit: Found | None, plan_path: str, *, open_: bool) -> str:
    count = f'<span class="count">{hit.count} in your run</span>' if hit else ""
    body = (
        f'<p class="measured">Measured: {rich(b.scope, 1500)}</p><p>{rich(b.summary, 3000)}</p>'
        "<h4>Decision tree</h4><ul>"
        + "".join(f"<li>{rich(d, 1500)}</li>" for d in b.decision)
        + "</ul><h4>What to ask the customer</h4><ul>"
        + "".join(f"<li>{rich(a, 1500)}</li>" for a in b.ask)
        + "</ul>"
        + (f"<h4>What New Relic cannot do</h4><p>{rich(b.cannot, 2000)}</p>" if b.cannot else "")
        + (_items(hit, plan_path) if hit else "")
        + "<h4>Ranked methods</h4>"
        + "".join(_method(n, m) for n, m in enumerate(b.methods, 1))
    )
    return (
        f'<details class="blocker" id="{e(b.key, 80)}"{" open" if open_ else ""}>'
        f"<summary>{e(b.title, 200)}{count}</summary>{body}</details>"
    )


def _summary_table() -> str:
    rows = "".join(
        f'<tr><td><a href="#{e(b.key, 80)}">{e(b.title, 200)}</a></td>'
        f"<td>{e(b.recommended.name, 200)}</td><td>{e(b.recommended.family, 5)}</td>"
        f"<td>{e(b.recommended.status, 30)}</td></tr>"
        for b in cookbook.blockers()
    )
    return (
        '<h3>Recommended first method per blocker</h3><div class="sum"><table><thead><tr>'
        "<th>Blocker</th><th>First method</th><th>Family</th><th>Status</th></tr></thead>"
        f"<tbody>{rows}</tbody></table></div>"
    )


def _toc(blocks: list[Blocker], counts: dict[str, int]) -> str:
    lis = "".join(
        f'<li><a href="#{e(b.key, 80)}">{e(b.title, 200)}</a>'
        + (f" ({counts[b.key]})" if b.key in counts else "")
        + "</li>"
        for b in blocks
    )
    return (
        '<nav class="toc sticky" aria-label="Contents"><span class="label">Contents</span>'
        f"<ol>{lis}</ol></nav>"
    )


def render_cookbook_html(
    plan: Plan | None = None,
    *,
    source: str = "grafana",
    plan_path: str = "",
    run_id: str = "",
    generated: datetime | None = None,
) -> str:
    """The cookbook page. `plan` given: only what this run needs. None: the whole cookbook."""
    stamp = (generated or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    title = f"{pair_name(source)} remediation cookbook"
    where = "<br>".join(x for x in (f"Run {e(run_id, 60)}" if run_id else "", stamp) if x)
    if plan is None:
        blocks = list(cookbook.blockers())
        body = (
            '<p class="intro">Every blocker the importer knows, with ranked methods. Run '
            "<code>importer cookbook --in &lt;converted folder&gt;</code> to see only what your "
            "migration needs.</p>"
            + _toc(blocks, {})
            + _guide(alerts=True)
            + _summary_table()
            + '<h2><span class="no">02</span>Blockers</h2>'
            + "".join(_blocker(b, None, "", open_=False) for b in blocks)
        )
        footer = "This is the full cookbook."
    else:
        hits, other = found_in(plan)
        if not hits:
            body = (
                '<p class="state"><b>Nothing in this run needs a cookbook method.</b> '
                + ("Some items have no cookbook entry; see the remediation plan." if other else "")
                + "</p>"
            )
        else:
            alerts = any("alert" in k for h in hits for k, _ in h.items)
            total = sum(h.count for h in hits)
            body = (
                f'<p class="state"><b>{total} items in your run</b> hit '
                f"{len(hits)} known blocker{'s' if len(hits) != 1 else ''}. Only those are "
                "shown, largest first.</p>"
                + _toc([h.blocker for h in hits], {h.blocker.key: h.count for h in hits})
                + _guide(alerts)
                + '<h2><span class="no">02</span>Found in your migration</h2>'
                + "".join(_blocker(h.blocker, h, plan_path, open_=True) for h in hits)
            )
            if other:
                body += (
                    f'<p class="intro">{other} more items have no cookbook entry; the remediation '
                    "plan lists their steps.</p>"
                )
        footer = (
            "Only the sections your run needs are shown. The full cookbook: "
            "<code>importer cookbook --all</code> or "
            "<code>docs/user/remediation-cookbook.md</code>."
        )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src data:\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{title}</title><style>{CSS}{BRAND_CSS}{EXTRA_CSS}</style></head><body><main>"
        f'<div class="masthead"><h1>{title}</h1><p class="where">{where}</p></div>'
        f"{logo_strip(source)}{body}"
        f"<footer><p>{footer}</p></footer></main></body></html>\n"
    )
