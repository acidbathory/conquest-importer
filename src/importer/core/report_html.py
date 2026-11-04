"""The post-migration report as one self-contained HTML file.

No scripts, no external fonts or images, and a Content-Security-Policy that forbids all network
access, so it can be opened offline and mailed around. Every piece of source-controlled text
(alarm names, reasons, steps built from names) goes through `e()`; markup is built only from
constants and numbers. Styling follows the beautiful-ui principles: a visible type scale, a
steady spacing rhythm, colour only where it carries meaning (always with an icon and a label),
restrained cards, reduced motion respected, accessible contrast and a table view of every chart.
"""

import re
from datetime import UTC, datetime
from html import escape

from importer.core.ir import Confidence
from importer.core.migration_summary import KINDS, MAX_REASONS, Asset, Group, Kind, Summary, percent
from importer.core.text import printable

# (confidence, css class, glyph, short label, what it means)
STATES: tuple[tuple[Confidence, str, str, str, str], ...] = (
    (Confidence.EXACT, "ok", "✓", "Migrated", "converted with no caveats"),
    (Confidence.APPROXIMATE, "warn", "≈", "Migrated, review", "converted; read the notes first"),
    (Confidence.MANUAL, "serious", "!", "Manual", "a person must rebuild it"),
    (Confidence.UNSUPPORTED, "crit", "&times;", "Unsupported", "no New Relic equivalent yet"),
)
KIND_NAMES: dict[Kind, tuple[str, str]] = {
    "alarm": ("Alarms", "alarms"),
    "dashboard": ("Dashboards", "dashboards"),
    "notification": ("Notification subscriptions", "subscriptions"),
}
MIN_LABEL_SHARE = 10  # a segment narrower than this prints no label (its number is in the table)

