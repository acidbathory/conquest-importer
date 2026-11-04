"""Shared test setup: no test may read the developer's real .env or write to their out/ folder."""

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("IMPORTER_NO_DOTENV", "1")
    for name in ("NEW_RELIC_ACCOUNT_ID", "NEW_RELIC_API_KEY", "NEW_RELIC_ALLOWED_ACCOUNT_IDS"):
        monkeypatch.delenv(name, raising=False)
    from importer import cli

    monkeypatch.setattr(cli, "LATEST", tmp_path / "latest-run")
