"""Terraform that forwards the log groups behind converted log conditions to New Relic."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.cloudwatch.log_ingest import (
    ENDPOINTS,
    LogIngestConfig,
    log_groups_from_ir,
    log_ingest_files,
)


def files(**fields: object) -> dict[str, str]:
    return log_ingest_files(LogIngestConfig.model_validate({"log_groups": ["/app/web"], **fields}))


def test_the_stream_follows_new_relics_settings() -> None:
    main = files()["main.tf"]
    assert ENDPOINTS["us"] in main
    assert 'content_encoding = "GZIP"' in main and "buffering_size     = 1\n" in main
    assert 'log_groups = ["/app/web"]' in main
    assert 'resource "aws_cloudwatch_log_subscription_filter" "newrelic"' in main
    assert "firehose:PutRecordBatch" in main and 'identifiers = ["logs.amazonaws.com"]' in main


def test_eu_uses_the_eu_endpoint_and_groups_are_sorted_and_unique() -> None:
    out = files(nr_region="eu", log_groups=["/b", "/a", "/a"])["main.tf"]
    assert ENDPOINTS["eu"] in out and 'log_groups = ["/a", "/b"]' in out


def test_the_license_key_is_a_sensitive_variable_and_never_written() -> None:
    out = files()
    assert "sensitive   = true" in out["variables.tf"]
    assert (
        "NRAK" not in "".join(out.values())
        and 'default = "' not in out["variables.tf"].split("newrelic_license_key")[1].split("}")[0]
    )


@pytest.mark.parametrize(
    "fields",
    [
        {"log_groups": []},
        {"log_groups": ["/a b"]},
        {"log_groups": ['/a"; evil']},
        {"log_groups": ["/a"] * 0 + [f"/g{i}" for i in range(51)]},
        {"name_prefix": "Bad Prefix"},
        {"aws_region": "mars"},
    ],
)
def test_hostile_or_invalid_input_is_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValueError, match=r"log group|name_prefix|aws_region|at most|at least"):
        LogIngestConfig.model_validate({"log_groups": ["/ok"], **fields})


def test_only_converted_log_conditions_contribute_log_groups(tmp_path: Path) -> None:
    def ir(name: str, kind: str, confidence: str, group: str) -> None:
        (tmp_path / f"{name}.json").write_text(
            json.dumps(
                {
                    "confidence": confidence,
                    "signal": {
                        "kind": kind,
                        "count": {"filters": [{"key": "logGroup", "value": group}]},
                    },
                }
            )
        )

    ir("a", "log", "approximate", "/converted")
    ir("b", "log", "manual", "/not-converted")
    ir("c", "metric", "approximate", "/metric")
    assert log_groups_from_ir(tmp_path) == ["/converted"]


def test_the_command_writes_files_for_the_groups_in_a_convert_folder(tmp_path: Path) -> None:
    alerts = tmp_path / "alerts"
    (alerts / "ir").mkdir(parents=True)
    (alerts / "ir" / "x.json").write_text(
        json.dumps(
            {
                "confidence": "approximate",
                "signal": {
                    "kind": "log",
                    "count": {"filters": [{"key": "logGroup", "value": "/g"}]},
                },
            }
        )
    )
    out = tmp_path / "o"
    res = CliRunner().invoke(app, ["ingest-logs-setup", "--alerts", str(alerts), "--out", str(out)])
    assert res.exit_code == 0, res.output
    assert "forwarding 1 log groups" in res.output
    assert (out / "main.tf").exists() and "TF_VAR_newrelic_license_key" in res.output


def test_the_command_refuses_to_run_with_nothing_to_forward(tmp_path: Path) -> None:
    res = CliRunner().invoke(app, ["ingest-logs-setup", "--out", str(tmp_path / "o")])
    assert res.exit_code == 1 and not (tmp_path / "o").exists()


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_generated_terraform_validates(tmp_path: Path) -> None:
    for name, text in files().items():
        (tmp_path / name).write_text(text)
    terraform = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    if init.returncode != 0:
        pytest.skip("terraform init needs network access to download providers")
    result = subprocess.run(  # noqa: S603
        [terraform, "validate", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
