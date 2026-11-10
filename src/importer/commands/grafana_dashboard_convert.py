"""Command: convert Grafana dashboards to New Relic dashboards, offline.

`importer convert-dashboards --source grafana` already works through the dashboard registry; this
command adds what the registry path cannot take: a name map for the metrics, and a closing
section in the report that lists the metric names the queries use and what must be true for them
to show data. Nothing is sent anywhere and the dashboards are created private by the existing
`apply-dashboards`.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from importer.core.dashboard_ir import IRDashboard, WidgetKind
from importer.core.dashboard_report import build_dashboard_report
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import dashboard_id, dashboards_terraform, nerdgraph_dashboard
from importer.core.report import md_cell
from importer.core.text import printable
from importer.sources.grafana.dashboard_convert import reasons as r
from importer.sources.grafana.dashboard_convert.name_map import (
    MAP_ENV,
    NameMap,
    NameMapError,
    active_name_map,
    load_name_map,
    merge_hints,
)
from importer.sources.grafana.dashboard_convert.translate import Report, convert_all, load_items

MAX_METRICS_LISTED = 40


def _inside(out: Path, source: Path) -> bool:
    """True when `out` is the input folder or sits below it."""
    folder = source if source.is_dir() else source.parent
    return folder.resolve() in (out.resolve(), *out.resolve().parents)


def prerequisite_section(report: Report, map_entries: int) -> str:
    """Closing section of report.md: the data the dashboards need and the names they use."""
    lines = [
        "",
        "## Grafana data prerequisites",
        "",
        f"- {r.D_DATA[0].upper()}{r.D_DATA[1:]}.",
        "- Names are used as Prometheus wrote them. Run the data check before trusting a widget; "
        "a metric that arrives under another name needs an entry in a name map "
        f"(`--name-map`, or the environment variable {MAP_ENV}).",
        f"- Name map entries loaded: **{map_entries}**.",
        "",
        f"Metric names used by converted queries: **{len(report.metrics)}**.",
    ]
    if report.metrics:
        lines += ["", "| Metric | Queries |", "|---|---|"]
        top = report.metrics.most_common(MAX_METRICS_LISTED)
        lines += [f"| {md_cell(name, 120)} | {count} |" for name, count in sorted(top)]
        if len(report.metrics) > MAX_METRICS_LISTED:
            lines.append(f"\n{len(report.metrics) - MAX_METRICS_LISTED} more names are not listed.")
    return "\n".join(lines) + "\n"


def _write(out: Path, dashboards: list[IRDashboard], report: Report, entries: int) -> int:
    """Write IR, NerdGraph, Terraform and the report; returns how many dashboards were emitted."""
    (out / "dashboard-ir").mkdir(parents=True, exist_ok=True)
    (out / "nerdgraph").mkdir(parents=True, exist_ok=True)
    emitted: list[tuple[int, IRDashboard]] = []
    for d in dashboards:
        ident = dashboard_id(d)
        (out / "dashboard-ir" / f"{ident}.json").write_text(d.model_dump_json(indent=2) + "\n")
        try:
            payload = nerdgraph_dashboard(d)
        except CannotEmit:
            continue
        emitted.append((ident, d))
        (out / "nerdgraph" / f"{ident}.json").write_text(json.dumps(payload, indent=2) + "\n")
    (out / "dashboards.tf").write_text(dashboards_terraform(emitted))
    keys = {d.source_key for _, d in emitted}
    text = build_dashboard_report(dashboards, keys, "grafana") + prerequisite_section(
        report, entries
    )
    (out / "report.md").write_text(text)
    return len(emitted)


def register(app: typer.Typer) -> None:
    @app.command("convert-grafana-dashboards")
    def convert_grafana_dashboards(
        source: Annotated[
            Path, typer.Option("--in", help="File or folder from `extract-grafana-dashboards`")
        ],
        out: Annotated[Path, typer.Option(help="Output directory")] = Path("out/grafana-dash"),
        name_map: Annotated[
            Path | None,
            typer.Option(
                "--name-map",
                help="JSON map from a Prometheus metric name to its New Relic event and "
                f"attribute; without it {MAP_ENV} is read",
            ),
        ] = None,
        hints: Annotated[
            list[Path] | None,
            typer.Option(
                "--hints",
                help="JSON from `grafana-prom-hints`: the exact type of each metric and the "
                "labels that name one series. Replaces the guess from the name suffix. May be "
                "given more than once (one file per data source).",
            ),
        ] = None,
    ) -> None:
        """Convert Grafana dashboards to IR, NerdGraph payloads, Terraform and a report.

        Offline and read-only: nothing is sent anywhere. Panels the coverage plan calls
        convertible become widgets; every other panel becomes a visible "Not migrated" widget
        with the reason and the original query. Converted queries use the Prometheus metric
        names as they are, so the data must reach New Relic under those names (remote write,
        OTLP or Alloy); see the closing section of report.md. Dashboards are created private.
        """
        try:
            if _inside(out, source):
                raise ValueError(f"--out {out} is inside --in {source}; choose a separate folder")
            names: NameMap = load_name_map(name_map) if name_map else active_name_map()
            for hint_file in hints or []:
                names = merge_hints(names, load_name_map(hint_file))
            items = load_items(source)
            if not items:
                raise ValueError(f"no Grafana dashboards found in {source}")
            dashboards, report = convert_all(items, names)
            emitted = _write(out, dashboards, report, len(names.metrics))
        except (ValueError, OSError, NameMapError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        widgets = [w for d in dashboards for w in d.widgets if w.source_type != "row"]
        placeholders = sum(w.kind == WidgetKind.UNSUPPORTED for w in widgets)
        typer.echo(
            f"Read {len(dashboards)} dashboards, emitted {emitted}: {len(widgets) - placeholders} "
            f"widgets converted, {placeholders} placeholders. See {out / 'report.md'}"
        )
        if hints:
            typer.echo(f"Hints loaded: {len(names.types)} types, {len(names.series)} series sets.")
        typer.echo(f"Note: {r.D_DATA}.")
