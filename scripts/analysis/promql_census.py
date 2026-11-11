"""Census of the PromQL in Grafana dashboards: which constructs, and which ones block conversion.

Usage:
    uv run python scripts/analysis/promql_census.py DASHBOARDS... [--alerts PATH...] [--json OUT]

`DASHBOARDS` are files or folders of Grafana dashboard JSON (the importer's own loader reads
them). Every visible Prometheus query is parsed with `importer.sources.grafana.promql_ast`,
mapped to construct ids (`promql_constructs.py`) and run through the current converter
(`convert_query`) so that "blocks today" is the converter's own answer. Nothing here touches a
network. The script prints a Markdown summary; `--json` writes the full result.
"""

import argparse
import json
import sys
from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from promql_constructs import constructs_of
from promql_verdicts import VERDICTS, verdict_for

from importer.core.dashboard_ir import WidgetKind
from importer.sources.grafana.dashboard_convert.legend import read_legend
from importer.sources.grafana.dashboard_convert.name_map import active_name_map
from importer.sources.grafana.dashboard_convert.promql import (
    QueryOptions,
    convert_classic,
    convert_query,
)
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboard_convert.translate import (
    Item,
    draft_panels,
    load_items,
)
from importer.sources.grafana.dashboards.datasources import resolve_target
from importer.sources.grafana.promql_ast import PromQLSyntaxError, parse

# Constructs the converter writes today (read from dashboard_convert/promql.py; the census test
# checks each one and each excluded one against `convert_query`).
SUPPORTED_TODAY = frozenset(
    {
        "selector.instant", "selector.range",
        "matcher.eq", "matcher.ne", "matcher.re", "matcher.nre",
        "matcher.value_variable", "matcher.ne_empty",
        "window.fixed", "window.rate_interval", "window.interval", "window.range",
        "window.variable",
        "fn.rate", "fn.irate", "fn.increase", "fn.histogram_quantile",
        "agg.sum", "agg.avg", "agg.min", "agg.max", "agg.count", "agg.topk",
        "agg.by", "agg.ungrouped",
        "binop.+", "binop.-", "binop.*", "binop./",
        "arith.scalar_scalar", "arith.vector_scalar", "arith.vector_vector",
        "match.implicit", "scalar.literal", "unary.-", "var.as_operand", "name.recording_rule",
    }
)  # fmt: skip
QUERY_REASON_PREFIXES = ("dashboard query", "dashboard logql query")
TIMESERIES_TYPES = frozenset({"timeseries", "graph"})


@dataclass
class Expression:
    dashboard: str
    panel: str  # "<dashboard index>:<panel index>"
    panel_title: str
    panel_type: str
    ref_id: str
    text: str
    family: str
    parse_error: str = ""
    constructs: list[str] = field(default_factory=list)
    converts: bool = False  # the plain reader (convert_classic) writes it: SUPPORTED_TODAY
    engine: bool = False  # only the engine (promql_engine) writes it, see the rules registry
    reason: str = ""  # the plain reader's reason when it does not convert

    @property
    def unsupported(self) -> set[str]:
        return {c for c in self.constructs if c not in SUPPORTED_TODAY}


@dataclass
class PanelRow:
    key: str
    dashboard: str
    title: str
    type: str
    converted: bool  # the importer makes a widget of it today
    planner_reasons: list[str]
    other_blockers: list[str]  # reasons that are not about the query text
    expressions: list[int] = field(default_factory=list)  # indexes into Census.expressions


@dataclass
class Census:
    dashboards: int = 0
    panels_total: int = 0
    expressions: list[Expression] = field(default_factory=list)
    panels: list[PanelRow] = field(default_factory=list)  # panels with a PromQL expression
    loki_expressions: int = 0
    other_panels: int = 0


def _options(
    draft_ctx_variables: dict[str, str | None], names: list[str], panel_type: str, drafted
) -> QueryOptions:  # type: ignore[no-untyped-def]
    """Query options in which every dashboard variable is a plain variable (not "unavailable"),
    so that the converter judges the query text and not the variable plan."""
    variables = {**dict.fromkeys(names), **draft_ctx_variables}
    return QueryOptions(
        variables=variables,
        name_map=drafted.ctx.name_map,
        timeseries=panel_type in TIMESERIES_TYPES,
        intervals=drafted.ctx.variables.intervals,
    )


