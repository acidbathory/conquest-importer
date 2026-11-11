"""`cookbook`: write the remediation cookbook as an HTML page (offline, read-only)."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.cookbook_html import render_cookbook_html
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.remediation_plan import build_plan, read_items
from importer.core.report_brand import PAIRS
from importer.core.text import printable


def register(app: typer.Typer) -> None:
    @app.command("cookbook")
    def cookbook_command(
        source_dir: Annotated[
            Path | None,
            typer.Option("--in", help="Converted folder: show only the blockers found in it"),
        ] = None,
        source: Annotated[str, typer.Option(help="Source for the header logos")] = "grafana",
        all_: Annotated[
            bool, typer.Option("--all", help="The full cookbook, nothing personalised")
        ] = False,
        out: Annotated[Path, typer.Option(help="The HTML file to write")] = Path("cookbook.html"),
    ) -> None:
        """Write the remediation cookbook as one HTML page.

        Without --in (or with --all) the page is the full cookbook. With --in it holds only the
        blockers your converted output actually hit, with counts and the affected items. Offline;
        no scripts and no external assets in the page.
        """
        try:
            if source not in PAIRS:
                raise ValueError(f"--source must be one of {', '.join(sorted(PAIRS))}")
            plan = None
            if source_dir is not None and not all_:
                if not source_dir.is_dir():
                    raise ValueError(f"--in {source_dir} is not a folder")
                plan = build_plan(read_items(source_dir))
            html = render_cookbook_html(plan, source=source)
            if len(html) > MAX_INPUT_BYTES:
                raise ValueError("the page is larger than the importer writes")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(html, encoding="utf-8")
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(f"Wrote {out}")
