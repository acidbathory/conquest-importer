"""Command: check that converted queries show the same numbers as the originals."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.parity.model import ParityError
from importer.parity.session import Options, verify


def register(app: typer.Typer) -> None:
    @app.command("verify-parity")
    def verify_parity(
        converted: Annotated[
            Path,
            typer.Option(
                "--in",
                help="Converted folder (has dashboard-ir/ and/or ir/, or dashboards/ and alerts/)",
            ),
        ],
        source_prom: Annotated[
            str,
            typer.Option(
                "--source-prom",
                help="Grafana data source uid or name (read through GRAFANA_URL and "
                "GRAFANA_SA_TOKEN), or a Prometheus URL such as http://127.0.0.1:9090",
            ),
        ],
        nr_account_id: Annotated[int, typer.Option(help="New Relic account id to query")],
        export: Annotated[
            Path | None,
            typer.Option(
                "--export",
                help="The export the conversion started from (the original queries are read "
                "from it); default: the export folder next to a guided-menu converted folder",
            ),
        ] = None,
        source_loki: Annotated[
            str | None,
            typer.Option("--source-loki", help="Like --source-prom, for LogQL count queries"),
        ] = None,
        nr_region: Annotated[str, typer.Option(help="us or eu")] = "us",
        hours: Annotated[float, typer.Option(help="Window length in hours (at most 168)")] = 6,
        step: Annotated[int, typer.Option(help="Step and New Relic bucket, in seconds")] = 60,
        tolerance: Annotated[
            float, typer.Option(help="Relative tolerance per point, for example 0.05")
        ] = 0.05,
        var: Annotated[
            list[str] | None,
            typer.Option("--var", help="Dashboard variable value, name=value (repeatable)"),
        ] = None,
        name_map: Annotated[
            Path | None, typer.Option("--name-map", help="The name map used for the conversion")
        ] = None,
        hints: Annotated[
            list[Path] | None,
            typer.Option("--hints", help="The hints file(s) used for the conversion"),
        ] = None,
        watch: Annotated[
            bool, typer.Option("--watch", help="Repeat, rewriting the report, until Ctrl-C")
        ] = False,
        every: Annotated[
            str, typer.Option(help="Pause between runs with --watch, e.g. 10m")
        ] = "10m",
        out: Annotated[Path, typer.Option(help="Folder for the reports")] = Path("parity"),
        allow_host: Annotated[
            str | None,
            typer.Option(
                "--allow-host", help="Host name of a self-hosted Grafana or Prometheus (https)"
            ),
        ] = None,
    ) -> None:
        """Compare original PromQL/LogQL with the converted NRQL over the same window.

        Read-only on both sides: GET calls on a fixed list of paths to the source, and NRQL
        queries to New Relic (NEW_RELIC_API_KEY). Writes parity.json, parity.md, parity.html
        and verified-labels.json (pass it to `convert-grafana-dashboards --parity`). Exit code
        2 means at least one item is a MISMATCH. The result means something only where the same
        data is in both places; an empty side is reported as NO DATA.
        """
        options = Options(
            converted=converted,
            source_prom=source_prom,
            nr_account_id=nr_account_id,
            export=export,
            source_loki=source_loki,
            nr_region=nr_region,
            hours=hours,
            step=step,
            tolerance=tolerance,
            out=out,
            watch=watch,
            every=every,
            variables=var or [],
            name_map=name_map,
            hints=hints or [],
            allow_host=allow_host,
        )
        try:
            code = verify(options, typer.echo)
        except (ParityError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        if code:
            raise typer.Exit(code=code)
