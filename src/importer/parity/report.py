"""Write the parity results: parity.json, parity.md, parity.html and verified-labels.json.

parity.json has a fixed shape that the scoreboard reads. Everything that came from the source
(names, label values, queries) goes through the escaping functions before it reaches markdown or
HTML.
"""

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

from importer.core.report import md_cell
from importer.core.report_brand import BRAND_CSS, header_lines, logo_strip, pair_name
from importer.core.report_html import CSS, e
from importer.core.text import printable
from importer.parity.model import Item, Verdict

LABELS_FILE = "verified-labels.json"
LABELS_VERSION = 1
LIMITS = [
    "A comparison means something only when the same data reaches both places. If the source "
    "or New Relic has no data for a query, the item is NO DATA and says which side is empty; "
    "that is a finding about the data path, not about the converted query.",
    "Prometheus gives an instant value per step; New Relic gives the average or sum of the "
    "samples in each bucket. Smooth series agree; spiky ones can differ by design.",
    "The first and last bucket of the window are not compared (partial). Points missing on "
    "either side are skipped.",
    "VERIFIED covers the window and variable values that were checked, not every value a "
    "dashboard variable can take, and not future data.",
]
SUMMARY_ORDER = [v.name for v in Verdict]


def item_id(item: Item) -> str:
    return f"{item.pair.owner_key}:{item.pair.title}"


def nrql_digest(nrql: str) -> str:
    return hashlib.sha256(nrql.encode()).hexdigest()[:16]


def summary(items: list[Item]) -> dict[str, int]:
    counts = dict.fromkeys(SUMMARY_ORDER, 0)
    for item in items:
        counts[item.outcome.verdict.name] += 1
    return counts


def reason_of(item: Item) -> str:
    out = item.outcome
    return printable(out.cause or out.note or ("within tolerance" if out.points else ""), 300)


def to_json(items: list[Item], hours: float, step: int, now: datetime) -> str:
    body = {
        "generated": now.isoformat(timespec="seconds"),
        "window_hours": hours,
        "step_seconds": step,
        "items": [
            {
                "id": printable(item_id(i), 300),
                "kind": i.pair.kind,
                "verdict": i.outcome.verdict.name,
                "reason": reason_of(i),
                "points": i.outcome.points,
                "within_tolerance": round(i.outcome.fraction or 0.0, 4),
            }
            for i in items
        ],
        "summary": summary(items),
    }
    return json.dumps(body, indent=2) + "\n"


def labels_json(items: list[Item], now: datetime) -> str:
    """What the converter reports read: one entry per widget whose parity was VERIFIED."""
    verified = [
        {
            "dashboard": i.pair.owner_key,
            "widget": i.pair.title,
            "nrql_sha256": nrql_digest(i.pair.nrql),
            "verified_on": now.date().isoformat(),
        }
        for i in items
        if i.pair.kind == "widget" and i.outcome.verdict is Verdict.VERIFIED
    ]
    return json.dumps({"version": LABELS_VERSION, "verified": verified}, indent=2) + "\n"


def _percent(value: float | None) -> str:
    return "" if value is None else f"{value * 100:.1f}%"


def to_markdown(items: list[Item], hours: float, step: int, tolerance: float, now: datetime) -> str:
    counts = summary(items)
    lines = [
        *header_lines("Parity check", "grafana"),
        "",
        f"Checked {now:%Y-%m-%d %H:%M} UTC over the last {hours:g} hours at {step} s steps, "
        f"tolerance {tolerance * 100:g}% per point.",
        "",
        "| Verdict | Items |",
        "|---|---|",
        *[f"| {name.replace('_', ' ')} | {n} |" for name, n in counts.items()],
        "",
        "## Limits of this check",
        "",
        *[f"- {text}" for text in LIMITS],
        "",
        "## Items",
        "",
        "| Item | Kind | Verdict | Points | Within tolerance | Median error | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for i in items:
        o = i.outcome
        lines.append(
            f"| {md_cell(i.pair.owner, 50)}: {md_cell(i.pair.title, 60)} | {i.pair.kind} | "
            f"{o.verdict.value} | {o.points} | {_percent(o.fraction)} | "
            f"{_percent(o.median_error)} | {md_cell(reason_of(i), 300)} |"
        )
    lines += ["", "## Details of items that are not verified", ""]
    for i in (x for x in items if x.outcome.verdict is not Verdict.VERIFIED):
        lines += [
            f"### {md_cell(i.pair.owner, 60)}: {md_cell(i.pair.title, 80)}",
            "",
            f"- Verdict: **{i.outcome.verdict.value}**. {md_cell(reason_of(i), 300)}",
            f"- Source query: {md_cell(i.original_sent or i.pair.original, 400)}",
            f"- New Relic query: {md_cell(i.nrql_sent or i.pair.nrql, 400)}",
        ]
        lines += [
            f"- Series {md_cell(_label_text(s.labels), 120)}: {s.within} of {s.points} "
            f"points within tolerance, median error {_percent(s.median_error)}"
            for s in i.outcome.series[:10]
        ]
        lines.append("")
    return "\n".join(lines) + "\n"


def _label_text(labels: dict[str, str]) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted(labels.items())) or "(no labels)"


