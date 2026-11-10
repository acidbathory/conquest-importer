"""Read converted output and plan the manual work: one row per item that did not convert.

Input is the output folder of `convert` (alert monitors in `ir/`), of `convert-grafana-dashboards`
(`dashboard-ir/`) or of `convert-grafana-notifications` (`report.md`; that command writes no IR).
Each item is matched to a remediation rule by its reasons, and the rule's title finds its
playbook. Nothing is sent anywhere. Names in the output come from the customer's export, so they
only reach the plan through `md_cell` (markdown) or `csv_cell` (CSV).
"""

import csv
import io
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.core.ir import Confidence, IRMonitor
from importer.core.playbook import CHECKED_ON, Playbook, fill_template, playbook_for
from importer.core.remediation import Remedy, remedies_for
from importer.core.report import md_cell
from importer.core.text import printable

MAX_FILES = 5000
MAX_FILE_BYTES = 20_000_000
MAX_REPORT_LINES = 50_000
# Dashboard-level lines that count or describe what happened; they are not items to fix.
SUMMARY = re.compile(
    r"^\d+ of \d+ panels converted|^data must reach|^original time range|"
    r"^\d+ collapsed rows|^\d+ variables were not converted|^\d+ variables use another|"
    r"^\d+ variables that others depend|^\d+ query_result\(\) variables|"
    r"^\d+ constant variables|^the file could not be read"
)
COUNTED = re.compile(r"^(\d+) ")
PANEL_TYPE = re.compile(r"^dashboard panel type ([a-z-]+) ")
DATA_SOURCE = re.compile(r"^dashboard data source ([a-z ]+?) is not supported")
PROMQL_WORDS = frozenset(
    {
        "by",
        "without",
        "on",
        "ignoring",
        "group_left",
        "group_right",
        "bool",
        "offset",
        "and",
        "or",
        "unless",
        "sum",
        "avg",
        "min",
        "max",
        "count",
        "rate",
        "irate",
        "increase",
        "histogram_quantile",
        "topk",
        "bottomk",
        "stddev",
        "stdvar",
        "quantile",
    }
)


@dataclass(eq=False)
class Item:
    source: str
    kind: str  # "alert rule", "dashboard panel", "contact point", ...
    container: str  # the dashboard an item sits in; empty for alerts
    name: str
    reasons: tuple[str, ...]
    count: int = 1  # how many things this item stands for ("4 variables were dropped")
    variant: str = ""
    query: str = ""
    extra_steps: tuple[str, ...] = ()
    remedies: list[Remedy] = field(default_factory=list)

    @property
    def title(self) -> str:
        return self.remedies[0].title if self.remedies else ""

    @property
    def playbook(self) -> Playbook | None:
        return playbook_for(self.title, self.variant) if self.title else None

    @property
    def minutes(self) -> int:
        pb = self.playbook
        return (pb.effort_minutes if pb else 30) * self.count


def promql_names(query: str) -> tuple[str, tuple[str, ...], str]:
    """The first metric name, the grouping labels and the first label a variable filters.

    Each is empty when the text does not give it. Names are checked again by `fill_template`
    before they reach any output.
    """
    text = re.sub(r'"(?:[^"\\]|\\.)*"', '""', query)
    labels: list[str] = []
    for group in re.finditer(r"\b(?:by|without)\s*\(([^)]*)\)", text):
        labels += [x.strip() for x in group.group(1).split(",") if x.strip()]
    variable_label = ""
    for matcher in re.finditer(r'([A-Za-z_]\w*)\s*(?:=~|!~|!=|=)\s*"[^"]*\$', query):
        variable_label = variable_label or matcher.group(1)
    text = re.sub(r"\{[^}]*\}", "", text)
    text = re.sub(r"\b(?:by|without|on|ignoring|group_left|group_right)\s*\([^)]*\)", " ", text)
    text = re.sub(r"\$\{?\w+\}?|\[[^\]]*\]", " ", text)
    unique = tuple(dict.fromkeys(labels))
    for name in re.finditer(r"[A-Za-z_:][A-Za-z0-9_:]*(?!\w)", text):
        word = name.group(0)
        rest = text[name.end() :].lstrip()
        if word not in PROMQL_WORDS and not rest.startswith("(") and not word.startswith("__"):
            return word, unique, variable_label
    return "", unique, variable_label


