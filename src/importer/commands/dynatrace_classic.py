"""Commands for the Dynatrace classic source: extract-dynatrace-classic."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.apply import NewRelicAPIError, NewRelicClient
from importer.core.ir import IRMonitor
from importer.core.secrets import MissingSecretError, require_env
from importer.core.text import printable
from importer.sources.dynatrace.classic import data_check
from importer.sources.dynatrace.classic.extract import (
    ClassicError,
    api_token,
    environment_url,
    extract,
    http_getter,
)

MAX_HOURS = 168


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


def check_command(
    alerts: Annotated[Path, typer.Option(help="Output folder of `convert` (has ir/)")] = Path(
        "out/dynatrace-classic/alerts"
    ),
    nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")] = 0,
    nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
    hours: Annotated[int, typer.Option(help="How far back to look (1 to 168)")] = 24,
    out: Annotated[Path | None, typer.Option(help="Also write the markdown report here")] = None,
) -> None:
    """Check that the New Relic signal behind each converted Dynatrace condition has data.

    Read-only count queries. A condition on a metric that never arrives looks healthy and never
    fires; the data prerequisite in each conversion says what to install. Exits with code 2 if
    any converted condition has no data.
    """
    if not nr_account_id or not 1 <= hours <= MAX_HOURS:
        typer.echo("Error: --nr-account-id is required and --hours must be 1 to 168", err=True)
        raise typer.Exit(code=1)
    try:
        monitors = [
            IRMonitor.model_validate_json(p.read_text())
            for p in sorted((alerts / "ir").glob("*.json"))
        ]
        with NewRelicClient(require_env("NEW_RELIC_API_KEY"), nr_region) as client:
            checks = data_check.check_all(client, nr_account_id, monitors, hours)
    except (NewRelicAPIError, MissingSecretError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
        raise typer.Exit(code=1) from None
    text = data_check.render_report(checks, hours)
    typer.echo(text)
    if out:
        out.write_text(text)
    if data_check.has_blind_conditions(checks):
        raise typer.Exit(code=2)


def register(app: typer.Typer) -> None:
    app.command("extract-dynatrace-classic")(extract_command)
    app.command("check-dynatrace-metrics")(check_command)
