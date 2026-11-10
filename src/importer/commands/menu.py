"""Commands for the guided menu: menu, runs, rollback-run."""

from typing import Annotated

import typer

from importer.core.console import TerminalConsole, has_terminal
from importer.core.menu import Menu, Runner
from importer.core.runs import RunStore
from importer.core.text import printable

NEEDS_TERMINAL = (
    "Error: this needs a terminal to ask you questions. Run it from an interactive shell, or "
    "use the individual commands (see 'importer --help')."
)


def make_runner(app: typer.Typer) -> Runner:
    """Run one of the CLI's own commands in-process and return its exit code."""
    command = typer.main.get_command(app)

    def run(args: list[str]) -> int:
        try:
            code = command.main(args=args, prog_name="importer", standalone_mode=False)
        except typer.Exit as exc:
            return int(exc.exit_code)
        except typer.Abort:
            return 1
        except Exception as exc:  # a bad option must not kill the menu
            typer.echo(f"Error: {printable(str(exc), 300)}", err=True)
            return 1
        return code if isinstance(code, int) else 0

    return run


def register(app: typer.Typer) -> None:
    @app.command("menu")
    def menu_command(
        no_open: Annotated[
            bool,
            typer.Option(
                "--no-open", help="Do not open the HTML reports in a browser (paths are printed)."
            ),
        ] = False,
    ) -> None:
        """Numbered menu: GCP, AWS, Grafana, Dynatrace, roll back a run, check setup, sample."""
        if not has_terminal():
            typer.echo(NEEDS_TERMINAL, err=True)
            raise typer.Exit(code=1)
        raise typer.Exit(
            code=Menu(
                TerminalConsole(), make_runner(app), RunStore(), open_reports=not no_open
            ).run()
        )

    @app.command("runs")
    def runs_command() -> None:
        """List the runs that created objects in New Relic (newest first). Read-only, offline."""
        runs = RunStore().list()
        if not runs:
            typer.echo("There are no recorded runs.")
            return
        for r in runs:
            typer.echo(
                f"{r.run_id}  {r.source:10} account {r.account_id}  {r.status:18} "
                f"{len(r.pending())} object(s) left"
            )

    @app.command("rollback-run")
    def rollback_run_command(
        real_migration: Annotated[
            bool,
            typer.Option(
                "--real-migration",
                help="Also allow dashboards of a real (non-sandbox) run to be deleted by kind.",
            ),
        ] = False,
    ) -> None:
        """Pick a recorded run, preview what would be deleted, and delete it after you type yes."""
        if not has_terminal():
            typer.echo(NEEDS_TERMINAL, err=True)
            raise typer.Exit(code=1)
        menu = Menu(TerminalConsole(), make_runner(app), RunStore(), real_migration=real_migration)
        try:
            menu.rollback_menu()
        except KeyboardInterrupt:
            typer.echo("\nStopped.")
            raise typer.Exit(code=130) from None
