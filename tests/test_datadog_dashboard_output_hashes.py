"""Datadog dashboard output is pinned byte for byte, like the CloudWatch and GCP dashboards.

The hashes in tests/fixtures/datadog-dashboard-output-hashes.json were registered with the
owner's approval on 2026-11-11 (first converter version, 123 of 454 data widgets). A change to any
output file fails this test; regenerating the file needs a human review of the diff, like the
golden files. Regenerate with:  uv run python tests/test_datadog_dashboard_output_hashes.py
"""

import hashlib
import json
import sys
from pathlib import Path

from typer.testing import CliRunner

from importer.cli import app

FIXTURES = Path(__file__).parent / "fixtures"
HASH_FILE = FIXTURES / "datadog-dashboard-output-hashes.json"


def _hash_outputs(out: Path) -> dict[str, str]:
    args = ["convert-dashboards", "--source", "datadog"]
    args += ["--in", str(FIXTURES / "datadog" / "real"), "--out", str(out)]
    res = CliRunner().invoke(app, args)
    assert res.exit_code == 0, res.output
    return {
        f"datadog/{f.relative_to(out).as_posix()}": hashlib.sha256(f.read_bytes()).hexdigest()
        for f in sorted(out.rglob("*"))
        if f.is_file()
    }


def test_every_datadog_dashboard_output_file_matches_the_recorded_hash(tmp_path: Path) -> None:
    want: dict[str, str] = json.loads(HASH_FILE.read_text())
    assert want, "no recorded hashes"
    assert _hash_outputs(tmp_path / "o") == want


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        got = _hash_outputs(Path(tmp) / "o")
    HASH_FILE.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n")
    sys.stdout.write(f"wrote {len(got)} hashes to {HASH_FILE}\n")