def _variant(reason: str) -> str:
    for pattern in (PANEL_TYPE, DATA_SOURCE):
        found = pattern.match(reason)
        if found:
            return found.group(1).strip()
    return ""


def _items_from_monitors(folder: Path) -> list[Item]:
    items: list[Item] = []
    for path in sorted((folder / "ir").glob("*.json"))[:MAX_FILES]:
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        monitor = IRMonitor.model_validate_json(path.read_text())
        if monitor.confidence not in (Confidence.MANUAL, Confidence.UNSUPPORTED):
            continue
        reasons = tuple(monitor.reasons)
        items.append(
            Item(
                monitor.source_system,
                monitor.source_type,
                "",
                monitor.name,
                reasons,
                variant=next((v for r in reasons if (v := _variant(r))), ""),
                query=monitor.signal.raw_query or "",
                extra_steps=tuple(monitor.remediation),
            )
        )
    return items


def _dashboard_items(dash: IRDashboard) -> list[Item]:
    items: list[Item] = []
    for w in dash.widgets:
        if w.kind != WidgetKind.UNSUPPORTED:
            continue
        reasons = tuple(r for r in w.reasons if not r.startswith("query:"))
        query = next((r[len("query: ") :] for r in w.reasons if r.startswith("query:")), "")
        variant = next((v for r in reasons if (v := _variant(r))), "")
        items.append(
            Item(
                dash.source_system,
                "dashboard panel",
                dash.name,
                w.title,
                reasons,
                1,
                variant,
                query,
            )
        )
    for reason in dash.reasons:
        if SUMMARY.match(reason) or not remedies_for([reason]):
            continue
        counted = COUNTED.match(reason)
        kind = "dashboard variable" if "variable" in reason else "dashboard feature"
        items.append(
            Item(
                dash.source_system,
                kind,
                dash.name,
                kind,
                (reason,),
                int(counted.group(1)) if counted else 1,
            )
        )
    return items


def _items_from_dashboards(folder: Path) -> list[Item]:
    items: list[Item] = []
    for path in sorted((folder / "dashboard-ir").glob("*.json"))[:MAX_FILES]:
        if path.stat().st_size > MAX_FILE_BYTES:
            continue
        items += _dashboard_items(IRDashboard.model_validate_json(path.read_text()))
    return items


def _rows(lines: list[str], header: str) -> list[list[str]]:
    """The body rows of the markdown table that follows the `## header` line."""
    out: list[list[str]] = []
    try:
        start = lines.index(header)
    except ValueError:
        return out
    for line in lines[start + 1 :]:
        if line.startswith("#"):
            break
        if line.startswith("|") and not line.startswith(("|---", "| Contact", "| Mute", "| Route")):
            out.append([c.strip() for c in line.strip().strip("|").split(" | ")])
    return out


def _items_from_notifications(folder: Path) -> list[Item]:
    path = folder / "report.md"
    if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
        return []
    lines = path.read_text().splitlines()[:MAX_REPORT_LINES]
    if not lines or lines[0] != "# Grafana notification conversion":
        return []
    from importer.sources.grafana.notifications import reasons as R

    items: list[Item] = []
    for row in _rows(lines, "## Contact points"):
        if len(row) < 4:
            continue
        name, ctype, verdict, result = row[:4]
        if verdict == "NO EQUIVALENT":
            reason = R.R_CP_NONE
        elif verdict == "MANUAL STEP" or result.startswith("manual"):
            reason = R.R_CP_MANUAL
        else:
            continue
        if ctype.lower() == "email" and result.startswith("manual"):
            reason = "grafana converted email destination needs an address"
        items.append(Item("grafana", "contact point", "", name, (reason,), variant=ctype.lower()))
    for row in _rows(lines, "## Mute timings"):
        if len(row) >= 3 and row[2]:
            reasons = tuple(x.strip() for x in row[2].split(";") if x.strip())
            items.append(Item("grafana", "mute timing", "", row[0], reasons))
    unrouted = re.compile(r"^- (.*): (grafana notification policy .*)$")
    route = re.compile(r"^- route \d+ \((.*)\): (grafana .*)$")
    seen: dict[str, list[str]] = defaultdict(list)
    for line in lines:
        if found := unrouted.match(line):
            seen[found.group(1)].append(found.group(2))
        elif found := route.match(line):
            items.append(
                Item("grafana", "notification route", "", found.group(1), (found.group(2),))
            )
    items += [Item("grafana", "alert rule routing", n, n, tuple(r)) for n, r in seen.items()]
    return items


