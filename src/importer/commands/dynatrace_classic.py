"""Commands for the Dynatrace classic source: extract-dynatrace-classic."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.dynatrace.classic.extract import (
    ClassicError,
    api_token,
    environment_url,
    extract,
    http_getter,
)


def extract_command(
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path(
        "out/dynatrace-classic"
    ),
) -> None:
    """Read metric events, Davis detectors and alerting settings from a Dynatrace tenant.

    Read-only GET requests for a fixed list of Settings schemas. Set DYNATRACE_ENV_URL
    (https://<tenant id>.apps.dynatrace.com, or .live.dynatrace.com for a classic tenant) and
    DYNATRACE_API_TOKEN (a platform token with settings:objects:read and settings:schemas:read, or
    an API token with settings.read) in .env or the environment. The token is never stored or
    printed. Notification secrets, webhook URLs and recipients are dropped before the export is
    written.
    """
    try:
        token, env_url = api_token(), environment_url()
        with http_getter(token, env_url) as get:
            result = extract(get, env_url, out)
    except (ClassicError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    total = sum(result.counts.values())
    typer.echo(f"Read {total} objects from {len(result.counts)} schemas.")
    for schema, count in sorted(result.counts.items()):
        typer.echo(f"  {count:5d}  {printable(schema, 80)}")
    for warning in result.warnings:
        typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
    typer.echo("Next:")
    typer.echo(f"  importer convert --source dynatrace-classic --in {out} --out {out / 'alerts'}")
    typer.echo(f"  then read {out / 'alerts' / 'report.md'}")


def register(app: typer.Typer) -> None:
    app.command("extract-dynatrace-classic")(extract_command)
