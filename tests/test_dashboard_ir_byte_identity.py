"""AWS and GCP dashboards must convert to exactly the bytes they did before the IR grew.

The hashes in tests/fixtures/dashboard-output-hashes.json were taken from integration/all-threads
before the table, raw NRQL, variable and option fields were added. Regenerating the file needs a
human review of the diff, like the golden files.
"""

import hashlib
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
HASHES: dict[str, str] = json.loads((FIXTURES / "dashboard-output-hashes.json").read_text())
SOURCES = {"cloudwatch": "cloudwatch-dashboards", "gcp": "gcp-dashboards"}


def _hash_outputs(source: str, out: Path) -> dict[str, str]:
    args = ["convert-dashboards", "--source", source]
    args += ["--in", str(FIXTURES / SOURCES[source]), "--out", str(out)]
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    return {
        f"{source}/{f.relative_to(out).as_posix()}": hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(out.rglob("*"))
        if f.is_file()
    }


@pytest.mark.parametrize("source", sorted(SOURCES))
def test_every_output_file_is_byte_identical_to_the_recorded_hash(
    source: str, tmp_path: Path
) -> None:
    got = _hash_outputs(source, tmp_path / "o")
    want = {k: v for k, v in HASHES.items() if k.startswith(source + "/")}
    assert want, "no recorded hashes for this source"
    assert got == want
