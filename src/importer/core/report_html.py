"""The post-migration report as one self-contained HTML file.

No scripts, no external fonts or images, and a Content-Security-Policy that forbids all network
access, so it can be opened offline and mailed around. Every piece of source-controlled text
(alarm names, reasons, steps built from names) goes through `e()`; markup is built only from
constants and numbers.

Design direction: an editorial audit document, deliberately not a dashboard. Serif text with a
monospaced label face (system fonts only, so it works offline), warm paper and ink, ONE accent
colour used sparingly, structure from spacing and hairline rules instead of boxed cards, an
asymmetric hero. Status colours appear only where they mean something, always with a glyph and a
label. Forbidden: gradients, shadows, radius above 4px, pure black, emoji, card grids.
"""

import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from importer.core.ir import Confidence
from importer.core.migration_summary import KINDS, MAX_REASONS, Asset, Group, Kind, Summary, percent
from importer.core.taxonomy import Category, classify
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
    "other": ("Other monitoring config", "items"),
}


@dataclass(frozen=True)
class Profile:
    """The wording that depends on which cloud the assets came from."""

    account_label: str  # header: "AWS account 1234"
    read_phrase: str  # "Your AWS alarms, dashboards and topics"
    source_phrase: str  # "your AWS account"
    alarm_title: tuple[str, str]  # table heading and unit for the main asset kind
    share_label: str
    unit_sentence: str  # what counts as one asset
    names_word: str  # what the file contains names of
    create_command: Callable[[str, str, str], str]  # (cloud account, nr account, area) -> shell
    checklist: tuple[str, ...]


def _aws_create(account: str, nr: str, area: str) -> str:
    return (
        f"importer migrate --aws-account-id {account} --nr-account-id {nr} --region {area} "
        f"--out out/migration-applied --yes"
    )


