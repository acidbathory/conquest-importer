"""Run the guided menu offline on test fixtures (fake read, fake create) for the README images.

Usage: python scripts/docs/make_menu_run.py <3|4|2|1> <work-dir> <transcript.txt>
Writes the reports under <work-dir> and the menu text to <transcript.txt>. No network, no tenant.
"""

import os
import sys
import tempfile
import webbrowser
from contextlib import contextmanager
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))
sys.path.insert(0, str(ROOT / "src"))

from importer.core.menu import Menu  # noqa: E402
from importer.core.runs import RunStore  # noqa: E402
from menu_helpers import HybridRunner  # noqa: E402
from test_menu import ScriptConsole  # noqa: E402
from test_menu_onego import NamedNR  # noqa: E402


def main(choice: str, work: Path, transcript: Path) -> None:
    os.environ.update(
        NEW_RELIC_ACCOUNT_ID="7",
        NEW_RELIC_ALLOWED_ACCOUNT_IDS="7",
        NEW_RELIC_API_KEY="k",
        GRAFANA_URL="https://example.grafana.net",
        GRAFANA_SA_TOKEN="t",  # noqa: S106
        DYNATRACE_ENV_URL="https://example.apps.dynatrace.com",
        DYNATRACE_API_TOKEN="t",  # noqa: S106
    )
    webbrowser.open = lambda *_a, **_k: True  # type: ignore[assignment]
    console = ScriptConsole([choice, "no", "0"])
    runner = HybridRunner()
    fake = NamedNR({})

    @contextmanager
    def client(_region: str) -> Any:
        yield fake

    with tempfile.TemporaryDirectory() as tmp:
        menu = Menu(
            console,
            runner,
            RunStore(Path(tmp) / "runs"),
            work_dir=work,
            client_factory=client,
            opener=lambda _u: None,
            terminal=lambda: False,
        )
        menu.run()
    transcript.write_text(console.text + "\n", encoding="utf-8")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
