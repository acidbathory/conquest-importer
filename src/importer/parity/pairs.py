"""Pair each converted query with the original it came from.

The converted folders hold only NRQL: the IR of a chart widget has no PromQL, and the IR of an
alert keeps the rule name in `raw_query`. The original text is read again from the export the
conversion started from, converted in memory with the same name map, and matched to the files in
the converted folder by the NRQL text. A query whose NRQL is not found among the files (because
another name map, hints file or export was used) is not guessed: it is reported as not
comparable with that reason.
"""

import re
from pathlib import Path

from importer.core.dashboard_ir import IRDashboard, VariableType, WidgetKind
from importer.core.emit.nrql import build_nrql
from importer.core.ir import IRMonitor, SignalKind
from importer.core.text import printable
from importer.parity.model import Pair, ParityError
from importer.parity.queries import has_timeseries
from importer.sources.grafana.dashboard_convert.name_map import NameMap, active_name_map
from importer.sources.grafana.dashboard_convert.panels import _targets
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.dashboard_convert.translate import draft_panels, load_items
from importer.sources.grafana.dashboards.datasources import resolve_target
from importer.sources.grafana.models import load_rules
from importer.sources.grafana.translate import Fail, _plan, translate

COMPARISON = re.compile(r"(?:==|!=|>=|<=|>|<)")
QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"|\'(?:[^\'\\]|\\.)*\'|`[^`]*`')
BRACES = re.compile(r"\{[^{}]*\}")
NOT_SERIES = (
    "a stat, table or other single-value widget has no series over time to compare; "
    "the same query as a chart can be checked"
)
NOT_FOUND = (
    "the original query was not found in the export: the conversion used another export, name "
    "map or hints file (pass the same --export, --name-map and --hints)"
)


def converted_dashboards(folder: Path) -> list[IRDashboard]:
    return [
        IRDashboard.model_validate_json(p.read_text())
        for p in sorted((folder / "dashboard-ir").glob("*.json"))
    ]


def converted_monitors(folder: Path) -> list[IRMonitor]:
    return [
        IRMonitor.model_validate_json(p.read_text()) for p in sorted((folder / "ir").glob("*.json"))
    ]


def find_folders(root: Path) -> tuple[Path | None, Path | None]:
    """(dashboards folder, alerts folder) under a converted folder, in either layout."""
    dashboards = next(
        (c for c in (root, root / "dashboards") if (c / "dashboard-ir").is_dir()), None
    )
    alerts = next((c for c in (root, root / "alerts") if (c / "ir").is_dir()), None)
    return dashboards, alerts


def variable_defaults(
    dash: IRDashboard, given: dict[str, tuple[str, ...]]
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    found: dict[str, tuple[str, ...]] = {}
    for v in dash.variables:
        if v.type is VariableType.NRQL and v.default_values in (["*"], []):
            continue
        if v.default_values:
            found[v.name] = tuple(v.default_values)
    found.update(given)
    return tuple(sorted(found.items()))


def _key(dash_uid: str, title: str, source: str) -> str:
    return printable(dash_uid or title or source or "dashboard", 10_000)


def dashboard_pairs(
    folder: Path,
    export: Path,
    names: NameMap | None = None,
    given: dict[str, tuple[str, ...]] | None = None,
) -> list[Pair]:
    names = names if names is not None else active_name_map()
    given = given or {}
    originals: dict[str, list[tuple[str, str, str]]] = {}  # key -> (nrql, expr, family)
    seen: set[str] = set()
    for item in load_items(export):
        source = item.dash
        if source is None:
            continue
        key, n = _key(source.uid, source.title, item.source), 1
        base = key
        while key in seen:
            n += 1
            key = f"{base}#{n}"
        seen.add(key)
        found = draft_panels(source, item.raw, names)
        rows = originals.setdefault(key, [])
        for panel, draft in zip(source.panels, found.drafts, strict=False):
            if draft.kind is not WidgetKind.CHART:
                continue
            try:
                targets = _targets(panel, found.ctx)
            except Unconvertible:
                continue
            if len(targets) != len(draft.queries):
                continue
            for (target, owner), nrql in zip(targets, draft.queries, strict=True):
                family = resolve_target(target, owner, source)
                rows.append((nrql, target.expr, family))
    pairs: list[Pair] = []
    for dash in converted_dashboards(folder):
        pool = list(originals.get(dash.source_key, []))
        variables = variable_defaults(dash, given)
        for widget in dash.widgets:
            if widget.kind is not WidgetKind.CHART:
                continue
            for series in widget.raw_series:
                hit = next((r for r in pool if r[0] == series.nrql), None)
                if hit is not None:
                    pool.remove(hit)
                skip = "" if hit else NOT_FOUND
                if hit and not has_timeseries(series.nrql):
                    skip = NOT_SERIES
                pairs.append(
                    Pair(
                        "widget", dash.name, dash.source_key, widget.title,
                        hit[2] if hit else "prometheus", hit[1] if hit else "", series.nrql,
                        has_timeseries(series.nrql), variables, skip,
                    )
                )  # fmt: skip
    return pairs


def _has_comparison(expr: str) -> bool:
    plain = BRACES.sub("", QUOTED.sub('""', expr))
    return bool(COMPARISON.search(plain))


def alert_pairs(folder: Path, export: Path) -> list[Pair]:
    on_disk = {m.source_key: m for m in converted_monitors(folder)}
    rules = load_rules(export)
    pairs: list[Pair] = []
    for rule, monitor in zip(rules, translate(rules), strict=True):
        disk = on_disk.get(monitor.source_key)
        if disk is None or disk.signal.kind not in (SignalKind.METRIC, SignalKind.LOG):
            continue
        try:
            nrql = build_nrql(disk)
        except ValueError:
            continue
        try:
            plan = _plan(rule)
            expr, family = plan.expr, ("loki" if plan.kind == "loki" else "prometheus")
        except Fail:
            expr, family = "", "prometheus"
        skip = "" if expr else NOT_FOUND
        if expr and family == "prometheus" and _has_comparison(expr):
            skip = "the rule's own query includes the threshold comparison, so it returns no values"
        pairs.append(
            Pair("alert", disk.name, disk.source_key, disk.name, family, expr, nrql, True, (), skip)
        )
    return pairs


def default_export(converted: Path) -> Path | None:
    """The export that the guided menu keeps next to `converted/`."""
    sibling = converted.resolve().parent / "export"
    return sibling if sibling.is_dir() else None


def build_pairs(
    converted: Path,
    export: Path | None,
    names: NameMap | None = None,
    given: dict[str, tuple[str, ...]] | None = None,
) -> list[Pair]:
    dashboards, alerts = find_folders(converted)
    if dashboards is None and alerts is None:
        raise ParityError(f"no dashboard-ir or ir folder in {printable(str(converted), 200)}")
    root = export if export is not None else default_export(converted)
    if root is None:
        raise ParityError("pass --export: the folder the conversion started from")
    pairs: list[Pair] = []
    try:
        if dashboards is not None:
            pairs += dashboard_pairs(dashboards, _sub(root, "dashboards"), names, given)
        if alerts is not None:
            pairs += alert_pairs(alerts, _sub(root, "alerts"))
    except (ValueError, OSError) as exc:
        raise ParityError(f"could not read the export: {printable(str(exc), 300)}") from None
    return pairs


def _sub(root: Path, name: str) -> Path:
    return root / name if (root / name).is_dir() else root
