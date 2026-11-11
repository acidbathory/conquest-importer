"""Command: convert-azure-notifications.

Reads the Azure action groups and alert processing rules (a file or folder of JSON, for example
what the Azure extract wrote or the portal's `az monitor action-group list` output) and the output
of `convert --source azure`, and writes New Relic notification plans, muting rules, Terraform and a
report. Offline: no network, no key, no secret. The plan folder can be read by
`apply-notifications`; plans with a secure URL are meant for the Terraform file (see the report).
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from importer.core.emit.common import CannotEmit
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.ir import IRMonitor
from importer.core.text import printable
from importer.sources.azure.notifications.convert import ROUTE_BY, Conversion, Inputs
from importer.sources.azure.notifications.convert import convert_notifications as convert
from importer.sources.azure.notifications.models import load_export
from importer.sources.azure.notifications.outputs import output_files, write_files

MAX_IR_FILES = 50_000


def read_monitors(alerts: Path) -> list[IRMonitor]:
    """The saved IR of a `convert` run, with a cap on the number and size of files."""
    folder = alerts / "ir"
    if not folder.is_dir():
        raise ValueError(f"{alerts} has no ir/ folder; pass the output folder of `convert`")
    files = sorted(folder.glob("*.json"))[:MAX_IR_FILES]
    if any(f.stat().st_size > MAX_INPUT_BYTES for f in files):
        raise ValueError("an IR file is larger than the importer accepts")
    try:
        return [IRMonitor.model_validate_json(f.read_text()) for f in files]
    except (RecursionError, ValueError):  # a ValidationError would quote the file's text
        raise ValueError("an IR file in ir/ cannot be read; run `convert` again") from None


def _summary(c: Conversion) -> str:
    receivers = c.receiver_counts()
    converted = sum(k["converted"] for k in receivers.values())
    total = sum(sum(v for n, v in k.items() if n != "approximate") for k in receivers.values())
    rules = c.rule_counts()
    return (
        f"Read {len(c.groups)} action groups ({total} receivers, {converted} converted) and "
        f"{len(c.rule_results)} alert processing rules ({rules['converted']} converted to "
        f"{len(c.muting_rules)} muting rules, {rules['manual']} manual). Planned "
        f"{sum(p.workflow is not None for p in c.plans)} workflows (all disabled). "
        f"{len(c.secrets)} secrets must be supplied."
    )


def _convert(source: Path, alerts: Path, out: Path, prefix: str, route_by: str) -> None:
    export = load_export(source)
    monitors = read_monitors(alerts)
    result = convert(Inputs(export=export, monitors=monitors, prefix=prefix, route_by=route_by))
    write_files(output_files(result), out)
    typer.echo(f"{_summary(result)} See {printable(str(out / 'report.md'), 200)}")
    for problem in result.problems:
        typer.echo(f"  Warning: {printable(problem, 200)}", err=True)
    for violation in result.violations:
        typer.echo(f"  Problem: {printable(violation, 300)}", err=True)
    if result.violations:
        raise typer.Exit(code=2)


def convert_azure_notifications(
    source: Annotated[
        Path,
        typer.Option(
            "--in", help="File or folder of Azure action groups and alert processing rules"
        ),
    ] = Path("out/azure-notifications"),
    alerts: Annotated[
        Path,
        typer.Option("--alerts", help="Output folder of `convert --source azure` (has ir/)"),
    ] = Path("out/azure-alerts"),
    out: Annotated[Path, typer.Option(help="New output folder")] = Path(
        "out/azure-notification-plans"
    ),
    prefix: Annotated[str, typer.Option(help="Prefix for created object names")] = "Imported:",
    route_by: Annotated[
        str,
        typer.Option(
            "--route-by",
            help="How a workflow selects issues: tag (the azure.actionGroup tag written by the "
            "alert conversion) or name (the converted condition names)",
        ),
    ] = "tag",
) -> None:
    """Plan New Relic destinations, workflows and muting rules from Azure notifications.

    Action group receivers become destinations (email, webhook, Logic App, Azure Function,
    runbook) or manual steps (ITSM, app push, SMS, voice, Event Hub, ARM role). Each action group
    that a converted condition uses becomes one workflow. Suppression rules from alert processing
    rules become muting rules; rules that add action groups stay manual. Nothing is created and no
    secret is read: URLs with a token are names that you supply when you apply the Terraform file.
    Offline.
    """
    if route_by not in ROUTE_BY:
        typer.echo(f"Error: --route-by must be one of {', '.join(ROUTE_BY)}", err=True)
        raise typer.Exit(code=1)
    try:
        _convert(source, alerts, out, prefix, route_by)
    except (OSError, ValueError, CannotEmit, json.JSONDecodeError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None


def register(app: typer.Typer) -> None:
    app.command("convert-azure-notifications")(convert_azure_notifications)
