"""Shared test setup: no test may read the developer's real .env or write to their out/ folder."""

import re
from collections.abc import Callable
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    for name in ("NEW_RELIC_ACCOUNT_ID", "NEW_RELIC_API_KEY", "NEW_RELIC_ALLOWED_ACCOUNT_IDS"):
        monkeypatch.delenv(name, raising=False)
    from importer import cli

    monkeypatch.setattr(cli, "LATEST", tmp_path / "latest-run")


@pytest.fixture
def help_text() -> Callable[..., str]:
    """Run the CLI and return its output as plain text: no colour, no wrapping, no box drawing.

    Rich styles and wraps help according to the terminal, which differs on CI.
    """

    def run(*args: str) -> str:
        from typer.testing import CliRunner

        from importer.cli import app

        env = {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}
        out = CliRunner().invoke(app, list(args), env=env).output
        out = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", out)
        out = re.sub(r"[│┃─━╭╮╰╯┌┐└┘├┤┬┴┼]", " ", out)
        return " ".join(out.split())

    return run
