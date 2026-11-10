"""Census of the PromQL in Grafana dashboards: which constructs, and which ones block conversion.

Usage:
    uv run python scripts/analysis/promql_census.py DASHBOARDS... [--alerts PATH...] [--json OUT]

`DASHBOARDS` are files or folders of Grafana dashboard JSON (the importer's own loader reads
them). Every visible Prometheus query is parsed with `importer.sources.grafana.promql_ast`,
mapped to construct ids (`promql_constructs.py`) and run through the current converter
(`convert_query`) so that "blocks today" is the converter's own answer. Nothing here touches a
network. The script prints a Markdown summary; `--json` writes the full result.
"""

import json
import sys
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from promql_constructs import constructs_of  # noqa: E402

from importer.core.dashboard_ir import WidgetKind  # noqa: E402
from importer.sources.grafana.dashboard_convert.legend import read_legend  # noqa: E402
from importer.sources.grafana.dashboard_convert.promql import (  # noqa: E402
    QueryOptions,
    convert_query,
)
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible  # noqa: E402
from importer.sources.grafana.dashboard_convert.translate import (  # noqa: E402
    Item,
    draft_panels,
    load_items,
)
from importer.sources.grafana.dashboard_convert.name_map import active_name_map  # noqa: E402
from importer.sources.grafana.dashboards.datasources import resolve_target  # noqa: E402
from importer.sources.grafana.promql_ast import PromQLSyntaxError, parse  # noqa: E402

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
        "match.implicit", "scalar.literal", "unary.-", "var.as_operand",
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
    converts: bool = False
    reason: str = ""  # the converter's reason when it does not convert

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


def _options(draft_ctx_variables: dict[str, str | None], names: list[str], panel_type: str, drafted) -> QueryOptions:  # type: ignore[no-untyped-def]
    """Query options in which every dashboard variable is a plain variable (not "unavailable"),
    so that the converter judges the query text and not the variable plan."""
    variables = {**{n: None for n in names}, **draft_ctx_variables}
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
            try:
                convert_query(target.expr, family, opts, read_legend(target.legend), panel.repeat or None)
                exp.converts = True
            except Unconvertible as stop:
                exp.reason = stop.reason
            except Exception as error:  # noqa: BLE001 - a census must not stop on one odd query
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
    for row in census.panels:
        for i in row.expressions:
            for c in census.expressions[i].constructs:
                exprs[c] += 1
                panels[c].add(row.key)
        b = blockers(census, row)
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
                "sole_blocker": sole[c],
                "sole_blocker_clean": sole_clean[c],
            }
        )
    for tag in sorted(set(sole) - set(exprs)):
        out.append({"construct": tag, "supported_today": False, "expressions": 0, "panels": 0,
                    "sole_blocker": sole[tag], "sole_blocker_clean": sole_clean[tag]})  # fmt: skip
    return out


def main(argv: list[str]) -> int:
    paths = [Path(a) for a in argv if not a.startswith("--")]
    census = build(paths)
    rows = table(census)
    print(f"dashboards {census.dashboards}, panels {census.panels_total}, "
          f"prom panels {len(census.panels)}, prom expressions {len(census.expressions)}, "
          f"loki expressions {census.loki_expressions}")  # fmt: skip
    for r in rows:
        print(r)
    if "--json" in argv:
        Path(argv[argv.index("--json") + 1]).write_text(json.dumps(asdict(census), indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