def analyse_item(item: Item, census: Census, index: int) -> None:
    dash = item.dash
    if dash is None:
        return
    census.dashboards += 1
    drafted = draft_panels(dash, item.raw, active_name_map())
    names = [v.name for v in dash.variables]
    for p_index, panel in enumerate(dash.panels):
        census.panels_total += 1
        key = f"{index}:{p_index}"
        row_ids: list[int] = []
        for target in panel.targets:
            if target.hidden or not target.expr.strip() or target.panel_id is not None:
                continue
            family = resolve_target(target, panel, dash)
            if family == "loki":
                census.loki_expressions += 1
                continue
            if family != "prometheus":
                continue
            exp = Expression(
                dash.title, key, panel.title, panel.type, target.ref_id, target.expr, family
            )
            try:
                exp.constructs = sorted(constructs_of(parse(target.expr), target.expr))
            except PromQLSyntaxError as error:
                exp.parse_error = str(error)
            opts = _options(drafted.ctx.variables.options, names, panel.type, drafted)
            legend, repeat = read_legend(target.legend), panel.repeat or None
            try:
                convert_classic(target.expr, family, opts, legend, repeat)
                exp.converts = True
            except Unconvertible as stop:
                exp.reason = stop.reason
                try:
                    convert_query(target.expr, family, opts, legend, repeat)
                    exp.engine = True
                except Unconvertible:
                    pass
            except Exception as error:
                exp.reason = f"error: {type(error).__name__}"
            row_ids.append(len(census.expressions))
            census.expressions.append(exp)
        if not row_ids:
            census.other_panels += 1
            continue
        draft = drafted.drafts[p_index]
        reasons = list(drafted.plan.panels[p_index].reasons)
        census.panels.append(
            PanelRow(
                key,
                dash.title,
                panel.title,
                panel.type,
                draft.kind != WidgetKind.UNSUPPORTED,
                reasons,
                [x for x in reasons if not x.startswith(QUERY_REASON_PREFIXES)],
                row_ids,
            )
        )


def build(paths: list[Path]) -> Census:
    census = Census()
    index = 0
    for path in paths:
        for item in load_items(path):
            analyse_item(item, census, index)
            index += 1
    return census


def blockers(census: Census, row: PanelRow) -> set[str]:
    """Unsupported constructs (or converter-shape tags) of the panel's failing expressions."""
    found: set[str] = set()
    for i in row.expressions:
        exp = census.expressions[i]
        if exp.converts:
            continue
        found |= exp.unsupported or {"unexplained: " + (exp.parse_error or exp.reason)[:70]}
    return found


def table(census: Census) -> list[dict[str, object]]:
    """One row per construct: expressions, panels, blocked today, sole blocker."""
    exprs: Counter[str] = Counter()
    panels: dict[str, set[str]] = defaultdict(set)
    sole: Counter[str] = Counter()
    sole_clean: Counter[str] = Counter()
    blocks: Counter[str] = Counter()
    for row in census.panels:
        for i in row.expressions:
            for c in census.expressions[i].constructs:
                exprs[c] += 1
                panels[c].add(row.key)
        b = blockers(census, row)
        blocks.update(b)
        if len(b) == 1 and not row.converted:
            (only,) = b
            sole[only] += 1
            sole_clean[only] += not row.other_blockers
    out: list[dict[str, object]] = []
    for c in sorted(exprs, key=lambda x: (-len(panels[x]), x)):
        out.append(
            {
                "construct": c,
                "supported_today": c in SUPPORTED_TODAY,
                "expressions": exprs[c],
                "panels": len(panels[c]),
                "blocks": blocks[c],
                "sole_blocker": sole[c],
                "sole_blocker_clean": sole_clean[c],
            }
        )
    for tag in sorted(set(sole) - set(exprs)):
        out.append({"construct": tag, "supported_today": False, "expressions": 0, "panels": 0,
                    "blocks": blocks[tag], "sole_blocker": sole[tag], "sole_blocker_clean": sole_clean[tag]})  # fmt: skip
    return out


@dataclass
class Projection:
    """Panels with a PromQL query, and how many convert under one scenario."""

    panels: int
    query_layer: int  # every PromQL expression of the panel converts
    end_to_end: int  # ... and nothing else (variables, transformations, widget type) blocks
    if_variables_solved: int = 0  # ... and only the "variable stays manual" gate is ignored


def today() -> Callable[[str], bool]:
    return lambda c: c in SUPPORTED_TODAY


def expressible(cap: int, verified_only: bool = False) -> Callable[[str], bool]:
    """Supported = what works today plus every construct whose verdict code is at most `cap`.

    With `verified_only` a construct also needs the status MEASURED or DOCS."""

    def check(construct: str) -> bool:
        if construct in SUPPORTED_TODAY:
            return True
        verdict = verdict_for(construct)
        return verdict.code <= cap and (not verified_only or verdict.status in ("MEASURED", "DOCS"))

    return check


