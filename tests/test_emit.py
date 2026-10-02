import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.emit.common import CannotEmit
from importer.emit.nerdgraph import MUTATION, nerdgraph_payload
from importer.emit.terraform import hcl_string, resource_block, terraform_file
from importer.models.ir import Confidence, IRMonitor
from importer.models.source import DatadogMonitor
from importer.report import build_report, md_cell
from importer.translate.monitors import load_metric_map, translate_monitor

METRIC_MAP = load_metric_map()
SCRIPTS = Path(__file__).parent.parent / "scripts"


def convert(name: str = "CPU high", query: str | None = None, **kw: Any) -> IRMonitor:
    m = DatadogMonitor(
        id=7,
        name=name,
        type="metric alert",
        query=query or "avg(last_5m):avg:system.cpu.user{*} by {host} > 90",
        **kw,
    )
    return translate_monitor(m, METRIC_MAP)


# --- NerdGraph ------------------------------------------------------------


def test_nerdgraph_payload_shape() -> None:
    ir = convert(options={"thresholds": {"critical": 90, "warning": 80}})
    p = nerdgraph_payload(ir)
    cond = p["variables"]["condition"]
    assert cond["nrql"]["query"] == (
        "SELECT average(cpuUserPercent) FROM SystemSample FACET hostname"
    )
    assert cond["enabled"] is False
    assert [t["priority"] for t in cond["terms"]] == ["CRITICAL", "WARNING"]
    assert cond["terms"][0]["thresholdDuration"] == 300
    assert cond["terms"][0]["operator"] == "ABOVE"


def test_nerdgraph_query_is_constant_and_untrusted_text_stays_in_variables() -> None:
    ir = convert(name='x"} ) { evil } #')
    p = nerdgraph_payload(ir)
    assert p["query"] == MUTATION
    assert "evil" not in p["query"]
    assert p["variables"]["condition"]["name"] == 'x"} ) { evil } #'


def test_no_data_becomes_expiration() -> None:
    ir = convert(options={"notify_no_data": True, "no_data_timeframe": 10})
    cond = nerdgraph_payload(ir)["variables"]["condition"]
    assert cond["expiration"]["expirationDuration"] == 600


def test_manual_monitor_cannot_be_emitted() -> None:
    ir = convert(query="avg(last_5m):avg:custom.unknown{*} > 1")
    assert ir.confidence == Confidence.MANUAL
    with pytest.raises(CannotEmit):
        nerdgraph_payload(ir)


def test_window_outside_range_cannot_be_emitted() -> None:
    ir = convert(query="avg(last_1w):avg:system.cpu.user{*} > 90")
    with pytest.raises(CannotEmit, match="window"):
        nerdgraph_payload(ir)


# --- Terraform ------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("plain", '"plain"'),
        ('a"b', '"a\\"b"'),
        ("a\\b", '"a\\\\b"'),
        ("a\nb", '"a\\nb"'),
        ("${evil}", '"$${evil}"'),
        ("%{if true}x%{endif}", '"%%{if true}x%%{endif}"'),
        ("a\x00b", '"a\\u0000b"'),
    ],
)
def test_hcl_string_escapes(raw: str, expected: str) -> None:
    assert hcl_string(raw) == expected


def test_hostile_name_cannot_break_out_of_hcl_string() -> None:
    hostile = (
        'x"\n}\nresource "null_resource" "pwn" {\n'
        '  provisioner "local-exec" { command = "${evil}" }\n'
    )
    block = resource_block(convert(name=hostile))
    assert 'resource "null_resource"' not in block.replace('\\"', "")
    assert "\nresource " not in block
    assert "${evil}" not in block.replace("$${", "")


def test_terraform_file_has_no_secrets_and_valid_header() -> None:
    tf = terraform_file([convert()])
    assert 'provider "newrelic" {}' in tf
    assert "api_key" not in tf


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_terraform_validate(tmp_path: Path) -> None:  # pragma: no cover
    (tmp_path / "main.tf").write_text(terraform_file([convert()]))
    subprocess.run(["terraform", "init", "-backend=false"], cwd=tmp_path, check=True)  # noqa: S607
    subprocess.run(["terraform", "validate"], cwd=tmp_path, check=True)  # noqa: S607


# --- Report ---------------------------------------------------------------


def test_md_cell_neutralises_markdown_and_newlines() -> None:
    out = md_cell("a|b\n`c`<script>[x](y)")
    assert "\n" not in out
    for ch in "|`<>[]":
        assert ch not in out


def test_report_counts() -> None:
    good = convert()
    bad = convert(query="avg(last_5m):avg:custom.unknown{*} > 1")
    report = build_report([good, bad], {good.source_id})
    assert "Monitors read: **2**" in report
    assert "Emitted as conditions: **1**" in report


# --- End to end -----------------------------------------------------------


def test_end_to_end_on_sample_definitions(tmp_path: Path) -> None:
    defs = json.loads((SCRIPTS / "sample_monitors.json").read_text())
    src = tmp_path / "dd"
    src.mkdir()
    for i, d in enumerate(defs, start=1000):
        d = {
            **d,
            "id": i,
            "query": d["query"].replace("{ref:cpu_high}", "1").replace("{ref:mem_high}", "2"),
        }
        d.pop("ref", None)
        (src / f"{i}.json").write_text(json.dumps(d))
    out = tmp_path / "out"
    result = CliRunner().invoke(app, ["convert", "--in", str(src), "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "report.md").exists()
    assert (out / "main.tf").read_text().count("newrelic_nrql_alert_condition") >= 5
    emitted = list((out / "nerdgraph").glob("*.json"))
    assert 5 <= len(emitted) < 20
    report = (out / "report.md").read_text()
    assert "Monitors read: **20**" in report
    for c in ("exact", "approximate", "manual", "unsupported"):
        assert f"| {c} |" in report


def test_condition_name_removes_control_characters_and_is_bounded() -> None:
    from importer.emit.common import MAX_NAME_LENGTH, condition_name

    assert condition_name("a\nb\tc\x1b[31m d") == "abc[31m d"
    assert len(condition_name("x" * 500)) == MAX_NAME_LENGTH
    ir = convert(name="Line one\nline two")
    assert nerdgraph_payload(ir)["variables"]["condition"]["name"] == "Line oneline two"
    assert '"Line oneline two"' in resource_block(ir)