def _apply_create(_project: str, nr: str, _area: str) -> str:
    return (
        f"importer apply --in &lt;run folder&gt;/alerts --account-id {nr} "
        f"--apply --confirm-account {nr}"
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
GCP_CHECKLIST = (
    "Link the Google Cloud project to New Relic with Workload Identity Federation, so metrics "
    "are polled every 5 minutes. Converted conditions return nothing until data flows. The "
    "importer does not generate this Terraform yet; <code>scripts/live-test/gcp-link</code> is a "
    "working example.",
    "Compare a few converted queries with the same chart in Cloud Monitoring before you trust "
    "them. Metric names are confirmed on live data for Compute Engine and Pub/Sub only.",
    "Conditions have no notifications yet. Recreate each notification channel as a destination "
    "and attach a workflow.",
    "Review each condition, then enable them one at a time.",
)
DYNATRACE_CHECKLIST = (
    "Forward the logs to New Relic so the converted log counts have data, and check that the "
    "attribute names in each condition (for example loglevel) match the attributes on the "
    "forwarded logs. Nothing has been tried against a Dynatrace tenant that has workflows.",
    "Davis problem workflows are notification routes, not conditions. Recreate each channel "
    "as a destination and attach a workflow filtered on issue priority or tags.",
    "SLOs are not converted yet. Rebuild each as a New Relic service level.",
    "Review each condition, then enable them one at a time.",
)
PROFILES: dict[str, Profile] = {
    "cloudwatch": Profile(
        account_label="AWS account",
        read_phrase="Your AWS alarms, dashboards and topics were read and converted.",
        source_phrase="your AWS account",
        alarm_title=("Alarms", "alarms"),
        share_label="Share of alarms, dashboards, subscriptions and other monitoring config "
        "converted",
        unit_sentence="One alarm, one dashboard, one notification subscription or one piece of "
        "other monitoring config (an EventBridge rule, canary, SLO or similar) is one asset.",
        names_word="alarm",
        create_command=_aws_create,
        checklist=CHECKLIST,
    ),
    "gcp": Profile(
        account_label="Google Cloud project",
        read_phrase="Your Google Cloud alert policies were read and converted.",
        source_phrase="your Google Cloud project",
        alarm_title=("Alert conditions", "conditions"),
        share_label="Share of alert conditions converted",
        unit_sentence="One alert condition is one asset (a policy with two conditions counts "
        "as two). Dashboards, notification channels and uptime checks are not read yet, so "
        "they are not counted.",
        names_word="policy",
        create_command=_apply_create,
        checklist=GCP_CHECKLIST,
    ),
    "dynatrace": Profile(
        account_label="Dynatrace tenant",
        read_phrase="Your Dynatrace workflows and SLOs were read and converted.",
        source_phrase="your Dynatrace tenant",
        alarm_title=("Workflows and SLOs", "workflows and SLOs"),
        share_label="Share of workflows and SLOs converted",
        unit_sentence="One workflow or one SLO is one asset. Classic alerting profiles and "
        "problem notifications are not read, so they are not counted.",
        names_word="workflow",
        create_command=_apply_create,
        checklist=DYNATRACE_CHECKLIST,
    ),
}

MIN_LABEL_SHARE = 10  # a segment narrower than this prints no label (its number is in the table)

GRAIN = (
    "data:image/svg+xml;utf8,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'"
    "%3E%3Cfilter id='n'%3E%3CfeTurbulence type='fractalNoise' baseFrequency='.9' "
    "numOctaves='2' stitchTiles='stitch'/%3E%3C/filter%3E%3Crect width='160' height='160' "
    "filter='url(%23n)' opacity='.045'/%3E%3C/svg%3E"
)

CSS = """
:root{color-scheme:light dark;--paper:#f5f1e8;--ink:#1b1a17;--ink2:#5b574d;--rule:#cfc8b8;
--accent:#b4441f;--ok:#0ca30c;--warn:#fab219;--serious:#ec835a;--crit:#d03b3b;--okink:#0a6b0a;
--serif:Charter,"Bitstream Charter","Iowan Old Style","Palatino Linotype",Palatino,Georgia,serif;
--mono:ui-monospace,"SF Mono",Menlo,Consolas,"Liberation Mono",monospace}
@media (prefers-color-scheme:dark){:root{--paper:#1a1814;--ink:#ece7da;--ink2:#aaa393;
--rule:#3a362d;--accent:#e0714a;--okink:#5fd35f}}
*{box-sizing:border-box}
body{margin:0;background:var(--paper) url("GRAIN");color:var(--ink);font:18px/1.6 var(--serif)}
main{max-width:960px;margin:0 auto;padding:64px 32px 96px}
.label{font:600 12px/1.4 var(--mono);letter-spacing:.12em;text-transform:uppercase;
color:var(--ink2)}
.mono{font-family:var(--mono)}
.masthead{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;
border-bottom:2px solid var(--ink);padding-bottom:12px}
h1{font:400 15px/1.4 var(--mono);letter-spacing:.14em;text-transform:uppercase;margin:0}
.where{font:13px/1.5 var(--mono);color:var(--ink2);margin:0}
.state{margin:24px 0 0;padding-left:16px;border-left:3px solid var(--accent);max-width:68ch}
.state b{font-weight:700}
.state p{margin:6px 0}
.cmd{font-family:var(--mono);font-size:13px;margin:10px 0;padding:10px 12px;overflow-x:auto;
white-space:pre-wrap;word-break:break-word;border:1px solid var(--rule);background:transparent}
.stages{list-style:none;margin:12px 0 0;padding:0;border-top:1px solid var(--rule)}
.stages li{display:grid;grid-template-columns:9ch 1fr;gap:12px;padding:8px 0;
border-bottom:1px solid var(--rule)}
.stages .mark{font-family:var(--mono);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
.hero{display:grid;grid-template-columns:minmax(0,5fr) minmax(0,4fr);gap:48px;
align-items:end;margin:56px 0 24px}
.big{font:400 clamp(96px,17vw,168px)/.85 var(--serif);letter-spacing:-.04em;margin:0}
.lede{font-size:22px;line-height:1.4;margin:0 0 8px}
.lede b{font-weight:700}
@media (max-width:720px){.hero{grid-template-columns:1fr;gap:16px}}
.bar{display:flex;gap:2px;height:28px;margin:24px 0 12px}
.bar.thin{height:6px;margin:12px 0 0}
.seg{display:flex;align-items:center;padding:0 8px;border-radius:2px;font:600 12px var(--mono);
white-space:nowrap;overflow:hidden;color:#1b1a17}
.thin .seg{padding:0;border-radius:0}
.seg.ok{background:var(--ok)}.seg.warn{background:var(--warn)}
.seg.serious{background:var(--serious)}.seg.crit{background:var(--crit);color:#fff}
.legend{display:flex;flex-wrap:wrap;gap:8px 32px;margin:0;padding:0;list-style:none;
font:12px/1.5 var(--mono)}
.legend .m{color:var(--ink2)}
.dot{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:6px}
.dot.ok{background:var(--ok)}.dot.warn{background:var(--warn)}
.dot.serious{background:var(--serious)}.dot.crit{background:var(--crit)}
.ledger{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));margin:56px 0 0;
border-top:1px solid var(--ink)}
.ledger>div{padding:16px 24px 8px 0}
.ledger>div+div{padding-left:24px;border-left:1px solid var(--rule)}
.ledger .n{font-size:48px;line-height:1.1;margin:4px 0}
.ledger .sub{font-size:15px;color:var(--ink2)}
h2{display:flex;gap:16px;align-items:baseline;font:400 28px/1.2 var(--serif);
margin:80px 0 24px;padding-top:12px;border-top:1px solid var(--ink)}
h2 .no{font:600 13px var(--mono);color:var(--accent);letter-spacing:.1em}
.intro{max-width:62ch;color:var(--ink2);margin:0 0 24px}
table{border-collapse:collapse;width:100%;font:14px/1.4 var(--mono);margin:8px 0 0}
caption{text-align:left;padding-bottom:8px}
th,td{text-align:left;padding:10px 16px 10px 0;border-bottom:1px solid var(--rule)}
thead th{border-bottom:1px solid var(--ink);font-weight:600;color:var(--ink2);
font-size:12px;letter-spacing:.06em;text-transform:uppercase}
td.n,th.n{text-align:right;font-variant-numeric:tabular-nums}
tr.total td{border-top:1px solid var(--ink);border-bottom:2px solid var(--ink);font-weight:700}
.problem{display:grid;grid-template-columns:72px minmax(0,1fr);gap:0 16px;
padding:32px 0;border-top:1px solid var(--rule)}
.problem .idx{font:600 13px var(--mono);color:var(--accent);padding-top:8px}
.problem header{display:flex;justify-content:space-between;gap:16px;align-items:baseline}
h3{font:700 22px/1.3 var(--serif);margin:0}
.count{font:12px var(--mono);letter-spacing:.08em;text-transform:uppercase;color:var(--ink2);
white-space:nowrap}
ol.steps{list-style:none;counter-reset:s;margin:12px 0 20px;padding:0;max-width:66ch}
ol.steps>li{counter-increment:s;position:relative;padding-left:36px;margin-bottom:12px}
ol.steps>li::before{content:counter(s);position:absolute;left:0;top:2px;font:600 12px var(--mono);
color:var(--ink2);border:1px solid var(--rule);border-radius:2px;width:22px;height:22px;
text-align:center;line-height:20px}
details{border-top:1px solid var(--rule);padding:10px 0}
summary{cursor:pointer;font:600 14px var(--mono);list-style:none}
summary::-webkit-details-marker{display:none}
summary::before{content:"+";display:inline-block;width:20px;color:var(--accent)}
details[open]>summary::before{content:"\\2212"}
summary:focus-visible{outline:2px solid var(--accent);outline-offset:3px}
.why{font-size:15px;color:var(--ink2);margin:8px 0 0 20px;padding:0;list-style:none}
.why li{margin-bottom:4px}
.also{margin:12px 0 0 20px}
.also .label{display:block;margin-bottom:4px}
code{font:.85em var(--mono);background:color-mix(in srgb,var(--ink) 8%,transparent);
padding:1px 5px;border-radius:2px}
ul.notes{list-style:none;padding:0;margin:0;max-width:70ch}
ul.notes li{padding:12px 0;border-top:1px solid var(--rule)}
.num{font:13px var(--mono);color:var(--ink2);white-space:nowrap}
footer{margin-top:80px;padding-top:16px;border-top:2px solid var(--ink);font-size:15px;
color:var(--ink2);max-width:70ch}
footer p{margin:0 0 8px}
@media print{body{background:#fff;color:#000}details>*{display:block}
.ledger,.hero,.state,table,details,.stages li{break-inside:avoid}
h2,.intro{break-after:avoid}}
@media (prefers-reduced-motion:no-preference){.seg{transition:flex-grow .3s}}
""".replace("GRAIN", GRAIN)


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
        f'<li><span class="dot {cls}" aria-hidden="true"></span><b>{glyph} {label}</b> '
        f'<span class="m">{meaning}</span></li>'
        for _, cls, glyph, label, meaning in STATES
    )
    return f'<ul class="legend">{items}</ul>'


