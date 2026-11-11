"""The GCP estate corpus stays valid, and the measuring script reproduces numbers on a subset."""

import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "analysis"
CORPUS = ROOT / "tests" / "fixtures" / "gcp-estate"


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


measure = load("gcp_estate_measure")


def files(folder: str) -> list[Path]:
    return sorted((CORPUS / folder).glob("*/*.json"))


# --- the corpus ---------------------------------------------------------------------------------


def test_corpus_has_the_promised_size_and_every_file_is_json() -> None:
    policies, dashboards = files("policies"), files("dashboards")
    assert len(policies) >= 60 and len(dashboards) >= 25
    for path in [*policies, *dashboards, *CORPUS.glob("*/*.json")]:
        json.loads(path.read_text())


def test_manifest_lists_every_file_once_with_origin_and_licence() -> None:
    manifest = json.loads((CORPUS / "manifest.json").read_text())
    listed = [e["file"] for e in manifest["files"]]
    on_disk = sorted(str(p.relative_to(CORPUS)) for p in CORPUS.glob("*/*/*.json"))
    on_disk += ["channels/channels.json", "uptime-checks/uptime-checks.json"]
    assert sorted(listed) == sorted(on_disk)
    assert len(listed) == len(set(listed))
    for entry in manifest["files"]:
        assert entry["origin"] in ("public", "SYNTHETIC") and entry["licence"]
        assert (entry["origin"] == "SYNTHETIC") == (
            "synthetic" in entry["file"]
            or entry["file"].startswith(("channels/", "uptime-checks/"))
        )
    assert "Apache" in (CORPUS / "SOURCES.md").read_text()


def test_no_addresses_secrets_or_real_project_ids() -> None:
    address = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.(?:com|org|net|io|dev)\b")
    secret = re.compile(
        r"AIza[0-9A-Za-z_-]{20}|xox[bp]-|ghp_[0-9A-Za-z]{20}|BEGIN [A-Z ]*PRIVATE KEY"
    )
    project = re.compile(r"projects/([A-Za-z0-9_${}.-]+)")
    for path in CORPUS.rglob("*.json"):
        text = path.read_text()
        assert not address.search(text), path
        assert not secret.search(text), path
        for found in project.findall(text):
            assert found in {
                "example-project",
                "PROJECT_ID",
                "${PROJECT_ID}",
                "-",
                ".",
            } or found.startswith("$"), (path, found)


def test_corpus_covers_the_shapes_the_task_asks_for() -> None:
    kinds: set[str] = set()
    shapes: set[str] = set()
    for path in files("policies"):
        policy = json.loads(path.read_text())
        shapes.add(
            "and" if policy["combiner"].startswith("AND") and len(policy["conditions"]) > 1 else ""
        )
        if "autoClose" in policy.get("alertStrategy", {}):
            shapes.add("auto-close")
        if policy.get("severity") in ("WARNING", "CRITICAL", "ERROR"):
            shapes.add("severity")
        if policy.get("documentation", {}).get("content"):
            shapes.add("documentation")
        for cond in policy["conditions"]:
            kinds.update(k for k in cond if k.startswith("condition"))
            body = cond.get("conditionThreshold", {})
            if body.get("denominatorFilter"):
                shapes.add("ratio")
            if body.get("forecastOptions"):
                shapes.add("forecast")
            if "select_slo_burn_rate" in str(body.get("filter")):
                shapes.add("slo-burn-rate")
            if "uptime_check" in str(body.get("filter")):
                shapes.add("uptime-check")
    assert kinds >= {
        "conditionThreshold",
        "conditionAbsent",
        "conditionMatchedLog",
        "conditionMonitoringQueryLanguage",
        "conditionPrometheusQueryLanguage",
        "conditionSql",
    }
    assert shapes >= {
        "and",
        "auto-close",
        "severity",
        "documentation",
        "ratio",
        "forecast",
        "slo-burn-rate",
        "uptime-check",
    }


def test_channels_cover_the_seven_types_and_checks_cover_http_and_tcp() -> None:
    channels = json.loads((CORPUS / "channels" / "channels.json").read_text())
    types = {c["type"] for c in channels}
    assert types >= {"email", "slack", "pagerduty", "webhook_tokenauth", "pubsub", "sms", "mobile"}
    checks = json.loads((CORPUS / "uptime-checks" / "uptime-checks.json").read_text())
    assert any("httpCheck" in c for c in checks) and any("tcpCheck" in c for c in checks)


# --- the script ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("reason", "code"),
    [
        ("PromQL conditions are not converted", "promql"),
        ("MQL queries in widgets are not converted", "mql"),
        (
            "the metric is not one of the Google Cloud service metrics that New Relic documents",
            "uncollected metric family",
        ),
        (
            "filter not supported: cannot read 'select_slo_health(\"projects/x'",
            "slo selector filter",
        ),
        ("filter not supported: cannot read '(a=\"b\" OR'", "filter syntax"),
        (
            "label 'resource.label.\"project_id\"' does not make a valid New Relic attribute name",
            "quoted label name",
        ),
        ("aligner 'ALIGN_STDDEV' is not supported", "aligner"),
        ("dashboard widget type is not supported", "widget type"),
        ("something new", "other: something new"),
    ],
)
def test_reason_codes(reason: str, code: str) -> None:
    assert measure.reason_code(reason) == code