def read_items(folder: Path) -> list[Item]:
    """Items from a converted folder, or from each folder directly below it."""
    folders = [folder, *sorted(p for p in folder.iterdir() if p.is_dir())]
    items: list[Item] = []
    for one in folders:
        items += _items_from_monitors(one)
        items += _items_from_dashboards(one)
        items += _items_from_notifications(one)
    return items


@dataclass
class Group:
    title: str
    playbook: Playbook | None
    items: list[Item]
    alone: int = 0  # items this reason is the only blocker of

    @property
    def count(self) -> int:
        return sum(i.count for i in self.items)

    @property
    def minutes(self) -> int:
        return sum(i.minutes for i in self.items)


@dataclass(frozen=True)
class TopFix:
    title: str
    unlocks: int  # items that are fully done once this fix and the ones before it are done
    affects: int
    minutes: int


@dataclass
class Plan:
    items: list[Item]
    groups: list[Group]
    unmatched: list[Item]

    def top_fixes(self, n: int = 5) -> list[TopFix]:
        """Greedy cover: the fix that finishes the most items, then the next given the first."""
        titles = {i: {r.title for r in i.remedies if not r.secondary} for i in self.items}
        done: set[str] = set()
        picked: list[TopFix] = []
        remaining = [i for i in self.items if titles[i]]
        pool = {t for ts in titles.values() for t in ts}
        for _ in range(n):
            best: tuple[int, int, str] | None = None
            for title in sorted(pool - done):
                gain = sum(i.count for i in remaining if titles[i] <= done | {title})
                hit = sum(i.count for i in remaining if title in titles[i])
                if gain > 0 and (best is None or (gain, hit) > (best[0], best[1])):
                    best = (gain, hit, title)
            if best is None:
                break
            gain, hit, title = best
            done.add(title)
            minutes = next(g.minutes for g in self.groups if g.title == title)
            picked.append(TopFix(title, gain, hit, minutes))
            remaining = [i for i in remaining if not titles[i] <= done]
        return picked


def build_plan(items: list[Item]) -> Plan:
    unmatched: list[Item] = []
    for item in items:
        item.remedies = remedies_for(list(item.reasons))
        if not [r for r in item.remedies if not r.secondary]:
            unmatched.append(item)
        else:
            item.remedies = [r for r in item.remedies if not r.secondary] + [
                r for r in item.remedies if r.secondary
            ]
    by_title: dict[str, list[Item]] = defaultdict(list)
    for item in items:
        if item.title:
            by_title[item.title].append(item)
    groups = []
    for title, members in by_title.items():
        alone = sum(i.count for i in members if len({r.title for r in i.remedies}) == 1)
        groups.append(Group(title, playbook_for(title), members, alone))
    groups.sort(key=lambda g: (-g.count, g.title))
    return Plan(items, groups, unmatched)


def csv_cell(text: str) -> str:
    """Text that a spreadsheet will not run as a formula."""
    text = printable(" ".join(text.split()), 2000)
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


def _steps_of(item: Item) -> list[str]:
    pb = item.playbook
    steps = list(pb.steps) if pb else [s for r in item.remedies[:1] for s in r.steps]
    return [*item.extra_steps, *steps]


def _filled(item: Item) -> str:
    pb = item.playbook
    if not pb or not pb.template:
        return ""
    metric, labels, label = promql_names(item.query)
    return fill_template(pb.template, metric, labels, label)


def render_csv(plan: Plan) -> str:
    buf = io.StringIO()
    out = csv.writer(buf, lineterminator="\n")
    out.writerow(
        ["source", "kind", "container", "item", "count", "reason", "also_blocked_by",
         "effort", "effort_minutes", "steps", "template", "done"]
    )  # fmt: skip
    for g in plan.groups:
        for i in g.items:
            pb = i.playbook
            out.writerow(
                [csv_cell(x) for x in (
                    i.source, i.kind, i.container, i.name, str(i.count), g.title,
                    "; ".join(r.title for r in i.remedies[1:]),
                    pb.effort if pb else "not estimated", str(i.minutes),
                    " | ".join(f"{n}. {s}" for n, s in enumerate(_steps_of(i), 1)),
                    _filled(i), "",
                )]
            )  # fmt: skip
    return buf.getvalue()