def _kind_counts(summary: Summary, kind: Kind | None) -> dict[Confidence, int]:
    counts = summary.counts(kind)
    return {conf: counts.get(conf, 0) for conf, *_ in STATES}


def _ledger(summary: Summary, names: dict[Kind, tuple[str, str]]) -> str:
    """One ruled column per asset type: no boxes, structure comes from the rules."""
    cols = []
    for kind in KINDS:
        if not summary.of(kind):
            continue
        title, unit = names[kind]
        counts = _kind_counts(summary, kind)
        total = sum(counts.values())
        done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
        cols.append(
            f'<div><div class="label">{title}</div><div class="n">{percent(done, total)}%</div>'
            f'<div class="sub">{done} of {total} {unit} migrated</div>'
            f"{_bar(counts, thin=True, name=title)}</div>"
        )
    return f'<div class="ledger">{"".join(cols)}</div>' if cols else ""


def _table(summary: Summary, names: dict[Kind, tuple[str, str]]) -> str:
    rows = []
    for kind in (*KINDS, None):
        counts = _kind_counts(summary, kind)
        total = sum(counts.values())
        if kind is not None and total == 0:
            continue
        done = counts[Confidence.EXACT] + counts[Confidence.APPROXIMATE]
        name = names[kind][0] if kind else "All assets"
        cells = "".join(f'<td class="n">{counts[c]}</td>' for c, *_ in STATES)
        rows.append(
            f"<tr{'' if kind else ' class="total"'}><td>{name}</td>"
            f'<td class="n">{total}</td>{cells}'
            f'<td class="n">{_share(done, total)}</td>'
            f'<td class="n">{_share(total - done, total)}</td></tr>'
        )
    heads = "".join(f'<th class="n" scope="col">{label}</th>' for _, _, _, label, _ in STATES)
    return (
        '<table><caption class="label">Counts behind the chart</caption>'
        '<thead><tr><th scope="col">Asset type</th>'
        f'<th class="n" scope="col">Total</th>{heads}'
        '<th class="n" scope="col">Migrated</th><th class="n" scope="col">Not migrated</th>'
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table>"
    )


