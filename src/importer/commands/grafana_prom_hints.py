"""Command: read metric types and series labels from a Prometheus data source for the converter.

Read-only: GET calls through Grafana's data source proxy, for only the metrics the dashboards
name, under hard caps (see `sources/grafana/prom_hints.py`). Writes a hints file that
`convert-grafana-dashboards --hints` reads.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.grafana.extract import GrafanaError, api_token, grafana_url, validate_host
from importer.sources.grafana.prom_hints import (
    find_datasource,
    hint_getter,
    hints_document,
    metric_names,
    read_hints,
)


def register(app: typer.Typer) -> None:
    @app.command("grafana-prom-hints")
    def grafana_prom_hints(
        datasource: Annotated[
            str, typer.Option("--datasource", help="Uid or name of the Prometheus data source")
        ],
        metrics_from: Annotated[
            Path,
            typer.Option(
                "--metrics-from",
                help="Folder from `extract-grafana-dashboards`, or converted dashboard IR",
            ),
        ],
        out: Annotated[Path, typer.Option(help="Hints file to write (must not exist)")] = Path(
            "hints.json"
        ),
        allow_host: Annotated[
            str | None,
            typer.Option("--allow-host", help="Host of a self-hosted Grafana (https only)"),
        ] = None,
    ) -> None:
        """Read each metric's exact type and series labels from Prometheus.

        Read-only: GET only, allow-listed paths, capped requests, series and bytes. Set
        GRAFANA_URL and GRAFANA_SA_TOKEN (read access to the data source) in .env or the
        environment. Behind a corporate proxy that inspects TLS, set SSL_CERT_FILE to a file with
        the corporate root certificate; verification is never turned off. Then run
        `convert-grafana-dashboards --hints <file>`; a metric left out keeps the suffix guess.
        """
        try:
            if out.exists():
                raise GrafanaError(f"{out} already exists; choose a new file")
            wanted = metric_names(metrics_from)
            if not wanted:
                raise GrafanaError(f"no Prometheus metric names found in {metrics_from}")
            token = api_token()
            base = validate_host(grafana_url(), allow_host)
            with hint_getter(token, base, allow_host=allow_host) as get:
                uid = find_datasource(get, datasource)
                hints = read_hints(get, uid, wanted)
            out.write_text(json.dumps(hints_document(hints), indent=2) + "\n")
        except (GrafanaError, ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(
            f"Wrote {out}: {len(hints.types)} types and {len(hints.series)} series keys for "
            f"{len(wanted)} metrics ({hints.requested} requests). "
            f"{len(hints.unknown)} not known to Prometheus (suffix guess stays)."
        )
        if hints.truncated:
            typer.echo(f"  {len(hints.truncated)} gauges had too many series for a key.")
        if hints.skipped:
            typer.echo(f"  {hints.skipped} metrics were over the cap and left out.")
        for warning in hints.warnings:
            typer.echo(f"  Warning: {printable(warning, 300)}", err=True)
        typer.echo(f"Next:\n  importer convert-grafana-dashboards --in <extract> --hints {out}")
