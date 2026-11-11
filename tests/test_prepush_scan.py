"""The pre-push scan finds .env values and private identity strings, and never prints them."""

import subprocess
from pathlib import Path

import pytest

from script_helpers import load_script

scan = load_script("prepush_scan")

SECRET = "zq9-not-a-real-secret-value"  # noqa: S105
IDENTITY = "Jane Q. Fakename"


def git(repo: Path, *args: str) -> None:
    subprocess.run(  # noqa: S603
        ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.invalid", *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


def run(repo: Path, env: Path, ids: Path) -> int:
    return int(scan.main(["--repo", str(repo), "--env", str(env), "--identities", str(ids)]))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    r = tmp_path / "repo"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "a.txt").write_text("hello\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "base")
    git(r, "update-ref", "refs/remotes/origin/main", "HEAD")
    return r


def write_env(path: Path) -> Path:
    path.write_text(
        f"# comment\nAPI_KEY={SECRET}\nexport OTHER='quoted-fake-value-1'\nSHORT=ab\nEMPTY=\n"
    )
    return path


def test_env_values_parse_skips_short_and_empty(tmp_path: Path) -> None:
    env = write_env(tmp_path / ".env")
    assert scan.load_env_values(env) == {"API_KEY": SECRET, "OTHER": "quoted-fake-value-1"}


def test_missing_files_give_no_needles(tmp_path: Path) -> None:
    assert scan.load_env_values(tmp_path / "nope") == {}
    assert scan.load_identities(tmp_path / "nope") == []


def test_identities_skip_comments_and_blank(tmp_path: Path) -> None:
    f = tmp_path / "ids"
    f.write_text(f"# who\n\n{IDENTITY}\n")
    assert scan.load_identities(f) == [IDENTITY]


def test_clean_repo_passes(repo: Path, tmp_path: Path) -> None:
    env = write_env(tmp_path / ".env")
    assert run(repo, env, tmp_path / "x") == 0


def test_hit_in_commit_history_fails_without_printing_value(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    env = write_env(tmp_path / ".env")
    (repo / "b.txt").write_text(f"token = {SECRET}\n")
    git(repo, "add", ".")
    git(repo, "commit", "-q", "-m", "oops")
    git(repo, "rm", "-q", "b.txt")
    git(repo, "commit", "-q", "-m", "remove again")
    code = run(repo, env, tmp_path / "x")
    out = capsys.readouterr()
    assert code == 1
    assert "API_KEY" in out.out
    assert SECRET not in out.out + out.err


def test_hit_in_tracked_file_fails(
    repo: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ids = tmp_path / "ids"
    ids.write_text(IDENTITY + "\n")
    (repo / "notes.md").write_text(f"by {IDENTITY}\n")
    git(repo, "add", ".")
    code = run(repo, tmp_path / "none", ids)
    out = capsys.readouterr()
    assert code == 1
    assert "notes.md" in out.out
    assert IDENTITY not in out.out + out.err


def test_untracked_env_file_is_not_scanned(repo: Path, tmp_path: Path) -> None:
    env = write_env(repo / ".env")
    assert run(repo, env, tmp_path / "x") == 0


def test_range_falls_back_when_no_origin_main(tmp_path: Path) -> None:
    r = tmp_path / "r2"
    r.mkdir()
    git(r, "init", "-q", "-b", "main")
    (r / "f").write_text(f"{SECRET}\n")
    git(r, "add", ".")
    git(r, "commit", "-q", "-m", "x")
    env = write_env(tmp_path / ".env")
    assert run(r, env, tmp_path / "x") == 1