def _asset_row(asset: Asset, extra: list[str]) -> str:
    shown = [r for r in asset.reasons if classify(r) is not Category.UNVERIFIED_NAMES] or (
        asset.reasons
    )
    reasons = "".join(f"<li>{e(r)}</li>" for r in shown[:MAX_REASONS])
    more = len(shown) - MAX_REASONS
    tail = f"<li>and {more} more</li>" if more > 0 else ""
    also = ""
    if extra:
        steps = "".join(f"<li>{rich(s)}</li>" for s in extra)
        also = (
            '<div class="also"><span class="label">Also for this one</span>'
            f'<ol class="steps">{steps}</ol></div>'
        )
    detail = f' <span class="num">{e(asset.detail)}</span>' if asset.detail else ""
    return (
        f"<details{' open' if asset.own_steps else ''}>"
        f"<summary>{e(asset.name, 120)}{detail}</summary>"
        f'<ul class="why"><li class="label">{e(asset.source_type, 60)}</li>{reasons}{tail}</ul>'
        f"{also}</details>"
    )


def _group(group: Group, number: int) -> str:
    steps = "".join(f"<li>{rich(s)}</li>" for s in group.steps)
    n = len(group.items)
    return (
        f'<section class="problem"><div class="idx">{number:02d}</div><div>'
        f"<header><h3>{e(group.title, 100)}</h3>"
        f'<span class="count">{n} asset{"s" if n != 1 else ""}</span></header>'
        f'<ol class="steps">{steps}</ol>'
        + "".join(_asset_row(a, extra) for a, extra in group.items)
        + "</div></section>"
    )


