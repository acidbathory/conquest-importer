"""The hooks that let parallel branches add commands, taxonomy rules and fixtures without
editing the same shared lines."""

import sys
from pathlib import Path

import pytest
import typer
from typer.testing import CliRunner

from importer import commands
from importer.cli import app
from importer.core import taxonomy
from importer.core.taxonomy import Category, classify, register_taxonomy
from importer.sources import base
from importer.sources.base import register_fixtures


@pytest.fixture
def extra_command_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A throwaway folder that load_all also scans, removed again afterwards."""
    monkeypatch.setattr(commands, "__path__", [*commands.__path__, str(tmp_path)])
    monkeypatch.setattr(sys, "modules", dict(sys.modules))  # forget modules imported by the test
    return tmp_path


def test_load_all_registers_commands_from_modules_in_name_order(extra_command_dir: Path) -> None:
    (extra_command_dir / "zz_second.py").write_text(
        "def register(app):\n    @app.command('second-cmd')\n    def second() -> None:\n"
        "        print('second ran')\n"
    )
    (extra_command_dir / "aa_first.py").write_text(
        "def register(app):\n    @app.command('first-cmd')\n    def first() -> None:\n"
        "        print('first ran')\n"
    )
    fresh = typer.Typer()

    @fresh.command("keep")
    def keep() -> None:
        pass

    assert commands.load_all(fresh) == ["aa_first", "zz_second"]
    result = CliRunner().invoke(fresh, ["second-cmd"])
    assert result.exit_code == 0 and "second ran" in result.output


def test_a_command_module_without_register_is_a_clear_error(extra_command_dir: Path) -> None:
    (extra_command_dir / "broken.py").write_text("X = 1\n")
    with pytest.raises(RuntimeError, match="broken has no register"):
        commands.load_all(typer.Typer())


def test_private_modules_are_skipped(extra_command_dir: Path) -> None:
    (extra_command_dir / "_helpers.py").write_text("raise RuntimeError('must not be imported')\n")
    assert commands.load_all(typer.Typer()) == []


def test_the_real_cli_still_lists_its_commands() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0 and "convert" in result.output and "extract-gcp" in result.output


@pytest.fixture
def clean_taxonomy(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(taxonomy, "_EXTRA", [])


def test_a_registered_rule_wins_over_the_built_in_ones(clean_taxonomy: None) -> None:
    reason = "widget zzz needs a manual step"
    before = classify(reason)
    register_taxonomy(Category.MISSING_MAPPING, r"^widget zzz needs")
    assert classify(reason) is Category.MISSING_MAPPING
    assert before is not Category.MISSING_MAPPING


def test_an_unanchored_rule_is_refused(clean_taxonomy: None) -> None:
    with pytest.raises(ValueError, match="must start with"):
        register_taxonomy(Category.OTHER, r"query")


def test_registering_a_rule_twice_adds_it_once(clean_taxonomy: None) -> None:
    register_taxonomy(Category.OTHER, r"^once only")
    register_taxonomy(Category.OTHER, r"^once only")
    assert len([r for r in taxonomy._EXTRA if r[1].pattern == "^once only"]) == 1


def test_a_rule_that_does_not_match_changes_nothing(clean_taxonomy: None) -> None:
    register_taxonomy(Category.OTHER, r"^never matches anything real")
    assert classify("no new relic mapping for x") is Category.MISSING_MAPPING


def test_register_fixtures_is_idempotent_and_additive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(base, "EXTRA_FIXTURES", {})
    register_fixtures("gcp", "tests/fixtures/a", "tests/fixtures/b")
    register_fixtures("gcp", "tests/fixtures/b", "tests/fixtures/c")
    assert base.EXTRA_FIXTURES["gcp"] == [
        "tests/fixtures/a",
        "tests/fixtures/b",
        "tests/fixtures/c",
    ]


def test_extra_fixture_folders_are_measured(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    from importer.core import improve

    here = Path(__file__).parent.parent
    shutil.copytree(here / "tests" / "fixtures" / "gcp", tmp_path / "only_here")
    monkeypatch.setattr(improve, "EXTRA_FIXTURES", {"gcp": ["only_here", "only_here"]})
    result = improve.measure(tmp_path)["sources"]
    expected = improve.measure(here)["sources"]["gcp"]["total"]
    assert result["gcp"]["total"] == expected  # only the extra folder exists under tmp_path
    assert set(result) == {"gcp"}
