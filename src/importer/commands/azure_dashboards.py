"""`plan-azure-dashboards`: how much of an Azure dashboard export converts, offline.

Converts workbooks and portal dashboards (see `sources/azure/dashboards.py`) and says how many
widgets became charts or text and how many stay placeholders, by reason and by data path. Nothing
is created and nothing is sent anywhere; `convert-dashboards --source azure` writes the NerdGraph
and Terraform output.
"""

import json
from pathlib import Path
from typing import Annotated

import typer

from importer.core.dashboard_report import build_dashboard_report
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import nerdgraph_dashboard
from importer.core.ids import MAX_INPUT_BYTES
from importer.core.text import printable
from importer.sources import DASHBOARD_REGISTRY
from importer.sources.azure.dashboard_measure import Measure, measure


def _plan_json(m: Measure) -> str:
    data = {
        "totals": {
            "dashboards": m.dashboards,
            "widgets": m.widgets,
            "converted": m.converted,
            "placeholders": m.placeholders,
        },
        "converted_by_type": dict(sorted(m.by_type.items())),
        "converted_by_data_path": dict(sorted(m.by_data_path.items())),
        "placeholders_by_reason": dict(m.by_reason.most_common()),
    }
    return json.dumps(data, indent=2) + "\n"


def register(app: typer.Typer) -> None:
    @app.command("plan-azure-dashboards")
    def plan_azure_dashboards(
        source: Annotated[
            Path, typer.Option("--in", help="File or folder from `extract-azure --dashboards`")
        ],
        out: Annotated[Path, typer.Option(help="Folder for report.md and plan.json")] = Path(
            "out/azure-dashboards-plan"
        ),
    ) -> None:
        """Say how many Azure workbook items and dashboard parts convert, and why the rest do not.

        Offline and read-only: nothing is created or sent anywhere. Writes report.md and plan.json
        under --out. Placeholders ('Not migrated') are counted apart and never as converted.
        """
        try:
            dashboards = DASHBOARD_REGISTRY["azure"].convert(source)
            if not dashboards:
                raise ValueError(f"no workbooks or dashboards found in {source}")
            emitted = set()
            for d in dashboards:
                try:
                    nerdgraph_dashboard(d)
                    emitted.add(d.source_key)
                except CannotEmit:
                    continue
            m = measure(dashboards)
            report = build_dashboard_report(dashboards, emitted, "azure")
            data = _plan_json(m)
            if max(len(report), len(data)) > MAX_INPUT_BYTES:
                raise ValueError("the plan is larger than the importer writes")
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.md").write_text(report)
            (out / "plan.json").write_text(data)
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(
            f"Read {m.dashboards} dashboards with {m.widgets} widgets: {m.converted} converted, "
            f"{m.placeholders} placeholders ('Not migrated')."
        )
        for path, count in sorted(m.by_data_path.items()):
            typer.echo(f"  data path {path}: {count}")
        typer.echo(f"Wrote {out / 'report.md'} and {out / 'plan.json'}.")
