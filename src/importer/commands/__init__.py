"""Command modules that add themselves to the CLI.

A workstream that needs new commands adds a module here with a `register(app)` function instead
of editing `cli.py`, so parallel branches never touch the same lines. Modules load in name order
when the CLI is built. A module must import from `importer.core` and `importer.sources`, never
from `importer.cli`.
"""

import importlib
import pkgutil

import typer


def load_all(app: typer.Typer) -> list[str]:
    """Import every module in this package and call its `register(app)`; return the names."""
    loaded: list[str] = []
    for info in sorted(pkgutil.iter_modules(__path__), key=lambda m: m.name):
        if info.name.startswith("_"):
            continue
        module = importlib.import_module(f"{__name__}.{info.name}")
        register = getattr(module, "register", None)
        if not callable(register):
            raise RuntimeError(f"importer.commands.{info.name} has no register(app) function")
        register(app)
        loaded.append(info.name)
    return loaded
