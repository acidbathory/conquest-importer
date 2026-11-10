"""The user guides must not point at files that do not exist or name commands that do not exist.

Checks the runbooks, the script reference, the README and the docs index:
- every relative Markdown link resolves to a file (and to a heading when it has an anchor);
- every `importer <command>` in a code span or code block is a real command, and every `--flag`
  written next to it is a real option of that command.
"""

import re
from pathlib import Path
from typing import Any

import pytest
import typer

from importer.cli import app

ROOT = Path(__file__).resolve().parent.parent
GUIDES = [
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    *sorted((ROOT / "docs" / "user").glob("*.md")),
]
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)\)")
SPAN = re.compile(r"`(importer [^`]*)`")
FENCE_LINE = re.compile(r"^\s*(?:uv run --active )?(importer [^\n]*)$")
FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _commands() -> dict[str, Any]:
    group: Any = typer.main.get_command(app)
    return dict(group.commands)


def _slug(heading: str) -> str:
    text = re.sub(r"[`*_]", "", heading.strip().lower())
    text = re.sub(r"[^\w\- ]", "", text)
    return text.replace(" ", "-")


def _anchors(path: Path) -> set[str]:
    found: set[str] = set()
    in_fence = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif not in_fence and line.startswith("#"):
            found.add(_slug(line.lstrip("#")))
    return found


def _invocations(text: str) -> list[str]:
    """Every `importer ...` command text in code spans and in fenced code blocks."""
    out = list(SPAN.findall(text))
    in_fence = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_fence = not in_fence
        elif in_fence:
            match = FENCE_LINE.match(line)
            if match:
                out.append(match.group(1))
    return out


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: str(p.relative_to(ROOT)))
def test_relative_links_resolve(guide: Path) -> None:
    text = guide.read_text(encoding="utf-8")
    bad: list[str] = []
    for target in LINK.findall(text):
        if re.match(r"[a-z][a-z0-9+.-]*:", target):  # http:, https:, mailto:
            continue
        name, _, anchor = target.partition("#")
        path = guide if not name else (guide.parent / name).resolve()
        if ROOT not in path.parents and path != ROOT:  # a GitHub-relative link such as ../../issues
            continue
        missing = not path.exists()
        if missing or (anchor and path.suffix == ".md" and anchor not in _anchors(path)):
            bad.append(target)
    assert not bad, f"{guide.name} has broken links: {bad}"


@pytest.mark.parametrize("guide", GUIDES, ids=lambda p: str(p.relative_to(ROOT)))
def test_commands_and_flags_are_real(guide: Path) -> None:
    commands = _commands()
    bad: list[str] = []
    for text in _invocations(guide.read_text(encoding="utf-8")):
        words = text.split()
        if len(words) < 2 or words[1].startswith(("-", "<", "[", "(", ".")):
            continue
        name = words[1]
        command = commands.get(name)
        if command is None:
            bad.append(f"unknown command: {name}")
            continue
        known = {opt for param in command.params for opt in (*param.opts, *param.secondary_opts)}
        known.add("--help")
        bad.extend(
            f"{name} has no option {flag}" for flag in FLAG.findall(text) if flag not in known
        )
    assert not bad, f"{guide.name}: {sorted(set(bad))}"
