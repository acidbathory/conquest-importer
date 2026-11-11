"""The PromQL engine rules: every case of promql_rule_cases.json, the registry and its evidence.

The expected NRQL in the JSON file was computed by hand from each rule (docs/research/
promql-engine-rules.md). The live harness imports the same file to compare values.
"""

import contextlib
import hashlib
import importlib.util
import json
import random
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts" / "analysis"


def load(name: str) -> ModuleType:
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner = load("promql_rule_runner")
CASES = runner.load_cases()
REGISTRY = (ROOT / "docs" / "research" / "promql-engine-rules.md").read_text()
VALIDITY = json.loads((ROOT / "docs" / "research" / "promql-engine-validity.json").read_text())


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_the_case_gives_the_hand_computed_result(case: dict[str, object]) -> None:
    got = runner.run_case(case)
    want = case["expected"]
    assert isinstance(want, dict)
    assert got["converted"] is want["converted"], got
    if not want["converted"]:
        assert want.get("reason_contains", "").lower() in got["reason"].lower()
        return
    assert got["nrql"] == want["nrql"]
    for key in ("window_seconds", "slide_by_seconds"):
        if key in want:
            assert got[key] == want[key], key


def test_every_case_is_ready_for_the_live_harness() -> None:
    ids = [c["id"] for c in CASES]
    assert len(ids) == len(set(ids))
    for case in CASES:
        assert case["differential_test_pending"], case["id"]
        assert case["expected"].get("shape"), case["id"]
        assert case["surface"] in ("alert", "gcp-alert", "dashboard")


def test_every_rule_of_the_cases_is_in_the_registry_with_a_status() -> None:
    for rule in {c["rule"] for c in CASES}:
        rows = [r for r in REGISTRY.splitlines() if r.startswith(f"| {rule} |")]
        assert rows, rule
        assert all("VERIFIED-VALID" in r for r in rows), rule


def test_every_converted_case_was_accepted_by_new_relic_for_this_exact_query() -> None:
    for case in CASES:
        got = runner.run_case(case)
        if not got["converted"]:
            continue
        entry = VALIDITY[case["id"]]
        assert entry["accepted"] is True, case["id"]
        assert entry["sha"] == hashlib.sha256(got["nrql"].encode()).hexdigest()[:16], (
            f"{case['id']}: the query changed; run scripts/analysis/promql_rule_validate.py"
        )


def test_a_refused_case_keeps_a_visible_reason() -> None:
    refused = [c for c in CASES if not c["expected"]["converted"]]
    assert len(refused) >= 8
    for case in refused:
        assert runner.run_case(case)["reason"], case["id"]


# --- hostile input ---


def test_random_expressions_never_raise_in_the_alert_engine() -> None:
    from importer.core.ir import Confidence
    from importer.sources.grafana.models import GrafanaRule
    from importer.sources.grafana.translate import translate_rule

    rng = random.Random(7)  # noqa: S311 - seeded, so the test is repeatable
    pieces = [
        *("sum", "avg", "(", ")", "by", "x_total", "rate", "[5m]", "/", "-", "1", "0", "{", "}"),
        *('a="b"', "topk", "3,", "without", "max_over_time", ">", "bool", "on", "$v", "${V}"),
    ]
    for _ in range(400):
        text = " ".join(rng.choices(pieces, k=rng.randint(1, 14)))
        rule = GrafanaRule(
            title="t", interval_seconds=60, for_seconds=300, datasource_managed=True, expr=text
        )
        monitor = translate_rule(rule, "k")
        assert monitor.confidence in (Confidence.MANUAL, Confidence.APPROXIMATE)


def test_random_expressions_never_raise_in_the_dashboard_engine() -> None:
    from importer.sources.grafana.dashboard_convert.name_map import NameMap
    from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
    from importer.sources.grafana.dashboard_convert.reuse import Unconvertible

    rng = random.Random(11)  # noqa: S311 - seeded, so the test is repeatable
    pieces = [
        *("sum", "avg", "count", "(", ")", "by", "x", "rate", "[5m]", "/", "1", "{", "}"),
        *('a="b"', "max_over_time", ">", "==", "bool", "$v", "[$__interval]", "0"),
    ]
    opts = QueryOptions(variables={"v": None}, name_map=NameMap(), timeseries=False)
    for _ in range(400):
        text = " ".join(rng.choices(pieces, k=rng.randint(1, 14)))
        with contextlib.suppress(Unconvertible):
            convert_query(text, "prometheus", opts)


def test_a_very_long_expression_is_not_parsed_by_the_engine() -> None:
    from importer.sources.grafana.rules2.promql_engine import _read

    assert _read("a" * 20_000) is None
