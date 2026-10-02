import re
import shutil
import subprocess
from pathlib import Path

import pytest
from pydantic import ValidationError
from typer.testing import CliRunner

from importer.cli import app
from importer.emit.ingest import ENDPOINTS, IngestConfig, ingest_files

REAL = Path(__file__).parent / "fixtures" / "cloudwatch-real"


def files(**kw: object) -> dict[str, str]:
    base: dict[str, object] = {"namespaces": ["AWS/EC2", "AWS/RDS"]}
    base.update(kw)
    return ingest_files(IngestConfig.model_validate(base))


def test_generates_expected_resources() -> None:
    out = files()
    assert set(out) == {"main.tf", "variables.tf", "outputs.tf"}
    for resource in (
        "aws_kinesis_firehose_delivery_stream",
        "aws_cloudwatch_metric_stream",
        "aws_s3_bucket_public_access_block",
        "aws_s3_bucket_server_side_encryption_configuration",
        "newrelic_cloud_aws_link_account",
    ):
        assert resource in out["main.tf"]
    assert 'metric_collection_mode = "PUSH"' in out["main.tf"]


def test_only_requested_namespaces_are_streamed_sorted_and_deduplicated() -> None:
    main = files(namespaces=["AWS/RDS", "AWS/EC2", "AWS/RDS"])["main.tf"]
    assert main.count("include_filter") == 2
    assert main.index('"AWS/EC2"') < main.index('"AWS/RDS"')


def test_eu_region_uses_eu_endpoint_and_provider_region() -> None:
    main = files(nr_region="eu")["main.tf"]
    assert ENDPOINTS["eu"] in main and ENDPOINTS["us"] not in main
    assert 'region     = "EU"' in main


def test_secrets_are_sensitive_variables_without_defaults_and_never_literal() -> None:
    out = files()
    for name in ("newrelic_license_key", "newrelic_api_key"):
        block = out["variables.tf"].split(f'variable "{name}"')[1].split("}")[0]
        assert "sensitive   = true" in block
        assert "default" not in block
    text = "".join(out.values())
    assert not re.search(r"NRAK-[A-Z0-9]{20,}|[0-9a-f]{32,}|[A-Za-z0-9]{36}NRAL", text)


def test_trust_policy_requires_external_id() -> None:
    main = files()["main.tf"]
    assert "sts:ExternalId" in main
    assert "754728514883" in main


@pytest.mark.parametrize(
    "fields",
    [
        {"name_prefix": 'x"; evil = "1'},
        {"name_prefix": "UPPER"},
        {"name_prefix": "a" * 40},
        {"name_prefix": "${evil}"},
        {"aws_region": 'us-east-1"'},
        {"aws_region": "nowhere"},
        {"nr_region": "mars"},
        {"namespaces": []},
        {"namespaces": ['AWS/EC2"\n}\nresource "x" "y" {']},
        {"namespaces": ["AWS/ EC2"]},
        {"namespaces": ["${evil}"]},
        {"namespaces": [f"AWS/N{i}" for i in range(60)]},
    ],
)
def test_hostile_or_invalid_inputs_are_rejected(fields: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        files(**fields)


def test_cli_derives_namespaces_from_alarm_export(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["ingest-setup", "--alarms", str(REAL), "--out", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    main = (tmp_path / "main.tf").read_text()
    for ns in ("AWS/EC2", "AWS/RDS", "AWS/Lambda", "AWS/SQS", "AWS/ApplicationELB", "Custom/MyApp"):
        assert f'"{ns}"' in main
    assert "TF_VAR_newrelic_license_key" in result.output


def test_cli_rejects_bad_input_without_writing(tmp_path: Path) -> None:
    result = CliRunner().invoke(
        app, ["ingest-setup", "--name-prefix", "Bad Name", "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 1
    assert not (tmp_path / "o").exists()


def test_cli_defaults_to_supported_namespaces(tmp_path: Path) -> None:
    result = CliRunner().invoke(app, ["ingest-setup", "--out", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert '"AWS/EC2"' in (tmp_path / "main.tf").read_text()


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_generated_setup_validates(tmp_path: Path) -> None:
    for name, text in files().items():
        (tmp_path / name).write_text(text)
    terraform = shutil.which("terraform") or "terraform"
    init = subprocess.run(  # noqa: S603
        [terraform, "init", "-backend=false", "-input=false", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if init.returncode != 0:
        pytest.skip("cannot download providers (offline or blocked)")
    check = subprocess.run(  # noqa: S603
        [terraform, "validate", "-no-color"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