def _stage(mark: str, name: str, text: str) -> str:
    return f'<li><span class="mark">{mark}</span><span><b>{name}</b> {text}</span></li>'


def _state(
    summary: Summary, aws_account: str, nr_account: int | None, region: str, profile: Profile
) -> str:
    """Where the migration stands: what has happened, what has not, and the next command."""
    nr = str(nr_account) if nr_account else "&lt;new relic account id&gt;"
    aws = e(aws_account, 20) if aws_account else "&lt;aws account id&gt;"
    area = e(region, 30) if region else "us-east-1"
    if summary.created is None:
        create = profile.create_command(aws, nr, area)
        stages = (
            _stage(
                "✓ Done",
                "Read and convert.",
                f"{profile.read_phrase} This is the plan.",
            )
            + _stage(
                "○ Not yet",
                "Create in New Relic.",
                "No condition, dashboard or workflow exists in New Relic yet.",
            )
            + _stage(
                "→ Next",
                "Enable and verify.",
                "After step 2, deploy the data stream and enable conditions one at a time.",
            )
        )
        return (
            '<section class="state"><p class="label">Preview · nothing has changed in New Relic'
            "</p>"
            "<p>This report is a <b>forecast</b>. The percentages show what the tool expects to "
            f"create from {profile.source_phrase}, not what exists in New Relic. To create it, "
            "everything is made <b>disabled or private</b> and can be rolled back:</p>"
            f'<pre class="cmd">{create}</pre>'
            f'<ol class="stages">{stages}</ol></section>'
        )
    plural = {"policy": "policies"}
    made = (
        ", ".join(
            f"{n} {kind if n == 1 else plural.get(kind, kind + 's')}"
            for kind, n in sorted(summary.created.items())
        )
        or "nothing"
    )
    existed = ", ".join(
        f"{n} {kind if n == 1 else plural.get(kind, kind + 's')}"
        for kind, n in sorted(summary.existing.items())
    )
    kept = f" {e(existed)} already existed and were left unchanged." if existed else ""
    refused = (
        f" New Relic refused <b>{summary.refused}</b>; those count as not migrated and are "
        "listed below with the reason."
        if summary.refused
        else ""
    )
    undo = (
        f"importer rollback --manifest &lt;run folder&gt;/manifest.json --account-id {nr} "
        f"--apply --confirm-account {nr}"
    )
    stages = (
        _stage("✓ Done", "Read and convert.", "Alarms, dashboards and topics were converted.")
        + _stage("✓ Done", "Create in New Relic.", f"Created {e(made)}.{kept}{refused}")
        + _stage(
            "→ Your turn",
            "Enable and verify.",
            "Nothing is switched on yet; follow the checklist at the end.",
        )
    )
    return (
        '<section class="state"><p class="label">Applied · created in New Relic</p>'
        "<p>The numbers below are what <b>was created</b>. Conditions and workflows are "
        "disabled and dashboards are private. To remove exactly these objects:</p>"
        f'<pre class="cmd">{undo}</pre>'
        f'<ol class="stages">{stages}</ol></section>'
    )


def _ignored(summary: Summary) -> str:
    if not summary.ignored:
        return ""
    items = "; ".join(e(text, 120) for text in summary.ignored)
    return f"<p><b>Not counted.</b> Found and left out on purpose: {items}.</p>"


def _review_notes(summary: Summary, number: int) -> str:
    notes = summary.review_notes()
    if not notes:
        return ""
    items = "".join(
        f'<li>{e(text, 400)} <span class="num">{n} asset{"s" if n != 1 else ""}</span></li>'
        for text, n in notes
    )
    return (
        f'<h2><span class="no">{number:02d}</span>Migrated, but read these first</h2>'
        '<p class="intro">Converted assets carry caveats. These are the most common.</p>'
        f'<ul class="notes">{items}</ul>'
    )


