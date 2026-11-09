"""Commands for Dynatrace dashboards, synthetic monitors and the instrumentation inventory.

`extract-dynatrace-inventory` reads from a tenant (read-only). The `plan-dynatrace-*` commands
read the files it writes (or files saved by hand) and write a report and a JSON plan. No command
here creates anything in New Relic.
"""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.dynatrace.dashboards.models import load_documents
from importer.sources.dynatrace.dashboards.plan import plan_documents, write_plan
from importer.sources.dynatrace.extract import DynatraceError
from importer.sources.dynatrace.inventory import reinstrument, synthetics
from importer.sources.dynatrace.inventory.extract import (
    environment_url,
    http_getter,
    inventory_token,
)
from importer.sources.dynatrace.inventory.extract import extract as extract_inventory
from importer.sources.dynatrace.inventory.models import (
    load_entities,
    load_locations,
    load_monitors,
    load_slos,
)

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

    @app.command("plan-dynatrace-dashboards")
    def plan_dashboards_command(
        source: Annotated[
            Path,
            typer.Option(
                "--in",
                help="Folder written by extract-dynatrace-inventory, or a dashboard JSON file",
            ),
        ],
        out: Annotated[Path, typer.Option(help="New folder for report.md and plan.json")],
    ) -> None:
        """Say which dashboard tiles and variables convert, and what a person must do.

        Offline. Every tile is exact (every clause maps to NRQL unchanged), draft (one plumbing
        clause dropped, with a note) or manual. Markdown tiles convert. A tile that reads data
        only Dynatrace has (self-monitoring metrics, security events, Smartscape) is flagged
        "no New Relic data source". No New Relic dashboard is created.
        """
        try:
            documents, copies = load_documents(source)
            if not documents:
                raise ValueError(f"no dashboard JSON found in {source}")
            plans = plan_documents(documents, copies)
            write_plan(plans, out)
        except (ValueError, OSError) as exc:
            raise _fail(exc) from None
        t = plans.totals
        typer.echo(
            f"Planned {len(plans.dashboards)} dashboards: {t.data} data tiles "
            f"({t.exact} exact, {t.draft} draft, {t.manual} manual), {t.markdown} markdown tiles, "
            f"{t.variables_exact + t.variables_draft + t.variables_manual} variables "
            f"({t.variables_exact} exact, {t.variables_draft} draft, {t.variables_manual} manual)."
        )
        typer.echo(f"{t.no_data_source} tiles read data New Relic does not have.")
        typer.echo(f"Wrote {out / 'report.md'} and {out / 'plan.json'}")

    @app.command("plan-dynatrace-reinstrumentation")
    def plan_reinstrumentation_command(
        source: Annotated[
            Path,
            typer.Option("--in", help="Folder written by extract-dynatrace-inventory (entities/)"),
        ],
        out: Annotated[Path, typer.Option(help="New folder for report.md and plan.json")],
    ) -> None:
        """Count what Dynatrace monitors and name the New Relic agent or integration for each.

        Offline. Counts per entity type, per technology and per target, the table rows that are
        not confirmed, and what has no migration path (OneAgent deep monitoring, PurePath,
        Davis). Nothing is installed.
        """
        try:
            loaded = load_entities(source)
            if not loaded.items:
                raise ValueError(f"no entities found in {source}")
            plan = reinstrument.plan_reinstrumentation(loaded, load_slos(source))
            reinstrument.write_plan(plan, out)
        except (ValueError, OSError) as exc:
            raise _fail(exc) from None
        typer.echo(
            f"Planned {plan.entities_read} entities in {len(plan.types)} types; "
            f"{len(plan.targets)} New Relic targets."
        )
        typer.echo(f"Wrote {out / 'report.md'} and {out / 'plan.json'}")

    @app.command("plan-dynatrace-synthetics")
    def plan_synthetics_command(
        source: Annotated[
            Path,
            typer.Option(
                "--in", help="Folder written by extract-dynatrace-inventory (synthetics/)"
            ),
        ],
        out: Annotated[Path, typer.Option(help="New folder for report.md and plan.json")],
    ) -> None:
        """Map each synthetic monitor to a New Relic monitor type and its locations.

        Offline. Says which monitors could be created from the JSON alone (simple, simple
        browser, certificate check) and which need a script a person writes. Nothing is
        created.
        """
        try:
            monitors = load_monitors(source)
            if not monitors.items:
                raise ValueError(f"no synthetic monitors found in {source}")
            plan = synthetics.plan_synthetics(
                monitors.items, load_locations(source).items, monitors.skipped
            )
            synthetics.write_plan(plan, out)
        except (ValueError, OSError) as exc:
            raise _fail(exc) from None
        creatable = sum(m.creatable_from_json for m in plan.monitors)
        typer.echo(
            f"Planned {len(plan.monitors)} monitors; {creatable} could be created from the "
            "JSON alone."
        )
        typer.echo(f"Wrote {out / 'report.md'} and {out / 'plan.json'}")
