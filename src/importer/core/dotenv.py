"""Read settings from a `.env` file in the current folder, so nobody has to export them by hand.

Only plain KEY=VALUE lines are understood. Variables that are already set in the environment win,
and values are never printed or logged; callers see only the names that were loaded.
"""

import os
import re
from pathlib import Path

_LINE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$")


def load_dotenv_file(path: Path = Path(".env")) -> list[str]:
    """Set variables from `path` that are not already set. Returns the names it set."""
    try:
        text = path.read_text()
    except OSError:
        return []
    loaded: list[str] = []
    for raw in text.splitlines():
        match = _LINE.match(raw)
        if not match or raw.lstrip().startswith("#"):
            continue
        name, value = match.groups()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if name not in os.environ:
            os.environ[name] = value
            loaded.append(name)
    return loaded