GLYPH = {
    Verdict.VERIFIED: ("ok", "✓"),
    Verdict.CLOSE: ("warn", "≈"),
    Verdict.MISMATCH: ("crit", "&times;"),
    Verdict.NO_DATA: ("serious", "!"),
    Verdict.NOT_COMPARABLE: ("na", "n/a"),
}
PAGE_CSS = (
    "td,th{padding:6px 10px;border-bottom:1px solid var(--rule);text-align:left;"
    "vertical-align:top;font:14px/1.4 var(--serif)}th{font:600 12px/1.4 var(--mono);"
    "letter-spacing:.08em;text-transform:uppercase;color:var(--ink2)}table{border-collapse:"
    "collapse;width:100%}.ok{color:var(--okink)}.warn{color:#8a6100}.crit{color:var(--crit)}"
    ".serious{color:var(--serious)}.na{color:var(--ink2)}li{margin:4px 0}"
)


def to_html(items: list[Item], hours: float, step: int, tolerance: float, now: datetime) -> str:
    counts = summary(items)
    totals = "".join(
        f"<li><b>{n}</b> {e(name.replace('_', ' ').lower())}</li>" for name, n in counts.items()
    )
    rows = []
    for i in items:
        o = i.outcome
        css, glyph = GLYPH[o.verdict]
        rows.append(
            f"<tr><td>{e(i.pair.owner, 60)}: {e(i.pair.title, 80)}</td><td>{e(i.pair.kind)}</td>"
            f'<td class="{css}">{glyph} {e(o.verdict.value)}</td><td>{o.points}</td>'
            f"<td>{e(_percent(o.fraction))}</td><td>{e(_percent(o.median_error))}</td>"
            f"<td>{e(reason_of(i), 300)}</td></tr>"
        )
    limits = "".join(f"<li>{e(text, 400)}</li>" for text in LIMITS)
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src data:\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        f"<title>{pair_name('grafana')}: parity check</title>"
        f"<style>{CSS}{BRAND_CSS}{PAGE_CSS}</style></head><body><main>"
        f'<div class="masthead"><h1>Parity check: {pair_name("grafana")}</h1>'
        f'<p class="where">{now:%Y-%m-%d %H:%M} UTC</p></div>{logo_strip("grafana")}'
        f"<p>Last {hours:g} hours at {step} s steps, tolerance {tolerance * 100:g}% per point.</p>"
        f"<ul>{totals}</ul><h2>Limits of this check</h2><ul>{limits}</ul>"
        "<h2>Items</h2><table><thead><tr><th>Item</th><th>Kind</th><th>Verdict</th>"
        "<th>Points</th><th>Within</th><th>Median error</th><th>Reason</th></tr></thead>"
        f"<tbody>{''.join(rows)}</tbody></table></main></body></html>\n"
    )


def write_all(
    out: Path,
    items: list[Item],
    hours: float,
    step: int,
    tolerance: float,
    now: datetime | None = None,
) -> list[Path]:
    """Write the four files into `out` (created if needed); returns their paths."""
    stamp = now or datetime.now(UTC)
    out.mkdir(parents=True, exist_ok=True)
    files = {
        "parity.json": to_json(items, hours, step, stamp),
        "parity.md": to_markdown(items, hours, step, tolerance, stamp),
        "parity.html": to_html(items, hours, step, tolerance, stamp),
        LABELS_FILE: labels_json(items, stamp),
    }
    for name, text in files.items():
        (out / name).write_text(text, encoding="utf-8")
    return [out / name for name in files]
