"""`plan-grafana-quickstarts`: which Grafana dashboards have a native New Relic replacement."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable
from importer.sources.grafana.dashboards.load import load_estate
from importer.sources.grafana.dashboards.quickstart_map import (
    plan_quickstarts,
    quickstart_json,
    quickstart_report,
    read_gnet_ids,
)


def _inside(out: Path, source: Path) -> bool:
    folder = source if source.is_dir() else source.parent
    return folder.resolve() in (out.resolve(), *out.resolve().parents)


def register(app: typer.Typer) -> None:
    @app.command("plan-grafana-quickstarts")
    def plan_grafana_quickstarts(
        source: Annotated[
            Path, typer.Option("--in", help="File or folder from `extract-grafana-dashboards`")
        ],
        out: Annotated[Path, typer.Option(help="Folder for quickstart-plan.md and .json")] = Path(
            "out/grafana-quickstarts"
        ),
    ) -> None:
        """Say which dashboards could be replaced by a New Relic quickstart instead of converted.

        Offline and read-only. Matches by grafana.com id, title and metric names against
        quickstarts/*.json read from the public newrelic-quickstarts repository, and checks
        that the metric names really overlap. Writes quickstart-plan.md and quickstart-plan.json
        under --out. Names, units and dimensions differ from the Grafana dashboard.
        """
        try:
            if _inside(out, source):
                raise ValueError(f"--out {out} is inside --in {source}; choose a separate folder")
            estate = load_estate(source)
            if not estate.dashboards:
                raise ValueError(f"no Grafana dashboards found in {source}")
            plan = plan_quickstarts(estate, read_gnet_ids(source))
            report, data = quickstart_report(plan), quickstart_json(plan)
            if max(len(report), len(data)) > MAX_INPUT_BYTES:
                raise ValueError(
                    "the plan is larger than the importer writes; plan fewer dashboards"
                )
            out.mkdir(parents=True, exist_ok=True)
            (out / "quickstart-plan.md").write_text(report)
            (out / "quickstart-plan.json").write_text(data)
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(f"{plan.coverage}.")
        typer.echo(
            f"Replace {plan.replace}, replace after changing collection {plan.change}, "
            f"related only {plan.related}."
        )
        typer.echo(f"Wrote {out / 'quickstart-plan.md'} and {out / 'quickstart-plan.json'}.")
