"""Run the cases of `promql_rule_cases.json` through the converters, offline.

Usage:
    uv run python scripts/analysis/promql_rule_runner.py [--cases FILE] [--json OUT]

Each case names a PromQL expression, the arguments the converter needs and the expected result
(docs/research/promql-engine-rules.md). `run_case` returns what the converter wrote; the unit test
(`tests/test_promql_engine_rules.py`) compares it with the expected NRQL, and the live harness
imports the same file to compare values against Prometheus with the telemetry rig.

Case fields:
    id, rule         the rule id of the registry and the case id
    surface          alert (a rule-file rule, comparison inside the query), gcp-alert (a Google
                     condition: placeholders, quoted names) or dashboard (a widget query)
    promql           the expression, with its comparison for an alert
    args             alert: interval, for, hints; gcp-alert: the same plus variables;
                     dashboard: timeseries, bucket_seconds, variables, hints
    expected         converted (bool), nrql, shape (what the harness compares), and for alerts
                     window_seconds and slide_by_seconds; refused cases give reason_contains
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from importer.core.emit.common import nrql_for
from importer.core.ir import Confidence
from importer.sources.gcp.promql import convert as gcp_convert
from importer.sources.grafana.dashboard_convert.name_map import NameMap
from importer.sources.grafana.dashboard_convert.promql import QueryOptions, convert_query
from importer.sources.grafana.dashboard_convert.reuse import Unconvertible
from importer.sources.grafana.models import GrafanaRule
from importer.sources.grafana.translate import Fail, translate_rule

DEFAULT_CASES = Path(__file__).resolve().parent / "promql_rule_cases.json"
MAP_ENV = "IMPORTER_GRAFANA_NAME_MAP"


class hints_file:
    """Write the case's hints as a name map file and point the importer at it."""

    def __init__(self, hints: dict[str, Any]) -> None:
        self.hints = hints
        self.old = os.environ.get(MAP_ENV)

    def __enter__(self) -> None:
        if not self.hints:
            os.environ.pop(MAP_ENV, None)
            return
        handle = tempfile.NamedTemporaryFile("w", suffix=".json", delete=False)
        json.dump(self.hints, handle)
        handle.close()
        self.path = handle.name
        os.environ[MAP_ENV] = handle.name

    def __exit__(self, *exc: object) -> None:
        if self.hints:
            Path(self.path).unlink(missing_ok=True)
        if self.old is None:
            os.environ.pop(MAP_ENV, None)
        else:
            os.environ[MAP_ENV] = self.old


def _alert(case: dict[str, Any]) -> dict[str, Any]:
    args = case.get("args", {})
    with hints_file(args.get("hints", {})):
        if case["surface"] == "gcp-alert":
            from importer.core.ir import Severity

            body = {
                "query": case["promql"],
                "duration": f"{args.get('for', 300)}s",
                "evaluationInterval": f"{args.get('interval', 60)}s",
            }
            try:
                monitor = gcp_convert(body, key="case", name=case["id"], severity=Severity.CRITICAL)
            except Fail as stop:
                return {"converted": False, "reason": stop.reason}
        else:
            rule = GrafanaRule(
                title=case["id"],
                interval_seconds=args.get("interval", 60),
                for_seconds=args.get("for", 300),
                datasource_managed=True,
                expr=case["promql"],
            )
            monitor = translate_rule(rule, case["id"])
        if monitor.confidence is Confidence.MANUAL:
            return {"converted": False, "reason": monitor.reasons[0] if monitor.reasons else ""}
        return {
            "converted": True,
            "nrql": nrql_for(monitor),
            "window_seconds": monitor.signal.aggregation_window_seconds,
            "slide_by_seconds": monitor.emit.slide_by_seconds if monitor.emit else None,
            "notes": monitor.reasons,
        }


def _dashboard(case: dict[str, Any]) -> dict[str, Any]:
    args = case.get("args", {})
    opts = QueryOptions(
        variables=args.get("variables", {}),
        name_map=NameMap.model_validate(args.get("hints", {})),
        timeseries=args.get("timeseries", False),
        bucket_seconds=args.get("bucket_seconds"),
    )
    try:
        done = convert_query(case["promql"], "prometheus", opts)
    except Unconvertible as stop:
        return {"converted": False, "reason": stop.reason}
    return {"converted": True, "nrql": done.nrql, "notes": done.notes}


def run_case(case: dict[str, Any]) -> dict[str, Any]:
    return _dashboard(case) if case["surface"] == "dashboard" else _alert(case)


def load_cases(path: Path = DEFAULT_CASES) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = json.loads(path.read_text())["cases"]
    return cases


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES)
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    results = {c["id"]: run_case(c) for c in load_cases(args.cases)}
    bad = 0
    for case in load_cases(args.cases):
        got, want = results[case["id"]], case["expected"]
        same = got["converted"] == want["converted"] and (
            not want["converted"] or got["nrql"] == want["nrql"]
        )
        bad += not same
        print(("ok   " if same else "FAIL ") + case["id"])
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    print(f"{len(results) - bad} of {len(results)} cases as expected")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
