"""`scoreboard`: one honest table of conversion, parity and live evidence, with a stall check."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from importer.core import scoreboard as sb
from importer.core.text import printable


def register(app: typer.Typer) -> None:
    @app.command("scoreboard")
    def scoreboard(
        folders: Annotated[
            list[Path] | None,
            typer.Option("--in", help="Converter output folder (repeat); default: <root>/out"),
        ] = None,
        root: Annotated[Path, typer.Option(help="Repository root")] = Path("."),
        out: Annotated[
            Path | None, typer.Option(help="Where scoreboard.md/json/history go")
        ] = None,
        evidence: Annotated[
            Path | None, typer.Option(help="Live evidence file (default: the tracked one)")
        ] = None,
        compare: Annotated[
            Path | None, typer.Option(help="Previous scoreboard.json to print deltas against")
        ] = None,
        stall_after: Annotated[int, typer.Option(help="Cycles without movement = stall")] = 3,
        ci: Annotated[bool, typer.Option(help="Also ask `gh run list` for the CI status")] = False,
        no_write: Annotated[bool, typer.Option(help="Print only; write no files")] = False,
    ) -> None:
        """Gather what converted, what only has a placeholder, parity and live evidence.

        Offline: reads files already in the repository and in the output folders. Writes
        scoreboard.md, scoreboard.json and appends one dated line to scoreboard-history.csv under
        --out (default docs/improvement). Prints MOVED or NOT MOVED per metric with --compare, and
        says so plainly when nothing moved. Only --ci uses the network (through `gh`).
        """
        try:
            dest = out or root / "docs" / "improvement"
            default_ev = root / "docs" / "improvement" / "live-evidence.json"
            ev = evidence or (default_ev if default_ev.is_file() else None)
            inputs = folders if folders else [p for p in [root / "out"] if p.is_dir()]
            today = datetime.now(UTC).strftime("%Y-%m-%d")
            board = sb.gather(inputs, root, ev, today)
            if ci:
                board["ci"] = sb.ci_status()
            deltas = None
            line = ""
            if compare:
                previous = json.loads(compare.read_text(encoding="utf-8"))
                if not isinstance(previous, dict):
                    raise ValueError(f"{compare} is not a scoreboard.json")
                deltas = sb.compare(previous, board)
                line = sb.verdict_line(deltas, previous)
            history = dest / "scoreboard-history.csv"
            rows = sb.read_history(history)
            if not no_write:
                dest.mkdir(parents=True, exist_ok=True)
                (dest / "scoreboard.md").write_text(sb.render_markdown(board, deltas))
                (dest / "scoreboard.json").write_text(json.dumps(board, indent=2) + "\n")
                sb.append_history(history, today, board["metrics"])
                rows = sb.read_history(history)
        except (ValueError, OSError) as exc:
            typer.echo(f"Error: {printable(str(exc), 400)}", err=True)
            raise typer.Exit(code=1) from None
        typer.echo(sb.render_markdown(board, deltas))
        if line:
            typer.echo(line)
        stall = sb.stall_message(rows, stall_after)
        if stall:
            typer.echo(stall)
