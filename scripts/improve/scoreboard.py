"""Run the scoreboard without installing the package: `uv run python scripts/improve/scoreboard.py`.

Same as `uv run importer scoreboard`; all arguments are passed through.
"""

import sys

from importer.cli import app

if __name__ == "__main__":
    app(["scoreboard", *sys.argv[1:]])
