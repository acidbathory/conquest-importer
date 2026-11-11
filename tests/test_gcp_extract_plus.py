"""The extra reads: log-based metrics, SLOs and uptime details (fixtures: gcp-extract-plus)."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.gcp.extract import GcpError, extract
from importer.sources.gcp.extract_plus import READ_TEMPLATES

FIX = Path(__file__).parent / "fixtures" / "gcp-extract-plus"
P = "projects/example-project"


def api(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIX / name).read_text())
    return data


class Fake:
    """A fake Cloud Monitoring and Logging API serving the synthetic fixture responses."""

    def __init__(self, fail: str = "", services: dict[str, Any] | None = None) -> None:
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.fail = fail
        self.services = services or api("api-services.json")

    def __call__(self, url: str, params: dict[str, str]) -> dict[str, Any]:
        self.calls.append((url, params))
        if self.fail and self.fail in url:
            raise GcpError("403 denied")
        if url.endswith("/metrics"):
            return api("api-log-metrics.json")
        if url.endswith("/services"):
            return self.services
        if url.endswith("/services/checkout/serviceLevelObjectives"):
            return api("api-slos-checkout.json")
        if url.endswith("serviceLevelObjectives"):
            return {}
        if url.endswith("uptimeCheckConfigs"):
            return api("api-uptime-check-configs.json")
        return {}


def read(tmp_path: Path, fake: Fake | None = None) -> tuple[Path, Fake]:
    fake = fake or Fake()
    extract(fake, "example-project", tmp_path / "out")
    return tmp_path / "out", fake


def load(out: Path, folder: str) -> list[dict[str, Any]]:
    data: list[dict[str, Any]] = json.loads((out / folder / f"{folder}.json").read_text())
    return data


def test_reads_log_metrics_and_slos_into_their_folders(tmp_path: Path) -> None:
    out, _ = read(tmp_path)
    metrics = {m["name"]: m for m in load(out, "log-metrics")}
    assert metrics["checkout_errors"]["labelExtractors"] == {
        "order_region": "EXTRACT(jsonPayload.region)"
    }
    assert metrics["checkout_latency_ms"]["valueType"] == "DISTRIBUTION"
    assert "exponentialBuckets" in metrics["checkout_latency_ms"]["bucketOptions"]
    assert "description" not in metrics["checkout_errors"]
    slos = {s["name"].rsplit("/", 1)[-1]: s for s in load(out, "slos")}
    assert slos["availability-99-9"]["goal"] == 0.999
    assert slos["availability-99-9"]["rollingPeriod"] == "2419200s"
    assert slos["latency-cut"]["calendarPeriod"] == "WEEK"
    assert "requestBased" in slos["availability-99-9"]["serviceLevelIndicator"]


def test_only_get_paths_from_the_templates_are_called(tmp_path: Path) -> None:
    _, fake = read(tmp_path)
    allowed = {
        t.format(p="example-project", s=s)
        for t in READ_TEMPLATES.values()
        for s in ("checkout", "gateway")
    }
    plus = [u for u, _ in fake.calls if "logging.googleapis" in u or "service" in u]
    assert plus and set(plus) <= allowed
    assert all(u.startswith("https://") for u, _ in fake.calls)


def test_a_service_id_that_is_not_plain_text_is_never_put_in_a_url(tmp_path: Path) -> None:
    evil = {"services": [{"name": f"{P}/services/a%2F..%2Fb"}, {"name": f"{P}/services/ok"}]}
    _, fake = read(tmp_path, Fake(services=evil))
    urls = [u for u, _ in fake.calls if u.endswith("serviceLevelObjectives")]
    assert urls == [
        "https://monitoring.googleapis.com/v3/projects/example-project/services/ok/"
        "serviceLevelObjectives"
    ]


def test_a_failed_read_warns_and_writes_nothing_so_convert_knows_it_was_not_read(
    tmp_path: Path,
) -> None:
    out = tmp_path / "out"
    result = extract(Fake(fail="/metrics"), "example-project", out)
    assert any(w.startswith("log-metrics:") for w in result.warnings)
    assert not (out / "log-metrics").exists() and (out / "slos" / "slos.json").exists()


def test_uptime_details_keep_the_url_parts_and_drop_every_secret(tmp_path: Path) -> None:
    out, _ = read(tmp_path)
    checks = {c["details"]["id"]: c["details"] for c in load(out, "uptime-checks")}
    assert checks["site-health"]["host"] == "host.example.com"
    assert checks["site-health"]["regions"] == ["USA", "EUROPE", "ASIA_PACIFIC"]
    assert checks["site-health"]["timeout"] == "10s"
    assert checks["api-auth"]["auth"] == "basic"
    assert checks["api-auth"]["headerNames"] == ["Authorization"]
    assert checks["post-check"]["hasBody"] is True
    assert checks["internal"]["internal"] is True
    text = (out / "uptime-checks" / "uptime-checks.json").read_text()
    for planted in ("PLANTED-SECRET-PASSWORD", "PLANTED-HEADER-TOKEN", "probe-user", "eyJ0b2tl"):
        assert planted not in text


def test_a_host_that_is_not_a_host_name_is_dropped(tmp_path: Path) -> None:
    from importer.sources.gcp.extract_plus import uptime_details

    bad = {"monitoredResource": {"labels": {"host": "a b/../c?token=x"}}, "httpCheck": {}}
    assert uptime_details(bad)["host"] == ""


@pytest.mark.parametrize("project", ["", "A", "x/../y"])
def test_a_bad_project_is_refused_before_any_call(tmp_path: Path, project: str) -> None:
    fake = Fake()
    with pytest.raises(ValueError, match="project id"):
        extract(fake, project, tmp_path / "o")
    assert not fake.calls