def render_report(
    summary: Summary,
    *,
    aws_account: str = "",
    nr_account: int | None = None,
    region: str = "",
    generated: datetime | None = None,
    source: str = "cloudwatch",
) -> str:
    profile = PROFILES[source]
    names: dict[Kind, tuple[str, str]] = {**KIND_NAMES, "alarm": profile.alarm_title}
    stamp = (generated or datetime.now(UTC)).strftime("%Y-%m-%d %H:%M UTC")
    overall = _kind_counts(summary, None)
    total = sum(overall.values())
    done = overall[Confidence.EXACT] + overall[Confidence.APPROXIMATE]
    where = "<br>".join(
        x
        for x in (
            f"{profile.account_label} {e(aws_account, 20)} · {e(region, 30)}"
            if aws_account
            else e(region, 30),
            f"New Relic account {nr_account}" if nr_account else "",
            stamp,
        )
        if x
    )
    if total == 0:
        hero = (
            '<div class="hero"><p class="big">0</p><p class="lede">No assets were found, so '
            "there was nothing to migrate.</p></div>"
        )
        chart = '<p class="intro">Nothing was found to migrate.</p>'
    else:
        rest = total - done
        forecast = summary.created is None
        word = "can be migrated" if forecast else "migrated"
        hero = (
            '<div class="hero"><div><p class="label">Total completion'
            f"{' · forecast' if forecast else ''}</p>"
            f'<p class="big">{percent(done, total)}%</p></div><div>'
            f'<p class="lede"><b>{done} of {total}</b> assets {word}. '
            f"{rest} did not ({percent(rest, total)}%) and need a person.</p>"
            f'<p class="label">{profile.share_label}</p>'
            "</div></div>"
        )
        chart = _bar(overall, name="All assets") + _legend()
    groups = summary.groups()
    n = 1
    sections = [
        f'<h2><span class="no">{n:02d}</span>What was migrated</h2>{_table(summary, names)}'
    ]
    n += 1
    if groups:
        sections.append(
            f'<h2><span class="no">{n:02d}</span>What was not migrated, and how to fix it</h2>'
            '<p class="intro">Grouped by problem, largest first. The steps are written once per '
            "problem; open an asset to see why it failed.</p>"
            + "".join(_group(g, i) for i, g in enumerate(groups, start=1))
        )
    else:
        sections.append(
            f'<h2><span class="no">{n:02d}</span>What was not migrated</h2>'
            '<p class="intro">Everything found was converted.</p>'
        )
    n += 1
    notes = _review_notes(summary, n)
    if notes:
        sections.append(notes)
        n += 1
    steps = list(profile.checklist)
    if summary.log_groups:
        steps.insert(
            1,
            f"Forward the {summary.log_groups} log group(s) behind your log-based conditions: "
            "deploy <code>logs-ingest/</code> the same way. Those conditions return nothing until "
            "the logs arrive.",
        )
    checklist = "".join(f"<li>{c}</li>" for c in steps)
    sections.append(
        f'<h2><span class="no">{n:02d}</span>Before you enable anything</h2>'
        f'<ol class="steps">{checklist}</ol>'
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; '
        "style-src 'unsafe-inline'; img-src data:\">"
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Migration report</title>"
        f"<style>{CSS}</style></head><body><main>"
        f'<div class="masthead"><h1>Migration report</h1><p class="where">{where}</p></div>'
        f"{_state(summary, aws_account, nr_account, region, profile)}{hero}{chart}"
        f"{_ledger(summary, names)}"
        f"{''.join(sections)}"
        f"<footer><p><b>How this is counted.</b> {profile.unit_sentence} "
        "Migrated means converted exactly or approximately; "
        "manual and unsupported mean not migrated. Percentages are whole numbers and never "
        "round to 0 or 100 unless that is exact.</p>"
        f"{_ignored(summary)}"
        f"<p><b>Private.</b> This file contains {profile.names_word} and resource names. "
        "Review it before sharing outside the team; <code>importer stats</code> output is the "
        "safe summary.</p>"
        "</footer></main></body></html>\n"
    )
