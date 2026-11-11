"""AWS and GCP dashboards must convert to exactly the bytes they did before the IR grew.

The hashes in tests/fixtures/dashboard-output-hashes.json were taken from integration/all-threads
before the table, raw NRQL, variable and option fields were added. Regenerating the file needs a
human review of the diff, like the golden files.
"""

import hashlib
import json
import random
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.dashboard_ir import IRDashboard
from importer.core.emit.common import CannotEmit
from importer.core.emit.dashboard import dashboard_resource, nerdgraph_dashboard

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


@pytest.mark.parametrize("source", [pytest.param("cloudwatch"), pytest.param("gcp")])
def test_every_output_file_is_byte_identical_to_the_recorded_hash(
    source: str, tmp_path: Path
) -> None:
    got = _hash_outputs(source, tmp_path / "o")
    want = {k: v for k, v in HASHES.items() if k.startswith(source + "/")}
    assert want, "no recorded hashes for this source"
    assert got == want


# --- generated dashboards ------------------------------------------------------------------
# 120 dashboards built from a seeded generator. Their fingerprints (IR JSON, NerdGraph payload and
# Terraform) were taken by running this generator against the emitters of integration/all-threads
# before the IR grew; see tests/fixtures/dashboard-generated-hashes.json. The generator only uses
# names that existed then. Heights stay at 12 or below, titles and labels include hostile text.

GENERATED_COUNT = 120
HOSTILE_TEXT = [
    "plain",
    'quote " and \\ backslash',
    "${var.x} and %{if}",
    "line\nbreak\ttab",
    "x" * 300,
    "unicode é中\U0001f600",
    "'; DROP --",
    "{{not_a_variable}} and }} {{",
    "{{instance}}",
    "",
]
FACETS = ["host", "aws.region", "service.name", "`odd`", "a.5xx"]
ATTRIBUTES = ["cpu", "aws.ec2.CPUUtilization", "gcp.compute.cpu", "aws.apigateway.5XXError"]
VISUALIZATIONS = ["viz.line", "viz.area", "viz.bar", "viz.pie", "viz.billboard"]


def _filter(rng: random.Random) -> dict[str, Any]:
    key = rng.choice(["host", "region", "InstanceId", "aws.account"])
    shape = rng.choice(["eq", "ne", "in", "like", "absent"])
    text = rng.choice([*HOSTILE_TEXT[:4], "web-1", "us-east-1", "{{x}}"])
    out: dict[str, Any] = {"key": key, "value": text}
    if shape == "ne":
        out["negate"] = True
    elif shape == "in":
        out["values"] = [text, "b"]
    elif shape == "like":
        out["wildcard"] = True
        out["value"] = "web-*"
    elif shape == "absent":
        out = {"key": key, "absent": True}
    return out


def _signal(rng: random.Random) -> dict[str, Any]:
    attribute = rng.choice(ATTRIBUTES)
    term = {
        "metric": "m",
        "space_aggregation": "avg",
        "nr_target": {"event": rng.choice(["Metric", "Log"]), "attribute": attribute},
    }
    return {
        "terms": {"a": term},
        "aggregation": rng.choice(["avg", "min", "max", "sum"]),
        "scope": [_filter(rng) for _ in range(rng.randint(0, 3))],
        "group_by": rng.sample(FACETS, rng.randint(0, 2)),
    }


def _widget(rng: random.Random, n: int) -> dict[str, Any]:
    layout = {
        "column": rng.randint(1, 12),
        "row": n * 3 + 1,
        "width": rng.randint(1, 12),
        "height": rng.randint(1, 12),
    }
    kind = rng.choice(["chart"] * 5 + ["markdown", "unsupported"])
    widget: dict[str, Any] = {
        "kind": kind,
        "source_type": rng.choice(HOSTILE_TEXT),
        "title": rng.choice(HOSTILE_TEXT),
        "layout": layout,
        "confidence": rng.choice(["exact", "approximate", "manual"]),
        "reasons": [rng.choice(HOSTILE_TEXT)] if rng.random() < 0.3 else [],
    }
    if kind == "chart":
        widget["visualization"] = rng.choice(VISUALIZATIONS)
        widget["series"] = [
            {
                "label": rng.choice([None, *HOSTILE_TEXT]),
                "signal": _signal(rng),
                "period_seconds": rng.choice([1, 60, 300, 3600, 86400]),
            }
            for _ in range(rng.randint(0, 3))
        ]
    elif kind == "markdown":
        widget["visualization"] = "viz.markdown"
        widget["text"] = rng.choice(HOSTILE_TEXT)
    return widget


def generated_dashboards() -> list[IRDashboard]:
    out = []
    for i in range(GENERATED_COUNT):
        rng = random.Random(i)  # noqa: S311 - test data, not security
        raw = {
            "source_system": rng.choice(["cloudwatch", "gcp", "other"]),
            "source_key": f"gen-{i}",
            "name": rng.choice(HOSTILE_TEXT) or "unnamed",
            "widgets": [_widget(rng, n) for n in range(rng.randint(0, 6))],
            "confidence": rng.choice(["exact"] * 6 + ["approximate", "manual"]),
            "reasons": [],
        }
        out.append(IRDashboard.model_validate(raw))
    return out


def fingerprint(d: IRDashboard) -> str:
    parts = [d.model_dump_json(indent=2)]
    try:
        parts.append(json.dumps(nerdgraph_dashboard(d), indent=2))
        parts.append(dashboard_resource(d, 7))
    except CannotEmit as exc:
        parts.append(f"cannot emit: {exc}")
    return hashlib.sha256("\n--\n".join(parts).encode()).hexdigest()


def test_generated_dashboards_are_byte_identical_to_the_old_emitters() -> None:
    want: dict[str, str] = json.loads((FIXTURES / "dashboard-generated-hashes.json").read_text())
    got = {str(i): fingerprint(d) for i, d in enumerate(generated_dashboards())}
    assert len(got) == GENERATED_COUNT
    assert got == want


def test_the_generated_set_exercises_both_outcomes() -> None:
    dashboards = generated_dashboards()
    emitted = 0
    for d in dashboards:
        try:
            nerdgraph_dashboard(d)
            emitted += 1
        except CannotEmit:
            pass
    assert 60 < emitted < GENERATED_COUNT
    assert sum(len(d.widgets) for d in dashboards) > 200
