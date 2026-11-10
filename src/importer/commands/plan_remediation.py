"""`plan-remediation`: turn converted output into a to-do list for what did not convert."""

from pathlib import Path
from typing import Annotated

import typer

from importer.core.ids import MAX_INPUT_BYTES
from importer.core.remediation_plan import build_plan, read_items, render_csv, render_markdown
from importer.core.text import printable


def _inside(out: Path, source: Path) -> bool:
    return source.resolve() in (out.resolve(), *out.resolve().parents)


def register(app: typer.Typer) -> None:
    @app.command("plan-remediation")
    def plan_remediation(
        source: Annotated[
            Path,
            typer.Option("--in", help="Output folder of a convert command (or a folder of them)"),
        ],
        out: Annotated[Path, typer.Option(help="Folder for remediation-plan.md and .csv")] = Path(
            "out/remediation"
        ),
    ) -> None:
        """List every item that did not convert, grouped by reason, with the way to finish it.

        Offline and read-only. Reads the IR written by `convert` and
        `convert-grafana-dashboards`, and the report of `convert-grafana-notifications`. Writes
        remediation-plan.md (groups, steps, templates, checklist and the five fixes that unlock
        the most items) and remediation-plan.csv (one row per item) under --out.
        """
        try:
            if not source.is_dir():
                raise ValueError(f"--in {source} is not a folder")
            if _inside(out, source):
                raise ValueError(f"--out {out} is inside --in {source}; choose a separate folder")
            items = read_items(source)
            if not items:
                raise ValueError(f"no unconverted items found in {source}")
            plan = build_plan(items)
            report, table = render_markdown(plan), render_csv(plan)
            if max(len(report), len(table)) > MAX_INPUT_BYTES:
                raise ValueError("the plan is larger than the importer writes; plan fewer items")
            out.mkdir(parents=True, exist_ok=True)
            (out / "remediation-plan.md").write_text(report)
            (out / "remediation-plan.csv").write_text(table)
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        total = sum(i.count for i in plan.items)
        have = sum(i.count for i in plan.items if i.playbook)
        typer.echo(f"{total} items in {len(plan.groups)} groups; {have} have a full playbook.")
        for n, top in enumerate(plan.top_fixes(5), 1):
            typer.echo(f"  {n}. {top.title}: unlocks {top.unlocks} of {top.affects} affected")
        typer.echo(f"Wrote {out / 'remediation-plan.md'} and {out / 'remediation-plan.csv'}.")
