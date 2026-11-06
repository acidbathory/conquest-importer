"""The Terraform that links a Google Cloud project to New Relic."""

import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.gcp.link_terraform import SERVICE_BLOCKS, link_terraform, services_for
from importer.sources.gcp.models import GcpAlertPolicy
from importer.sources.gcp.translate import translate_policy


def monitor(metric: str) -> object:
    policy = GcpAlertPolicy.model_validate(
        {
            "displayName": "p",
            "conditions": [
                {
                    "displayName": "c",
                    "conditionThreshold": {
                        "filter": f'metric.type="{metric}"',
                        "comparison": "COMPARISON_GT",
                        "thresholdValue": 1,
                        "duration": "60s",
                        "aggregations": [
                            {"alignmentPeriod": "60s", "perSeriesAligner": "ALIGN_MAX"}
                        ],
                    },
                }
            ],
        }
    )
    return translate_policy(policy)[0]


def test_services_follow_the_metrics_used() -> None:
    ms = [
        monitor("pubsub.googleapis.com/subscription/num_undelivered_messages"),
        monitor("compute.googleapis.com/instance/cpu/utilization"),
        monitor("compute.googleapis.com/instance/disk/read_ops_count"),
        monitor("kubernetes.io/container/cpu/core_usage_time"),
    ]
    blocks, unknown = services_for(ms)  # type: ignore[arg-type]
    assert blocks == ["kubernetes", "pub_sub", "virtual_machines"] and not unknown


def test_every_block_name_exists_in_the_provider_schema() -> None:
    # names taken from `terraform providers schema` for newrelic_cloud_gcp_dm_integrations
    known = {
        "ai_platform", "alloy_db", "api_gateway", "app_engine", "big_query", "big_table",
        "composer", "data_flow", "data_proc", "data_store", "firestore", "functions",
        "kubernetes", "load_balancing", "mem_cache", "pub_sub", "redis", "run", "spanner",
        "sql", "storage", "virtual_machines", "vpc_access",
    }  # fmt: skip
    assert set(SERVICE_BLOCKS.values()) <= known


def test_the_file_has_no_secrets_and_the_expected_resources() -> None:
    tf = link_terraform("my-project", 1234567, ["pub_sub"], "newrelic-pool")
    for needed in (
        "newrelic_cloud_gcp_link_account",
        "newrelic_cloud_gcp_dm_integrations",
        "use_workload_identity_federation = true",
        "pub_sub {",
        'default = "1234567"',
    ):
        assert needed in tf
    assert "api_key" not in tf and "private_key" not in tf and "@@" not in tf


@pytest.mark.parametrize("bad", ["", "My-Project", "a;b", 'x"y', "p" * 40, "x\nresource"])
def test_a_bad_project_id_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="project id"):
        link_terraform(bad, 1, [], "newrelic-pool")


@pytest.mark.parametrize("bad", ["", "ab", "Pool", 'a"b${x}'])
def test_a_bad_pool_id_is_refused(bad: str) -> None:
    with pytest.raises(ValueError, match="pool-id"):
        link_terraform("my-project", 1, [], bad)


def test_an_unverified_region_is_refused() -> None:
    with pytest.raises(ValueError, match="not supported"):
        link_terraform("my-project", 1, [], "newrelic-pool", region="eu")


def test_duplicate_services_are_written_once() -> None:
    tf = link_terraform("my-project", 1, ["run", "run", "sql"], "newrelic-pool")
    assert tf.count("run {") == 1 and tf.count("sql {") == 1


def test_cli_writes_the_file(tmp_path: Path) -> None:
    out = tmp_path / "link"
    result = CliRunner().invoke(
        app,
        ["gcp-link-terraform", "--project", "my-project", "--nr-account-id", "1234567",
         "--services", "run", "--out", str(out)],
    )  # fmt: skip
    assert result.exit_code == 0, result.output
    assert "run {" in (out / "main.tf").read_text()


@pytest.mark.skipif(shutil.which("terraform") is None, reason="terraform not installed")
def test_generated_file_is_formatted(tmp_path: Path) -> None:  # pragma: no cover
    (tmp_path / "main.tf").write_text(link_terraform("my-project", 1, ["pub_sub"], "newrelic-pool"))
    subprocess.run(["terraform", "fmt", "-check", str(tmp_path)], check=True)  # noqa: S603, S607
