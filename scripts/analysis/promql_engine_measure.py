"""Before/after numbers for the PromQL engine, offline.

Usage:
    uv run python scripts/analysis/promql_engine_measure.py [--dashboards DIR ...] [--json OUT]

"Before" is the plain reader alone (engine switched off in the same process, which is exact
because the engine only runs after the plain reader refused); "after" is the shipped behaviour.

1. Google: the 84 PromQL alert samples of `tests/fixtures/gcp-promql` (a converted alert has a
   real query behind it; a placeholder never counts), and the PromQL conditions of the GCP estate
   corpus (`tests/fixtures/gcp-estate`).
2. Grafana alert-rule fixtures (`tests/fixtures/grafana*`): rules that convert.
3. Grafana dashboards, when `--dashboards` names folders of dashboard JSON: PromQL expressions the
   converter writes (`convert_classic` against `convert_query`), through the census.

A line marked "not counted" shows what would change if the owner supplied defaults for the
`${NAME}` placeholders; those expressions are never added to the converted numbers.
"""

import argparse
import contextlib
import importlib
import json
import os
import re
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT / "tests"))

from promql_census import build as census_build  # noqa: E402

from importer.core.ir import Confidence, IRMonitor  # noqa: E402
from importer.sources.gcp.models import GcpAlertPolicy  # noqa: E402
from importer.sources.gcp.translate import translate_policy  # noqa: E402
from importer.sources.grafana.models import load_rules  # noqa: E402

translate = importlib.import_module("importer.sources.grafana.translate")
PLACEHOLDER = re.compile(r"\$\{(\w+)\}")


@contextlib.contextmanager
def engine_off() -> Iterator[None]:
    saved = list(translate.PROM_FALLBACKS)
    translate.PROM_FALLBACKS[:] = []
    try:
        yield
    finally:
        translate.PROM_FALLBACKS[:] = saved


@contextlib.contextmanager
def defaults_for(names: set[str]) -> Iterator[None]:
    """A name map that gives every placeholder the value `x` (hypothetical; never counted)."""
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
        json.dump({"variables": dict.fromkeys(sorted(names), "x")}, handle)
    old = os.environ.get("IMPORTER_GRAFANA_NAME_MAP")
    os.environ["IMPORTER_GRAFANA_NAME_MAP"] = handle.name
    try:
        yield
    finally:
        Path(handle.name).unlink(missing_ok=True)
        if old is None:
            os.environ.pop("IMPORTER_GRAFANA_NAME_MAP", None)
        else:
            os.environ["IMPORTER_GRAFANA_NAME_MAP"] = old


def real(m: IRMonitor) -> bool:
    s = m.signal
    if m.confidence is Confidence.MANUAL:
        return False
    return bool(s.metric and s.metric.terms) or s.heartbeat is not None or s.count is not None


def gcp_conditions(folders: list[Path]) -> list[dict[str, Any]]:
    """Policies holding a PromQL condition, from sample files or estate files."""
    out: list[dict[str, Any]] = []
    for folder in folders:
        for path in sorted(folder.rglob("*.json")):
            data = json.loads(path.read_text())
            if isinstance(data, dict) and "conditions" in data:
                out.append(data)
    return out


def gcp_count(policies: list[dict[str, Any]]) -> dict[str, int]:
    total = converted = placeholders = 0
    for raw in policies:
        policy = GcpAlertPolicy.model_validate(raw)
        for cond, m in zip(raw["conditions"], translate_policy(policy), strict=True):
            body = cond.get("conditionPrometheusQueryLanguage")
            if not isinstance(body, dict):
                continue
            total += 1
            converted += real(m)
            placeholders += "${" in str(body.get("query", ""))
    return {"conditions": total, "converted": converted, "with_placeholder": placeholders}


def gcp_section() -> dict[str, Any]:
    samples = gcp_conditions([ROOT / "tests" / "fixtures" / "gcp-promql"])
    estate = gcp_conditions(
        [
            ROOT / "tests" / "fixtures" / "gcp-estate" / "policies" / d
            for d in ("public", "synthetic")
        ]
    )
    names = {n for p in samples + estate for n in PLACEHOLDER.findall(json.dumps(p))}
    with engine_off():
        before = {"samples": gcp_count(samples), "estate": gcp_count(estate)}
    after = {"samples": gcp_count(samples), "estate": gcp_count(estate)}
    with defaults_for(names):
        hypothetical = {"samples": gcp_count(samples), "estate": gcp_count(estate)}
    return {"before": before, "after": after, "not_counted_with_defaults": hypothetical}


def _rules_in(path: Path) -> list[Any]:
    """The rules of one fixture file; a file that is not a rule export has none."""
    try:
        return list(load_rules(path))
    except Exception:  # the fixture folders hold other kinds of JSON too
        return []


def grafana_rules() -> dict[str, Any]:
    paths = sorted({p for p in (ROOT / "tests" / "fixtures").glob("grafana*/**/*.json")})

    def count() -> tuple[int, int]:
        done = total = 0
        seen: set[str] = set()
        for path in paths:
            rules = _rules_in(path)
            for rule in rules:
                key = json.dumps(
                    [rule.title, rule.expr, [n.model_dump(mode="json") for n in rule.data]],
                    sort_keys=True,
                    default=str,
                )
                if key in seen:
                    continue
                seen.add(key)
                total += 1
                done += translate.translate_rule(rule).confidence is not Confidence.MANUAL
        return done, total

    with engine_off():
        before = count()
    after = count()
    return {"before": before[0], "after": after[0], "distinct_rules": after[1]}


def dashboards(folders: list[Path]) -> dict[str, Any]:
    census = census_build(folders)
    classic = sum(e.converts for e in census.expressions)
    engine = sum(e.engine for e in census.expressions)
    return {
        "expressions": len(census.expressions),
        "before": classic,
        "after": classic + engine,
        "engine_adds": engine,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dashboards", type=Path, nargs="*", default=[])
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)
    result: dict[str, Any] = {"google": gcp_section(), "grafana_rules": grafana_rules()}
    if args.dashboards:
        result["grafana_dashboards"] = dashboards(args.dashboards)
    text = json.dumps(result, indent=1)
    print(text)
    if args.json:
        args.json.write_text(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
