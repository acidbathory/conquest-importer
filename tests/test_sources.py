"""The shared source interface: every registered source behaves the same way."""

from pathlib import Path

import pytest

from importer.core.ir import IRMonitor
from importer.sources import REGISTRY

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE_INPUT = {
    "datadog": FIXTURES / "datadog",
    "cloudwatch": FIXTURES / "cloudwatch-real",
    "gcp": FIXTURES / "gcp",
}


def test_every_source_is_registered_and_has_a_sample_input() -> None:
    assert sorted(REGISTRY) == ["cloudwatch", "datadog", "gcp"]
    assert sorted(SAMPLE_INPUT) == sorted(REGISTRY)


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
    assert "cloudwatch" in result.output and "datadog" in result.output
