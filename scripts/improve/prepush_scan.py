"""Pre-push scan: no secret value and no private identity string may leave this machine.

Reads the values in `.env` and the strings in the git-ignored `.prepush-identities` (one per
line), then searches the commits about to be pushed (`git log -p <base>..HEAD`) and every
tracked file. Exits 1 on a hit, 0 when clean, 2 on a usage or git error.

The values themselves are never printed: a hit is reported as the `.env` key name (or
"identity #N") plus the commit or file where it was found.

    uv run python scripts/improve/prepush_scan.py [--repo .] [--base origin/main]
"""

import argparse
import subprocess
import sys
from pathlib import Path

MIN_LENGTH = 6  # shorter values ("true", "8080") would match everywhere


def load_env_values(path: Path) -> dict[str, str]:
    """KEY -> value for each usable line of a dotenv file; short or empty values are skipped."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.removeprefix("export ").partition("=")
        value = value.strip().strip("'\"")
        if len(value) >= MIN_LENGTH:
            values[key.strip()] = value
    return values


def load_identities(path: Path) -> list[str]:
    if not path.is_file():
        return []
    lines = (x.strip() for x in path.read_text(encoding="utf-8", errors="replace").splitlines())
    return [x for x in lines if x and not x.startswith("#")]


def _git(repo: Path, *args: str) -> bytes:
    done = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), *args],  # noqa: S607
        capture_output=True,
        check=False,
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {args[0]} failed")
    return done.stdout


def _log_range(repo: Path, base: str) -> list[str]:
    """Commits to scan: base..HEAD, or all of HEAD's history when base does not exist."""
    probe = subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "rev-parse", "--verify", "-q", base],  # noqa: S607
        capture_output=True,
        check=False,
    )
    return [f"{base}..HEAD"] if probe.returncode == 0 else ["HEAD"]


def _hits(text: str, needles: dict[str, str]) -> list[str]:
    return [label for label, value in needles.items() if value in text]


def scan(repo: Path, base: str, needles: dict[str, str]) -> list[str]:
    """Human-readable hit descriptions (labels and places only, never values)."""
    found: list[str] = []
    commits = _git(repo, "log", "--format=%H", *_log_range(repo, base)).decode().split()
    for sha in commits:
        patch = _git(repo, "show", "--format=", "-p", sha).decode("utf-8", errors="replace")
        found += [f"{label} in commit {sha[:10]}" for label in _hits(patch, needles)]
        message = _git(repo, "log", "-1", "--format=%B%an%ae", sha).decode("utf-8", "replace")
        found += [
            f"{label} in commit message/author {sha[:10]}" for label in _hits(message, needles)
        ]
    for name in _git(repo, "ls-files", "-z").decode().split("\0"):
        path = repo / name
        if not name or not path.is_file():
            continue
        text = path.read_bytes().decode("utf-8", errors="replace")
        found += [f"{label} in tracked file {name}" for label in _hits(text, needles)]
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--repo", type=Path, default=Path())
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("--env", type=Path, default=None, help="default: <repo>/.env")
    parser.add_argument(
        "--identities", type=Path, default=None, help="default: <repo>/.prepush-identities"
    )
    args = parser.parse_args(argv)
    repo: Path = args.repo
    env_path = args.env or repo / ".env"
    id_path = args.identities or repo / ".prepush-identities"

    needles = {f"env value {k}": v for k, v in load_env_values(env_path).items()}
    needles.update({f"identity #{i}": v for i, v in enumerate(load_identities(id_path), 1)})
    if not needles:
        print("prepush_scan: nothing to look for (no .env values, no identities); passing")
        return 0
    try:
        hits = scan(repo, args.base, needles)
    except RuntimeError as exc:
        print(f"prepush_scan: {exc}", file=sys.stderr)
        return 2
    for hit in hits:
        print(f"HIT: {hit}")
    if hits:
        print(f"prepush_scan: {len(hits)} hit(s); do not push")
        return 1
    print(f"prepush_scan: clean ({len(needles)} strings checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
