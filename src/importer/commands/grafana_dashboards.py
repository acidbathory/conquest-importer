"""Commands for Grafana dashboards: read them from a stack, and plan what could be converted."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable
from importer.sources.grafana.dashboards.extract import (
    dashboards_token,
    extract_dashboards,
    http_getter,
)
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.plan import build_plan
from importer.sources.grafana.dashboards.report import build_report, plan_json
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
        typer.echo(f"  importer plan-grafana-dashboards --in {out} --out {out}-plan")

    @app.command("plan-grafana-dashboards")
    def plan_grafana_dashboards(
        source: Annotated[
            Path, typer.Option("--in", help="File or folder from `extract-grafana-dashboards`")
        ],
        out: Annotated[Path, typer.Option(help="Folder for report.md and plan.json")] = Path(
            "out/grafana-dashboards-plan"
        ),
    ) -> None:
        """Say how much of a set of Grafana dashboards could convert, and why the rest could not.

        Offline and read-only: nothing is converted or sent anywhere. Writes report.md and
        plan.json under --out. The tiers are described in docs/what-migrates.md.
        """
        try:
            plan = build_plan(load_estate(source))
            if not plan.dashboards:
                raise ValueError(f"no Grafana dashboards found in {source}")
            report, data = build_report(plan), plan_json(plan)
            if max(len(report), len(data)) > MAX_INPUT_BYTES:
                raise ValueError(
                    "the plan is larger than the importer writes; plan fewer dashboards"
                )
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.md").write_text(report)
            (out / "plan.json").write_text(data)
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        t = plan.totals
        typer.echo(
            f"Planned {t.dashboards} dashboards ({t.unreadable} could not be read), "
            f"{t.panels} panels: T1 {t.t1}, T2 {t.t2}, T3 {t.t3}, T3 with extra widget types "
            f"{t.t3_extra}."
        )
        typer.echo(f"Wrote {out / 'report.md'} and {out / 'plan.json'}.")
