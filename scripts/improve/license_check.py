"""Licence gate and clean-room check. Exits 1 on any failure.

    uv run python scripts/improve/license_check.py [--only deps|sources|clean-room]

deps        every installed distribution must carry an allowlisted licence, or be listed with a
            reason in license-exceptions.toml
sources     every tests/fixtures/**/SOURCES.md must record a licence
clean-room  no tracked file outside docs/ may carry a marker of the source-available licence
            used by a competing project, and no SOURCES.md or manifest may cite its repository
"""

import argparse
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable
from importlib import metadata
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

# One regex per permissive licence family, matched against the whole lower-cased name.
_ALLOWED = [
    r"mit( license)?|expat( license)?",
    r"bsd|bsd license|bsd-[23]-clause|bsd-?[23]|(new|simplified|modified|revised) bsd( license)?",
    r"apache([- ]?(software )?license)?([- ,]*(version )?2(\.0)?)?( license)?",
    r"isc( license)?( \(iscl\))?",
    r"mpl-2\.0|mozilla public license,? (version )?2\.0( \(mpl 2\.0\))?",
    r"psf-2\.0|python-2\.0|psfl?|psf license|python software foundation license",
    r"(the )?unlicense( \(unlicense\))?|0bsd|zlib|cc0-1\.0|public domain",
]
_ALLOWED_RE = [re.compile(p) for p in _ALLOWED]
_TOKENS = re.compile(r"(\(|\)|\s+OR\s+|\s+AND\s+)")
_MAX_FIELD = 200  # a longer License: field is pasted licence text, not a name

# Built in two parts so this file never matches its own scan.
_MARKER = re.compile(r"elastic[ -]licen[cs]e|\belv2\b|elastic-2\.0", re.IGNORECASE)
_UPSTREAM_URL = re.compile(r"(github\.com|githubusercontent\.com)/elastic/", re.IGNORECASE)


def normalise_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _term_allowed(term: str) -> bool:
    t = term.strip().lower()
    if t.startswith("apache") and re.search(r"\b1\.[01]\b", t):
        return False
    return any(p.fullmatch(t) for p in _ALLOWED_RE)


def is_allowed(expression: str) -> bool:
    """True if the (SPDX-like or free-text) licence expression is entirely permissive.

    `A OR B` passes if either side does, `A AND B` needs both; AND binds tighter than OR.
    """
    if _term_allowed(expression):  # a name that contains brackets, e.g. "ISC License (ISCL)"
        return True
    tokens = [t for t in _TOKENS.split(expression.strip()) if t.strip()]
    if not tokens:
        return False
    pos = 0

    def parse_or() -> bool:
        nonlocal pos
        result = parse_and()
        while pos < len(tokens) and tokens[pos].strip() == "OR":
            pos += 1
            right = parse_and()
            result = result or right
        return result

    def parse_and() -> bool:
        nonlocal pos
        result = parse_atom()
        while pos < len(tokens) and tokens[pos].strip() == "AND":
            pos += 1
            right = parse_atom()
            result = result and right
        return result

    def parse_atom() -> bool:
        nonlocal pos
        if pos >= len(tokens):
            return False
        tok = tokens[pos]
        pos += 1
        if tok == "(":
            inner = parse_or()
            if pos < len(tokens) and tokens[pos] == ")":
                pos += 1
            return inner
        if tok.strip() in {"OR", "AND", ")"}:
            return False
        return _term_allowed(tok)

    return parse_or() and pos == len(tokens)


def licence_text(dist: metadata.Distribution) -> str:
    """The licence of an installed distribution: SPDX expression, short License:, or classifiers."""
    meta = dist.metadata
    expression = (meta.get("License-Expression") or "").strip()
    if expression:
        return expression
    field = (meta.get("License") or "").strip()
    if field and field.upper() != "UNKNOWN" and "\n" not in field and len(field) <= _MAX_FIELD:
        return field
    names = [
        c.rsplit("::", 1)[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    names = [n for n in names if n != "OSI Approved"]
    return " AND ".join(names) if names else "UNKNOWN"


def load_exceptions(path: Path) -> dict[str, str]:
    """normalised package name -> reason. A missing file means no exceptions."""
    if not path.is_file():
        return {}
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    out: dict[str, str] = {}
    for entry in data.get("exception", []):
        reason = str(entry.get("reason", "")).strip()
        if not reason:
            raise ValueError(f"exception for {entry.get('package')!r} has no reason")
        out[normalise_name(str(entry["package"]))] = reason
    return out


def check_distributions(
    dists: Iterable[metadata.Distribution], exceptions: dict[str, str]
) -> list[str]:
    problems = []
    for dist in dists:
        name = dist.metadata["Name"]
        if normalise_name(name) in exceptions:
            continue
        text = licence_text(dist)
        if not is_allowed(text):
            problems.append(f"{name}: licence {text!r} is not on the allowlist")
    return sorted(problems)


def check_sources(root: Path) -> list[str]:
    return [
        f"{path.relative_to(root)}: records no licence"
        for path in sorted((root / "tests" / "fixtures").rglob("SOURCES.md"))
        if not re.search(r"licen[cs]e", path.read_text(encoding="utf-8"), re.IGNORECASE)
    ]


def _tracked(root: Path) -> list[str]:
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files", "-z"],  # noqa: S607
        capture_output=True,
        check=True,
    )
    return [p for p in done.stdout.decode().split("\0") if p]


def check_clean_room(root: Path) -> list[str]:
    problems = []
    for name in _tracked(root):
        path = root / name
        if name.startswith("docs/") or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        if _MARKER.search(text):
            problems.append(f"{name}: carries a marker of a source-available competitor licence")
        if path.name in {"SOURCES.md", "manifest.json"} and _UPSTREAM_URL.search(text):
            problems.append(f"{name}: cites a competitor repository as a source")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--only", choices=["deps", "sources", "clean-room"])
    parser.add_argument("--exceptions", type=Path, default=ROOT / "license-exceptions.toml")
    args = parser.parse_args(argv)

    problems: list[str] = []
    if args.only in (None, "deps"):
        dists = [d for d in metadata.distributions() if d.metadata["Name"] != "conquest-importer"]
        problems += check_distributions(dists, load_exceptions(args.exceptions))
        print(f"checked licences of {len(dists)} installed distributions")
    if args.only in (None, "sources"):
        problems += check_sources(ROOT)
    if args.only in (None, "clean-room"):
        problems += check_clean_room(ROOT)
    for p in problems:
        print(f"FAIL: {p}")
    print("license_check: " + (f"{len(problems)} problem(s)" if problems else "ok"))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