CSS = """
:root{color-scheme:light dark;--bg:#f7f8fa;--surface:#fcfcfb;--ink:#0b0b0b;--ink2:#52514e;
--line:#d9e0e7;--accent:#2a78d6;--ok:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;
--okink:#006300;--s1:4px;--s2:8px;--s3:16px;--s4:24px;--s5:40px;--r:8px}
@media (prefers-color-scheme:dark){:root{--bg:#121211;--surface:#1a1a19;--ink:#f0efec;
--ink2:#c3c2b7;--line:#383835;--accent:#3987e5;--okink:#5fd35f}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 Inter,ui-sans-serif,system-ui,
-apple-system,"Segoe UI",sans-serif}
main{max-width:1040px;margin:0 auto;padding:var(--s5) var(--s4)}
h1{font-size:28px;line-height:1.2;margin:0 0 var(--s1);font-weight:700}
h2{font-size:20px;line-height:1.3;margin:var(--s5) 0 var(--s3);padding-top:var(--s4);
border-top:1px solid var(--line);font-weight:650}
h3{font-size:16px;margin:0;font-weight:650}
p{margin:0 0 var(--s2)}
.muted{color:var(--ink2)}.small{font-size:14px}
.chip{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 12px;
font-size:14px;color:var(--ink2);background:var(--surface)}
.hero{display:flex;align-items:baseline;gap:var(--s3);flex-wrap:wrap;margin:var(--s4) 0 var(--s2)}
.hero .big{font-size:64px;line-height:1;font-weight:700;letter-spacing:-.02em}
.hero .of{font-size:18px;color:var(--ink2)}
.bar{display:flex;gap:2px;height:28px;margin:var(--s2) 0}
.bar.thin{height:8px;border-radius:999px;overflow:hidden}
.seg{display:flex;align-items:center;padding:0 var(--s2);border-radius:4px;font-size:13px;
font-weight:650;white-space:nowrap;overflow:hidden;color:#0b0b0b}
.thin .seg{border-radius:0;padding:0}
.seg.ok{background:var(--ok)}.seg.warn{background:var(--warn)}
.seg.serious{background:var(--serious)}.seg.crit{background:var(--crit);color:#fff}
.legend{display:flex;flex-wrap:wrap;gap:var(--s2) var(--s4);margin:var(--s2) 0 0;padding:0;
list-style:none;font-size:14px}
.dot{display:inline-block;width:12px;height:12px;border-radius:3px;margin-right:6px;
vertical-align:-1px}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.dot.serious{background:var(--serious)}.dot.crit{background:var(--crit)}
.tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:var(--s3);
margin:var(--s4) 0}
.tile{background:var(--surface);border:1px solid var(--line);border-radius:var(--r);
padding:var(--s3)}
.tile .label{font-size:14px;color:var(--ink2)}
.tile .value{font-size:36px;font-weight:700;line-height:1.2}
table{border-collapse:collapse;width:100%;font-size:14px;margin:var(--s2) 0}
th,td{text-align:left;padding:var(--s2) var(--s3) var(--s2) 0;border-bottom:1px solid var(--line);
vertical-align:top}
th{font-weight:650;color:var(--ink2)}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums;padding-right:var(--s3)}
.st{white-space:nowrap;font-weight:600}
.st.ok{color:var(--okink)}
details{border-top:1px solid var(--line);padding:var(--s2) 0}
summary{cursor:pointer;font-weight:600}
summary:focus-visible,a:focus-visible{outline:3px solid var(--accent);outline-offset:2px}
.group{margin:var(--s4) 0;padding-bottom:var(--s2)}
.group header{display:flex;justify-content:space-between;gap:var(--s3);align-items:baseline}
.count{color:var(--ink2);font-size:14px;white-space:nowrap}
ol,ul.plain{margin:var(--s2) 0 var(--s3);padding-left:var(--s4)}
li{margin-bottom:var(--s1)}
.callout{border-left:4px solid var(--accent);background:var(--surface);padding:var(--s2) var(--s3);
border-radius:0 var(--r) var(--r) 0;margin:var(--s3) 0}
.callout.warn{border-color:var(--warn)}
code{font:13px ui-monospace,SFMono-Regular,Menlo,monospace;background:var(--surface);
border:1px solid var(--line);border-radius:4px;padding:1px 4px}
footer{margin-top:var(--s5);padding-top:var(--s3);border-top:1px solid var(--line)}
@media (prefers-reduced-motion:no-preference){.seg{transition:flex-grow .3s}}
@media print{body{background:#fff}details{display:block}details>*{display:block}
.tile{break-inside:avoid}}
"""


def e(text: str, limit: int = 300) -> str:
    """Escape untrusted text for HTML: strip control characters, cap length, escape markup."""
    return escape(printable(text, limit), quote=True)