def test_primary_code_skips_the_channel_note_and_the_absence_prefix() -> None:
    reasons = [
        "a metric-absence condition could not be converted: aligner 'X' is not supported",
        "x",
    ]
    assert measure.primary_code(reasons) == "aligner"
    assert measure.primary_code(["notification channels must be configured manually"]) == "none"
    assert (
        measure.primary_code(["height 16 clamped to 12", "dashboard widget type is not supported"])
        == "widget type"
    )


@pytest.mark.parametrize(
    ("text", "family"),
    [
        ('metric.type = "compute.googleapis.com/instance/cpu/utilization"', "google service"),
        (
            'metric.type = "workload.googleapis.com/jvm.memory.heap.used"',
            "ops-agent third-party (workload)",
        ),
        ('metric.type = "agent.googleapis.com/memory/percent_used"', "ops-agent legacy (agent)"),
        ('metric.type = "logging.googleapis.com/user/x"', "log-based"),
        ('select_slo_health("projects/p/services/s/serviceLevelObjectives/o")', "slo"),
        ('{"prometheusQuery": "up"}', "promql query"),
        ("severity>=ERROR", "none"),
    ],
)
def test_metric_family(text: str, family: str) -> None:
    assert measure.metric_family(text) == family


def test_prepare_promql_rewrites_the_two_gcp_only_forms() -> None:
    prepared = measure.prepare_promql(
        'sum(rate({"workload.googleapis.com/a.b", k="v", ${Cluster}}[${__interval}]))'
    )
    assert prepared == 'sum(rate(workload_googleapis_com_a_b{k="v"}[5m]))'


@pytest.fixture
def subset(tmp_path: Path) -> Path:
    """A small corpus: some of each condition kind, three dashboards, all channels and checks."""
    wanted_policies = [
        "synthetic/gce-cpu-high-pager.json",
        "synthetic/absent-vm-uptime.json",
        "synthetic/log-error-severity.json",
        "synthetic/ratio-lb-5xx.json",
        "synthetic/mql-basic-condition.json",
        "synthetic/promql-simple-threshold.json",
        "synthetic/multi-and-cpu-mem.json",
        "public/google-gce__cpu-utilization-too-high.v1.json",
    ]
    wanted_dashboards = [
        "synthetic/grid-basic.json",
        "synthetic/mixed-panels.json",
        "synthetic/mql-charts.json",
    ]
    for folder, names in (("policies", wanted_policies), ("dashboards", wanted_dashboards)):
        for name in names:
            target = tmp_path / folder / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(CORPUS / folder / name, target)
    for name in ("channels/channels.json", "uptime-checks/uptime-checks.json"):
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(CORPUS / name, tmp_path / name)
    return tmp_path


def test_subset_numbers_are_reproduced_by_the_commands_and_the_library(subset: Path) -> None:
    summary = measure.summarise(measure.Corpus(subset), experiments=False)
    assert summary["policies"] == 8 and summary["dashboards"] == 3
    conditions = summary["conditions"]
    assert conditions["total"] == 9  # the AND policy has two conditions
    shapes = conditions["by_shape"]
    assert shapes["conditionThreshold: metric"]["converted"] >= 2
    assert shapes["conditionThreshold: ratio"] == {"converted": 1}
    assert shapes["conditionMonitoringQueryLanguage"] == {"manual": 1}
    assert shapes["conditionPrometheusQueryLanguage"] == {"manual": 1}
    assert shapes["conditionAbsent"] == {"converted": 1}
    assert shapes["conditionMatchedLog"] == {"converted": 1}
    codes = dict(conditions["blockers"])
    assert codes["ratio"] == 1 and codes["mql"] == 1 and codes["promql"] == 1
    widgets = summary["widgets"]
    assert widgets["scoreboard_converted"] >= 4
    assert widgets["by_kind"]["logsPanel"] == {"placeholder": 1}
    assert summary["cross_check"]["cli_conditions_total"] == conditions["total"]
    assert summary["notifications"]["channels"] == 20 and summary["uptime"]["checks"] == 12


def test_placeholders_are_never_counted_as_converted(subset: Path) -> None:
    widgets = measure.summarise(measure.Corpus(subset), experiments=False)["widgets"]
    assert widgets["scoreboard_converted"] + widgets["scoreboard_placeholders"] <= widgets["total"]
    placeholders = sum(n for _, n in widgets["blockers"])
    assert widgets["data_converted"] + placeholders == widgets["data_widgets"]
