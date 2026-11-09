import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.core.emit.common import CannotEmit
from importer.core.emit.nerdgraph import MUTATION, nerdgraph_payload
from importer.core.emit.terraform import hcl_string, resource_block, terraform_file
from importer.core.ir import (
    Aggregation,
    Confidence,
    IRMonitor,
    MetricSignal,
    MetricTerm,
    NoData,
    NrTarget,
    Operator,
    Severity,
    Signal,
    SignalKind,
    Threshold,
)
from importer.core.report import build_report, md_cell

FIXTURES = Path(__file__).parent / "fixtures"


def convert(
    name: str = "CPU high",
    *,
    window: int = 300,
    notify_no_data: bool = False,
    no_data_minutes: int = 10,
    confidence: Confidence = Confidence.APPROXIMATE,
    warning: float | None = None,
) -> IRMonitor:
    """A converted CPU monitor, built directly so these tests cover only the emitters."""
    term = MetricTerm(
        metric="cpu",
        space_aggregation=Aggregation.AVG,
        nr_target=NrTarget(event="Metric", attribute="aws.ec2.CPUUtilization"),
    )
    thresholds = [Threshold(severity=Severity.CRITICAL, operator=Operator.ABOVE, value=90)]
    if warning is not None:
        thresholds.append(
            Threshold(severity=Severity.WARNING, operator=Operator.ABOVE, value=warning)
        )
    return IRMonitor(
        source_system="cloudwatch",
        source_id=7,
        source_type="metric alarm",
        name=name,
        signal=Signal(
            kind=SignalKind.METRIC,
            raw_query=name,
            window_seconds=window,
            aggregation_window_seconds=60,
            metric=MetricSignal(
                terms={"a": term}, aggregation=Aggregation.AVG, group_by=["InstanceId"]
            ),
        ),
        thresholds=thresholds,
        no_data=NoData(notify=notify_no_data, timeout_minutes=no_data_minutes),
        confidence=confidence,
    )


# --- NerdGraph ------------------------------------------------------------


def test_nerdgraph_payload_shape() -> None:
    ir = convert(warning=80)
    p = nerdgraph_payload(ir)
    cond = p["variables"]["condition"]
    assert cond["nrql"]["query"] == (
        "SELECT average(aws.ec2.CPUUtilization) FROM Metric FACET InstanceId"
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
    ir = convert(notify_no_data=True, no_data_minutes=10)
    cond = nerdgraph_payload(ir)["variables"]["condition"]
    assert cond["expiration"]["expirationDuration"] == 600


def test_manual_monitor_cannot_be_emitted() -> None:
    ir = convert(confidence=Confidence.MANUAL)
    assert ir.confidence == Confidence.MANUAL
    with pytest.raises(CannotEmit):
        nerdgraph_payload(ir)


def test_window_outside_range_cannot_be_emitted() -> None:
    ir = convert(window=7 * 86400)
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
    init = subprocess.run(
        ["terraform", "init", "-backend=false"],  # noqa: S607
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode and "provider" in init.stderr and "registry" in init.stderr:
        pytest.skip("the Terraform provider could not be downloaded (outside network error)")
    assert init.returncode == 0, init.stderr
    subprocess.run(["terraform", "validate"], cwd=tmp_path, check=True)  # noqa: S607


# --- Report ---------------------------------------------------------------


def test_md_cell_neutralises_markdown_and_newlines() -> None:
    out = md_cell("a|b\n`c`<script>[x](y)")
    assert "\n" not in out
    for ch in "|`<>[]":
        assert ch not in out


def test_report_counts() -> None:
    good = convert()
    bad = convert(confidence=Confidence.MANUAL)
    report = build_report([good, bad], {good.source_id}, "cloudwatch")
    assert "Monitors read: **2**" in report
    assert "Emitted as conditions: **1**" in report


# --- End to end -----------------------------------------------------------


def test_end_to_end_on_real_cloudwatch_alarms(tmp_path: Path) -> None:
    out = tmp_path / "out"
    args = ["convert", "--source", "cloudwatch", "--in", str(FIXTURES / "cloudwatch-real")]
    result = CliRunner().invoke(app, [*args, "--out", str(out)])
    assert result.exit_code == 0, result.output
    assert (out / "report.md").exists()
    assert (out / "main.tf").read_text().count("newrelic_nrql_alert_condition") >= 5
    assert len(list((out / "nerdgraph").glob("*.json"))) >= 5
    report = (out / "report.md").read_text()
    assert "Monitors read: **9**" in report
    for c in ("exact", "approximate", "manual", "unsupported"):
        assert f"| {c} |" in report


def test_condition_name_removes_control_characters_and_is_bounded() -> None:
    from importer.core.emit.common import MAX_NAME_LENGTH, condition_name

    assert condition_name("a\nb\tc\x1b[31m d") == "abc[31m d"
    assert len(condition_name("x" * 500)) == MAX_NAME_LENGTH
    ir = convert(name="Line one\nline two")
    assert nerdgraph_payload(ir)["variables"]["condition"]["name"] == "Line oneline two"
    assert '"Line oneline two"' in resource_block(ir)
