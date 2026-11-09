"""Commands for Grafana dashboards: read them from a stack, and plan what could be converted."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.grafana.dashboards.extract import (
    dashboards_token,
    extract_dashboards,
    http_getter,
)
from importer.sources.grafana.extract import GrafanaError, grafana_url, validate_host


def register(app: typer.Typer) -> None:
    @app.command("extract-grafana-dashboards")
    def extract_grafana_dashboards(
        out: Annotated[Path, typer.Option(help="New folder for the export")] = Path(
            "out/grafana-dashboards"
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
        """Read dashboards, folders and library panels from a Grafana stack.

        Read-only: GET calls only, on a fixed list of paths. Set GRAFANA_URL
        (https://<stack>.grafana.net) and GRAFANA_SA_TOKEN (a service account token that can
        read dashboards, folders and library panels) in .env or the environment; the token is
        never stored or printed. Writes dashboards/<uid>.json, folders.json and
        library-panels.json under --out. Behind a corporate proxy that inspects TLS, set
        SSL_CERT_FILE to a file with the corporate root certificate.
        """
        try:
            token = dashboards_token()
            base = validate_host(grafana_url(), allow_host)
            with http_getter(token, base, allow_host=allow_host) as get:
                result = extract_dashboards(get, out)
        except (GrafanaError, ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(
            f"Read {result.dashboards} dashboards, {result.folders} folders and "
            f"{result.library_panels} library panels."
        )
        for warning in result.warnings:
            typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
        typer.echo("Next:")
        typer.echo(f"  importer plan-grafana-dashboards --in {out} --out {out / 'plan'}")
