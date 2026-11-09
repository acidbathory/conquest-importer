"""The shared source interface: every registered source behaves the same way."""

from pathlib import Path

import pytest

from importer.core import improve
from importer.core.ir import IRMonitor
from importer.sources import REGISTRY
from importer.sources.base import EXTRA_FIXTURES

ROOT = Path(__file__).parent.parent
CORE = {"cloudwatch", "gcp"}  # sources that must always exist; new ones appear in the registry


def sample_inputs() -> dict[str, Path]:
    """The first fixture folder each source registered for the improvement loop."""
    folders = {k: list(v) for k, v in improve.FIXTURES.items()}
    for name, more in EXTRA_FIXTURES.items():
        folders.setdefault(name, []).extend(f for f in more if f not in folders.get(name, []))
    return {name: ROOT / found[0] for name, found in folders.items() if found}


SAMPLE_INPUT = sample_inputs()


def test_every_source_is_registered_and_has_a_sample_input() -> None:
    assert set(REGISTRY) >= CORE
    assert sorted(SAMPLE_INPUT) == sorted(REGISTRY)
    assert all(path.is_dir() for path in SAMPLE_INPUT.values())


@pytest.mark.parametrize("name", sorted(SAMPLE_INPUT))
def test_load_then_translate_equals_convert(name: str) -> None:
    source = REGISTRY[name]
    raw = source.load(SAMPLE_INPUT[name])
    assert raw, "fixture should not be empty"
    via_steps = source.translate(raw)
    assert all(isinstance(m, IRMonitor) for m in via_steps)
    assert via_steps == source.convert(SAMPLE_INPUT[name])


@pytest.mark.parametrize("name", sorted(REGISTRY))
def test_registry_key_matches_source_name_and_has_help(name: str) -> None:
    assert REGISTRY[name].name == name
    assert REGISTRY[name].input_help


def test_unknown_source_is_rejected_by_cli(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    result = CliRunner().invoke(
        app, ["convert", "--source", "nope", "--in", str(tmp_path), "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1
    assert all(name in result.output for name in REGISTRY)
