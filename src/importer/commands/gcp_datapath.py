"""plan-gcp-datapath: what must be set up so converted Google items on non-polled metrics work.

Offline: reads converter output folders and writes guidance files. Sends nothing and never reads
or writes a key.
"""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.dashboard_ir import IRDashboard
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.text import printable
from importer.sources.gcp import datapath_plan as plan

MAX_IR_FILES = 50_000


def _files(folder: Path, sub: str) -> list[Path]:
    files = sorted((folder / sub).glob("*.json"))[:MAX_IR_FILES]
    if any(f.stat().st_size > MAX_INPUT_BYTES for f in files):
        raise ValueError("an IR file is larger than the importer accepts")
    return files


def _plan(
    region: Annotated[str, typer.Option(help="us, eu or jp: the New Relic data region")] = "us",
    out: Annotated[Path, typer.Option(help="New folder for the guide")] = Path("out/gcp-datapath"),
    alerts: Annotated[
        Path | None, typer.Option(help="Output folder of `convert` (has ir/)")
    ] = None,
    dashboards: Annotated[
        Path | None,
        typer.Option(help="Output folder of `convert-dashboards --source gcp` (has dashboard-ir/)"),
    ] = None,
) -> None:
    """Write the steps for sending non-polled Google metrics to New Relic. Sends nothing.

    Lists, per source class (Ops Agent, Managed Prometheus, custom, log forwarding), the metric
    names the converted conditions and widgets query and what to set up: an OpenTelemetry
    Collector snippet standing in for the Ops Agent, a Prometheus remote_write block and the
    OTLP settings for custom metrics. The license key is a placeholder or an environment
    variable name in every file.
    """
    try:
        monitors = (
            [IRMonitor.model_validate_json(f.read_text()) for f in _files(alerts, "ir")]
            if alerts
            else []
        )
        boards = (
            [
                IRDashboard.model_validate_json(f.read_text())
                for f in _files(dashboards, "dashboard-ir")
            ]
            if dashboards
            else []
        )
        plans = plan.collect(monitors, boards)
        files = plan.plan_files(region, plans)
        plan.write_files(files, out)
    except (ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(f"Wrote {len(files)} files to {printable(str(out), 200)}. No data was sent.")
    for path, one in sorted(plans.items()):
        typer.echo(f"  {path}: {one.items} converted items, {len(one.metrics)} metrics")
    typer.echo("Put the New Relic ingest license key where the files say; they hold a placeholder.")


def register(app: typer.Typer) -> None:
    app.command("plan-gcp-datapath")(_plan)
