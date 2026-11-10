"""Command: rank Grafana dashboards before converting them (offline, read-only)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from importer.core.text import printable
from importer.sources.grafana.triage.pipeline import run_triage
from importer.sources.grafana.triage.report import build_json, build_markdown
from importer.sources.grafana.triage.usage import UsageError, load_usage


def _inside(out: Path, source: Path) -> bool:
    folder = source if source.is_dir() else source.parent
    return folder.resolve() in (out.resolve(), *out.resolve().parents)


def register(app: typer.Typer) -> None:
    @app.command("triage-grafana-dashboards")
    def triage_grafana_dashboards(
        source: Annotated[
            Path, typer.Option("--in", help="File or folder from `extract-grafana-dashboards`")
        ],
        out: Annotated[Path, typer.Option(help="Folder for triage-report.md and .json")] = Path(
            "out/grafana-triage"
        ),
        usage: Annotated[
            Path | None,
            typer.Option(
                "--usage",
                help="JSON or CSV with dashboard uid, views_30d and last_viewed, exported from "
                "Grafana usage insights. Without it only weak hints are used.",
            ),
        ] = None,
    ) -> None:
        """Rank dashboards into migrate now, migrate later, quickstart or skip.

        Offline and read-only. Signals come from the extract (folder, tags, panel types, data
        sources, title words, duplicates, empty dashboards); widget counts come from the
        existing converter. The headline compares the widgets converted for the dashboards
        worth migrating now with the share for all of them.
        """
        try:
            if _inside(out, source):
                raise ValueError(f"--out {out} is inside --in {source}; choose a separate folder")
            views = load_usage(usage) if usage else {}
            result = run_triage(source, views, today=datetime.now(UTC).date())
            if not result.rows:
                raise ValueError(f"no Grafana dashboards found in {source}")
            out.mkdir(parents=True, exist_ok=True)
            (out / "triage-report.md").write_text(build_markdown(result))
            (out / "triage-report.json").write_text(build_json(result))
        except (ValueError, OSError, UsageError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(result.headline)
        typer.echo(f"Wrote {out / 'triage-report.md'} and {out / 'triage-report.json'}.")