VARIABLE_GATE = "dashboard panel uses a variable that stays manual"


def only(*extra: str) -> Callable[[str], bool]:
    """Supported = what works today plus the listed constructs (a what-if for cheap fixes)."""
    return lambda c: (
        c in SUPPORTED_TODAY or c in extra or any(c.startswith(x) for x in extra if x.endswith("."))
    )


def project(
    census: Census, supported: Callable[[str], bool], group: str | None = None
) -> Projection:
    query_layer = end_to_end = freed = 0
    panels = 0
    for row in census.panels:
        if group is not None and group_of(row) != group:
            continue
        panels += 1
        ok = True
        for i in row.expressions:
            exp = census.expressions[i]
            if exp.parse_error or (
                not exp.converts and not all(supported(c) for c in exp.unsupported)
            ):
                ok = False
            elif not exp.converts and not exp.unsupported:
                ok = False  # failed for a reason no construct explains: never counted
        query_layer += ok
        end_to_end += ok and not row.other_blockers
        freed += ok and all(x.startswith(VARIABLE_GATE) for x in row.other_blockers)
    return Projection(panels, query_layer, end_to_end, freed)


def greedy(census: Census, cap: int, steps: int = 12) -> list[tuple[str, int, int]]:
    """Add the construct that unlocks the most panels, step by step.

    Returns (construct, panels convertible at the query layer, same with the variable gate
    lifted). Only constructs with a verdict code of at most `cap` are candidates."""
    chosen: set[str] = set()
    candidates = {
        c for e in census.expressions for c in e.unsupported if verdict_for(c).code <= cap
    }

    def supported(c: str) -> bool:
        return c in SUPPORTED_TODAY or c in chosen

    out: list[tuple[str, int, int]] = []
    base = project(census, supported)
    for _ in range(steps):
        best: tuple[int, int, str] | None = None
        for c in sorted(candidates - chosen):
            chosen.add(c)
            p = project(census, supported)
            chosen.discard(c)
            gain = (
                p.query_layer - base.query_layer,
                p.if_variables_solved - base.if_variables_solved,
            )
            if gain[0] > 0 and (best is None or gain > best[:2]):
                best = (gain[0], gain[1], c)
        if best is None:
            break
        chosen.add(best[2])
        base = project(census, supported)
        out.append((best[2], base.query_layer, base.if_variables_solved))
    return out


def group_of(row: PanelRow) -> str:
    return "community" if row.dashboard.startswith("ckg-") else "built-in"


def other_blocker_classes(census: Census) -> Counter[str]:
    """Panels by the non-query reasons the planner gave (one panel can have several)."""
    out: Counter[str] = Counter()
    for row in census.panels:
        for reason in row.other_blockers:
            out[reason] += 1
    return out


