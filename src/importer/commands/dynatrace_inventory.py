"""Commands for Dynatrace dashboards, synthetic monitors and the instrumentation inventory.

`extract-dynatrace-inventory` reads from a tenant (read-only). The `plan-dynatrace-*` commands
read the files it writes (or files saved by hand) and write a report and a JSON plan. No command
here creates anything in New Relic.
"""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory.extract import (
    environment_url,
    http_getter,
    inventory_token,
)
from importer.sources.dynatrace.inventory.extract import extract as extract_inventory

OutOption = Annotated[Path, typer.Option(help="New folder for the result")]


def _fail(exc: Exception) -> typer.Exit:
    typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
    return typer.Exit(code=1)


def register(app: typer.Typer) -> None:
    @app.command("extract-dynatrace-inventory")
    def extract_inventory_command(
        out: Annotated[Path, typer.Option(help="New folder for the export")] = Path(
            "out/dynatrace-inventory"
        ),
    ) -> None:
        """Read dashboards, synthetic monitors and an entity inventory from a Dynatrace tenant.

        Read-only: GET requests to fixed paths. Set DYNATRACE_ENV_URL
        (https://<tenant id>.apps.dynatrace.com) and DYNATRACE_API_TOKEN in .env or the
        environment; the token is never stored or printed. An area that cannot be read is a
        warning and the other areas still run. Names, owners, entity names and secrets in
        scripts are reduced or dropped, but review the export before sharing it.
        """
        try:
            token, env_url = inventory_token(), environment_url()
            with http_getter(token) as get:
                result = extract_inventory(get, env_url, out)
        except (DynatraceError, ValueError, OSError) as exc:
            raise _fail(exc) from None
        typer.echo(
            f"Read {result.dashboards} dashboards, {result.classic_dashboards} classic "
            f"dashboards, {result.monitors} synthetic monitors, {result.locations} locations "
            f"and {result.entities} entities."
        )
        for warning in result.warnings:
            typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
        if result.read_nothing:
            typer.echo("Error: nothing could be read; see the warnings above.", err=True)
            raise typer.Exit(code=1)
        typer.echo("Next:")
        typer.echo(
            f"  importer plan-dynatrace-dashboards --in {out} --out {out / 'plan-dashboards'}"
        )
