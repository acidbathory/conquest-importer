"""Commands for Grafana notifications and the data path (read side; nothing is created).

- extract-grafana-notifications: read contact points, policies, mute timings, templates and
  ruler rules from a stack, read-only, with secrets dropped at read time.
- plan-grafana-datapath: write guidance files for sending Prometheus and Loki data to New Relic.
- check-grafana-data: ask New Relic (read-only) whether the data behind converted conditions
  arrives.
"""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.grafana.extract import GrafanaError, grafana_url
from importer.sources.grafana.extract import api_token as grafana_api_token
from importer.sources.grafana.extract import validate_host as grafana_host
from importer.sources.grafana.notifications.extract import (
    extract_notifications,
    notifications_getter,
)


def _extract(
    out: Annotated[Path, typer.Option(help="New folder for the export")] = Path(
        "out/grafana-notifications"
    ),
    allow_host: Annotated[
        str | None,
        typer.Option(
            "--allow-host",
            help="Host name of a self-hosted Grafana (https only); must equal the host in "
            "GRAFANA_URL. Without it only https://<stack>.grafana.net is accepted.",
        ),
    ] = None,
) -> None:
    """Read contact points, policies, mute timings, templates and ruler rules from Grafana.

    Read-only: GET calls on a fixed list of paths, never with a query string, so contact points
    are never decrypted and Grafana returns [REDACTED] for every stored secret. Set GRAFANA_URL
    and GRAFANA_SA_TOKEN (a service account token that can read alert provisioning data and
    data sources) in .env or the environment. Secret values, header values, URL paths and
    template text are not written; email addresses are written to contact-points.json only.
    Use a new folder, not the one `extract-grafana` wrote.
    """
    try:
        token = grafana_api_token()
        base = grafana_host(grafana_url(), allow_host)
        with notifications_getter(token, base, allow_host=allow_host) as get:
            result = extract_notifications(get, out)
    except (GrafanaError, ValueError, OSError) as exc:
        typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
        raise typer.Exit(code=1) from None
    typer.echo(
        f"Read {result.contact_points} contact points, {result.mute_timings} mute timings, "
        f"{result.templates} templates, {result.datasources} data sources and "
        f"{result.rule_groups} rule groups."
    )
    for warning in result.warnings:
        typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
    typer.echo(
        f"Written to {printable(str(out), 200)}. Email addresses are in contact-points.json."
    )


def register(app: typer.Typer) -> None:
    app.command("extract-grafana-notifications")(_extract)
