"""Prompts behind a small interface, so the menu can be driven by a test as well as a person."""

import getpass
import os
import sys
from typing import Protocol

import typer


class Console(Protocol):
    def say(self, text: str) -> None: ...
    def ask(self, prompt: str) -> str: ...
    def secret(self, prompt: str) -> str: ...


class InputEnded(Exception):
    """The input ran out (closed pipe, Ctrl-D) while a question was waiting."""


class TerminalConsole:
    """Real terminal: `secret` never echoes and nothing here writes to disk."""

    def say(self, text: str) -> None:
        typer.echo(text)

    def ask(self, prompt: str) -> str:
        try:
            return input(prompt + " ").strip()
        except EOFError:
            raise InputEnded from None

    def secret(self, prompt: str) -> str:
        try:
            return getpass.getpass(prompt + " (hidden) ").strip()
        except EOFError:
            raise InputEnded from None


def has_terminal() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def confirm_yes(console: Console, prompt: str) -> bool:
    """True only when the person types the whole word `yes`."""
    return console.ask(f"{prompt} Type 'yes' to continue, anything else cancels:").lower() == "yes"


def ensure_secret(console: Console, name: str, why: str) -> bool:
    """Make sure env var `name` is set for this process; ask with a hidden prompt if not.

    The value lives only in this process's environment: never written to disk or a report.
    Returns False if the person gave nothing.
    """
    if os.environ.get(name, "").strip():
        return True
    value = console.secret(f"{name} is not set. Paste it to {why}, or press Enter to skip:")
    if not value:
        return False
    os.environ[name] = value
    return True