def alert_expressions(paths: list[Path]) -> list[str]:
    """Every `expr` string in alert rule JSON files (recursively)."""
    found: list[str] = []

    def visit(value: object) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "expr" and isinstance(item, str) and item.strip():
                    found.append(item)
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    for path in paths:
        files = sorted(path.rglob("*.json")) if path.is_dir() else [path]
        for file in files:
            try:
                visit(json.loads(file.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                continue
    return found


def alert_table(texts: list[str]) -> tuple[int, int, list[tuple[str, int]]]:
    """(expressions, unparsed, [(construct, expressions)]) for alert rule queries."""
    counts: Counter[str] = Counter()
    bad = 0
    for text in texts:
        try:
            counts.update(constructs_of(parse(text), text))
        except PromQLSyntaxError:
            bad += 1
    return len(texts), bad, sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def render(census: Census, alerts: tuple[int, int, list[tuple[str, int]]] | None = None) -> str:
    """The Markdown tables of the research note."""
    lines: list[str] = []
    rows = sorted(
        table(census),
        key=lambda r: (bool(r["supported_today"]), -int(str(r["blocks"])), -int(str(r["panels"]))),
    )
    lines.append(
        f"dashboards {census.dashboards}; panels {census.panels_total}; panels with PromQL "
        f"{len(census.panels)}; PromQL expressions {len(census.expressions)}; "
        f"LogQL expressions skipped {census.loki_expressions}"
    )
    groups = Counter(group_of(p) for p in census.panels)
    converting = sum(e.converts for e in census.expressions)
    engine = sum(e.engine for e in census.expressions)
    lines.append(f"PromQL panels by group: {dict(groups)}")
    lines.append(
        f"PromQL expressions the converter writes today: {converting} of {len(census.expressions)}"
        f"; the engine adds {engine} more ({converting + engine} of {len(census.expressions)})"
    )
    lines.append("")
    lines.append(
        "| construct | expressions | panels | panels it blocks | sole blocker | sole, nothing else blocks | today | verdict | evidence |"
    )
    lines.append("|---|---:|---:|---:|---:|---:|---|---:|---|")
    for r in rows:
        name = str(r["construct"])
        v = verdict_for(name)
        today_ok = "yes" if r["supported_today"] else "no"
        lines.append(
            f"| `{name}` | {r['expressions']} | {r['panels']} | {r['blocks']} | {r['sole_blocker']} | "
            f"{r['sole_blocker_clean']} | {today_ok} | {v.code} | {v.status} |"
        )
    lines.append("")
    lines.append(
        "| scenario | PromQL panels | query layer converts | end to end converts | end to end if the variable gate is lifted |"
    )
    lines.append("|---|---:|---:|---:|---:|")
    for label, fn in (
        ("today", today()),
        (
            "cheap parser fixes only (quotes, upper case, huge)",
            only("style.", "shape.huge_expression"),
        ),
        ("verdict 1 and 2, any evidence", expressible(2)),
        ("verdict 1 and 2, MEASURED or DOCS only", expressible(2, True)),
        ("verdict 1, 2 and 3, any evidence", expressible(3)),
    ):
        p = project(census, fn)
        lines.append(
            f"| {label} | {p.panels} | {p.query_layer} | {p.end_to_end} | {p.if_variables_solved} |"
        )
    lines.append("")
    lines.append(
        "| group | scenario | PromQL panels | query layer | end to end | if variable gate lifted |"
    )
    lines.append("|---|---|---:|---:|---:|---:|")
    for group in ("community", "built-in"):
        for label, fn in (
            ("today", today()),
            ("verdict 1, 2", expressible(2)),
            ("verdict 1, 2, 3", expressible(3)),
        ):
            p = project(census, fn, group)
            lines.append(
                f"| {group} | {label} | {p.panels} | {p.query_layer} | {p.end_to_end} | {p.if_variables_solved} |"
            )
    lines.append("")
    lines.append("Greedy order (verdict 1 to 3), each row adds one construct to the previous rows:")
    lines.append("")
    lines.append("| step | construct | query layer converts | with variable gate lifted |")
    lines.append("|---:|---|---:|---:|")
    for n, (c, q, f) in enumerate(greedy(census, 3), 1):
        lines.append(f"| {n} | `{c}` | {q} | {f} |")
    lines.append("")
    lines.append("| other blocker (planner reason) | panels |")
    lines.append("|---|---:|")
    for reason, n in other_blocker_classes(census).most_common():
        lines.append(f"| {reason} | {n} |")
    if alerts is not None:
        total, bad, counts = alerts
        lines += [
            "",
            f"alert rule expressions {total}, unparsed {bad}",
            "",
            "| construct | expressions |",
            "|---|---:|",
        ]
        lines += [f"| `{c}` | {n} |" for c, n in counts]
    return "\n".join(lines) + "\n"


def render_verdicts(census: Census) -> str:
    """The verdict table: every construct the table knows, with its count in the corpus."""
    seen: Counter[str] = Counter(c for e in census.expressions for c in e.constructs)

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ")

    lines = [
        "| construct | in corpus | verdict | NRQL pattern | caveats | evidence | source |",
        "|---|---:|---:|---|---|---|---|",
    ]
    for name, v in sorted(VERDICTS.items(), key=lambda kv: (kv[1].code, kv[0])):
        lines.append(
            f"| `{name}` | {seen[name]} | {v.code} | {cell(v.nrql) or '-'} | {cell(v.note) or '-'} | "
            f"{v.status} | {cell(v.source) or '-'} |"
        )
    return "\n".join(lines) + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dashboards", nargs="+", type=Path)
    parser.add_argument("--alerts", nargs="*", type=Path, default=[])
    parser.add_argument("--json", type=Path)
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--verdict-table", type=Path)
    args = parser.parse_args(argv)
    census = build(args.dashboards)
    alerts = alert_table(alert_expressions(args.alerts)) if args.alerts else None
    text = render(census, alerts)
    if args.markdown:
        args.markdown.write_text(text, encoding="utf-8")
    else:
        sys.stdout.write(text)
    if args.verdict_table:
        args.verdict_table.write_text(render_verdicts(census), encoding="utf-8")
    if args.json:
        args.json.write_text(json.dumps(asdict(census), indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