def _link_line(pb: Playbook) -> list[str]:
    return [
        f"  - {k.note}: {k.url} ({'checked ' + CHECKED_ON if k.verified else 'UNVERIFIED'})"
        for k in pb.links
    ]


def render_markdown(plan: Plan) -> str:
    total = sum(i.count for i in plan.items)
    minutes = sum(i.minutes for i in plan.items)
    covered = sum(i.count for i in plan.items if i.playbook)
    lines = [
        "# Remediation plan",
        "",
        f"Items that did not convert: **{total}** in **{len(plan.groups)}** groups. "
        f"Items with a full playbook: **{covered}** of {total}. "
        f"Rough effort if every item is done on its own: about **{minutes / 60:.0f} hours** "
        "(a planning figure, not a measurement; items that share a fix cost less together).",
        "",
        "## Top 5 fixes that unlock the most items",
        "",
        "Each line counts the items that are completely done after that fix and the ones above it.",
        "",
        "| # | Fix | Items unlocked | Items affected | Effort |",
        "|---|---|---|---|---|",
    ]
    top = plan.top_fixes(5)
    for n, t in enumerate(top, 1):
        lines.append(f"| {n} | {md_cell(t.title, 80)} | {t.unlocks} | {t.affects} | "
                     f"{t.minutes / 60:.1f} h |")  # fmt: skip
    lines += [
        "",
        "## Groups",
        "",
        "| Reason | Items | Only blocker of | Effort per item |",
        "|---|---|---|---|",
    ]
    for g in plan.groups:
        lines.append(f"| {md_cell(g.title, 80)} | {g.count} | {g.alone} | "
                     f"{g.playbook.effort if g.playbook else 'not estimated'} |")  # fmt: skip
    for g in plan.groups:
        lines += ["", f"## {md_cell(g.title, 100)} ({g.count})", ""]
        pb = g.playbook
        if pb:
            lines += [
                f"- In the source tool: {pb.source}",
                f"- Closest New Relic construct: {pb.newrelic}"
                + (
                    " (no direct equivalent; this is the best workaround)"
                    if pb.no_equivalent
                    else ""
                ),
                f"- Effort: {pb.effort} per item",
                f"- What will differ: {pb.differs}",
            ]
            if pb.measured:
                lines.append(f"- Measured: {pb.measured}")
            lines += ["- Steps:", *(f"  {n}. {s}" for n, s in enumerate(pb.steps, 1))]
            if pb.template:
                lines += [
                    "- Template (edit the parts in angle brackets):",
                    "",
                    "  ```",
                    *(f"  {x}" for x in pb.template.splitlines()),
                    "  ```",
                ]
            if pb.links:
                lines += ["- Documentation:", *_link_line(pb)]
        else:
            lines.append("- No playbook yet. Generic steps from the remediation rules:")
            lines += [
                f"  - {s}" for s in dict.fromkeys(s for i in g.items[:1] for s in _steps_of(i))
            ]
        lines += ["", "Checklist:", ""]
        for i in g.items:
            where = (
                f" in {md_cell(i.container, 60)}" if i.container and i.container != i.name else ""
            )
            extra = f" (x{i.count})" if i.count > 1 else ""
            also = [r.title for r in i.remedies[1:] if not r.secondary]
            tail = f" - also blocked by: {md_cell('; '.join(also), 150)}" if also else ""
            lines.append(f"- [ ] {i.kind}: {md_cell(i.name, 80)}{where}{extra}{tail}")
            ready = _filled(i)
            if ready and ready != (g.playbook.template if g.playbook else ""):
                lines.append(f"  - with this item's names: `{ready}`")
    if plan.unmatched:
        lines += ["", "## Items with no remediation rule", ""]
        lines += [
            f"- [ ] {i.kind}: {md_cell(i.name, 80)}: {md_cell('; '.join(i.reasons), 200)}"
            for i in plan.unmatched
        ]
    return "\n".join(lines) + "\n"


def summary(plan: Plan) -> Counter[str]:
    return Counter({g.title: g.count for g in plan.groups})