def rich(text: str, limit: int = 600) -> str:
    """Escape, then turn `backtick` spans into <code>. Escaping first keeps this safe."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text, limit))


def _share(n: int, total: int) -> str:
    p = percent(n, total)
    return "0%" if p is None or n == 0 else f"{p}%"


def _bar(counts: dict[Confidence, int], *, thin: bool = False, name: str = "") -> str:
    total = sum(counts.values())
    if total == 0:
        return ""
    parts, spoken = [], []
    for conf, cls, glyph, label, _ in STATES:
        n = counts.get(conf, 0)
        if n == 0:
            continue
        pct = 100 * n / total
        text = f"{glyph} {round(pct)}%" if pct >= MIN_LABEL_SHARE and not thin else ""
        parts.append(
            f'<span class="seg {cls}" style="flex:{n} 1 0" title="{label}: {n} of {total}">'
            f"{text}</span>"
        )
        spoken.append(f"{label} {n}")
    cls = "bar thin" if thin else "bar"
    return (
        f'<div class="{cls}" role="img" aria-label="{e(name)}: {", ".join(spoken)} of {total}">'
        + "".join(parts)
        + "</div>"
    )


def _legend() -> str:
    items = "".join(
        f'<li><span class="dot {cls}" aria-hidden="true"></span><b>{glyph} {label}</b>'
        f' <span class="muted">{meaning}</span></li>'
        for _, cls, glyph, label, meaning in STATES
    )
    return f'<ul class="legend">{items}</ul>'


def _kind_counts(summary: Summary, kind: Kind | None) -> dict[Confidence, int]:
    counts = summary.counts(kind)
    return {conf: counts.get(conf, 0) for conf, *_ in STATES}


def _tile(summary: Summary, kind: Kind) -> str:
    title, unit = KIND_NAMES[kind]
    counts = _kind_counts(summary, kind)
    total = sum(counts.values())
    done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
    if total == 0:
        value, sub = "none", f"No {unit} found"
    else:
        value, sub = f"{percent(done, total)}%", f"{done} of {total} {unit} migrated"
    return (
        f'<div class="tile"><div class="label">{title}</div><div class="value">{value}</div>'
        f'<div class="small muted">{sub}</div>{_bar(counts, thin=True, name=title)}</div>'
    )


def _table(summary: Summary) -> str:
    rows = []
    for kind in (*KINDS, None):
        counts = _kind_counts(summary, kind)
        total = sum(counts.values())
        if kind is not None and total == 0:
            continue
        done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
        name = KIND_NAMES[kind][0] if kind else "All assets"
        cells = "".join(f'<td class="n">{counts[c]}</td>' for c, *_ in STATES)
        weight = ("<b>", "</b>") if kind is None else ("", "")
        rows.append(
            f'<tr><td>{weight[0]}{name}{weight[1]}</td><td class="n">{total}</td>{cells}'
            f'<td class="n">{_share(done, total)}</td>'
            f'<td class="n">{_share(total - done, total)}</td></tr>'
        )
    heads = "".join(f'<th class="n" scope="col">{label}</th>' for _, _, _, label, _ in STATES)
    return (
        '<table><caption class="muted small" style="text-align:left">Counts behind the '
        'charts (table view)</caption><thead><tr><th scope="col">Asset type</th>'
        f'<th class="n" scope="col">Total</th>{heads}'
        '<th class="n" scope="col">Migrated %</th><th class="n" scope="col">Not migrated %</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _asset_row(asset: Asset, extra: list[str]) -> str:
    reasons = "".join(f"<li>{e(r)}</li>" for r in asset.reasons[:MAX_REASONS])
    more = len(asset.reasons) - MAX_REASONS
    tail = f'<li class="muted">and {more} more</li>' if more > 0 else ""
    also = ""
    if extra:
        steps = "".join(f"<li>{rich(s)}</li>" for s in extra)
        also = f'<p class="small"><b>Also for this one</b></p><ol>{steps}</ol>'
    detail = f' <span class="muted">({e(asset.detail)})</span>' if asset.detail else ""
    return (
        f"<details{' open' if extra else ''}><summary>{e(asset.name, 120)}{detail}</summary>"
        f'<p class="small muted">{e(asset.source_type, 60)}</p>'
        f'<ul class="plain small">{reasons}{tail}</ul>{also}</details>'
    )


def _group(group: Group) -> str:
    steps = "".join(f"<li>{rich(s)}</li>" for s in group.steps)
    n = len(group.items)
    return (
        f'<section class="group"><header><h3>{e(group.title, 100)}</h3>'
        f'<span class="count">{n} asset{"s" if n != 1 else ""}</span></header>'
        f'<p class="small muted">Manual steps</p><ol>{steps}</ol>'
        + "".join(_asset_row(a, extra) for a, extra in group.items)
        + "</section>"
    )


def _created(summary: Summary) -> str:
    if summary.created is None:
        return (
            '<div class="callout warn"><b>Plan only.</b> Nothing has been created in New Relic. '
            '"Migrated" below means converted and ready to create.</div>'
        )
    made = ", ".join(f"{n} {kind}" for kind, n in sorted(summary.created.items())) or "nothing"
    return (
        f'<div class="callout"><b>Created in New Relic:</b> {e(made)}. Conditions and workflows '
        "were created disabled and dashboards private; "
        "<code>importer rollback</code> removes exactly these.</div>"
    )


def _review_notes(summary: Summary) -> str:
    notes = summary.review_notes()
    if not notes:
        return ""
    items = "".join(
        f'<li>{e(text, 400)} <span class="muted">({n} asset{"s" if n != 1 else ""})</span></li>'
        for text, n in notes
    )
    return (
        "<h2>Migrated, but read these first</h2>"
        '<p class="muted">Converted assets carry caveats. These are the most common.</p>'
        f'<ul class="plain">{items}</ul>'
    )


CHECKLIST = (
    "Deploy the data-ingestion Terraform (<code>ingest/</code>) so metrics reach New Relic; "
    "converted conditions return nothing until data flows.",
    "Run <code>importer validate</code> after ingestion starts, to compare CloudWatch and New "
    "Relic values side by side.",
    "Review each condition and workflow, then enable them one at a time.",
    "Avoid double counting: do not run New Relic's AWS polling integration and Metric Streams "
    "for the same metrics.",
)


def render_report(
    summary: Summary,
    *,
    aws_account: str = "",
    nr_account: int | None = None,
    region: str = "",
    generated: datetime | None = None,
) -> str:
    stamp = (generated or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    overall = _kind_counts(summary, None)
    total = sum(overall.values())
    done = overall[Confidence.EXACT] + overall[Confidence.APPROXIMATE]
    where = " · ".join(
        x
        for x in (
            f"AWS account {e(aws_account, 20)}" if aws_account else "",
            e(region, 30),
            f"New Relic account {nr_account}" if nr_account else "",
            stamp,
        )
        if x
    )
    if total == 0:
        hero = '<p class="hero"><span class="big">0</span><span class="of">assets found</span></p>'
        body = '<p class="muted">Nothing was found to migrate.</p>'
    else:
        hero = (
            f'<p class="hero"><span class="big">{percent(done, total)}%</span>'
            f'<span class="of">migrated · {done} of {total} assets · '
            f"{percent(total - done, total)}% not migrated ({total - done})</span></p>"
        )
        body = _bar(overall, name="All assets") + _legend()
    tiles = "".join(_tile(summary, k) for k in KINDS if summary.of(k))
    groups = summary.groups()
    if groups:
        remediation = (
            "<h2>What was not migrated, and how to fix it</h2>"
            '<p class="muted">Grouped by problem, largest first. The manual steps are written '
            "once per group; open an asset to see why it failed.</p>"
            + "".join(_group(g) for g in groups)
        )
    else:
        remediation = "<h2>What was not migrated</h2><p>Everything found was converted.</p>"
    checklist = "".join(f"<li>{c}</li>" for c in CHECKLIST)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Migration report</title>"
        f"<style>{CSS}</style></head><body><main>"
        f'<h1>Migration report</h1><p class="muted">{where}</p>'
        f"{_created(summary)}{hero}{body}"
        f'<div class="tiles">{tiles}</div>'
        f"<h2>What was migrated</h2>{_table(summary)}"
        f"{remediation}{_review_notes(summary)}"
        f"<h2>Before you enable anything</h2><ol>{checklist}</ol>"
        '<footer class="small muted"><p><b>How this is counted.</b> One alarm, one dashboard, or '
        "one notification subscription is one asset. Migrated = converted exactly or "
        "approximately; manual and unsupported = not migrated. Percentages are whole numbers and "
        "never round to 0 or 100 unless that is exact.</p>"
        "<p><b>Private.</b> This file contains alarm and resource names. Review it before "
        "sharing outside the team; <code>importer stats</code> output is the safe summary.</p>"
        "</footer></main></body></html>\n"
    )
