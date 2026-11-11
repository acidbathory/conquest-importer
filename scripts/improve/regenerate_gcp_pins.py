"""Regenerate the pinned outputs that the combined GCP work changes on purpose.

    uv run python scripts/improve/regenerate_gcp_pins.py           # dry run: show what would change
    uv run python scripts/improve/regenerate_gcp_pins.py --apply   # write it

Read docs/improvement/proposals/2026-11-11-gcp-combined-output-diff.md first: running this with
--apply is the owner's approval of that output. It rewrites exactly:

  tests/fixtures/emitter-output-hashes.json       groups convert/gcp and notifications/gcp
  tests/fixtures/dashboard-output-hashes.json     the gcp/ keys
  tests/fixtures/ir-before-grafana.json           the gcp/ keys (other sources must not move)
  tests/fixtures/taxonomy_before_grafana.json     reasons of the GCP fixtures (others must not move)

and removes the strict xfail markers on notifications/gcp (tests/test_emitter_byte_identity.py)
and on the gcp dashboards (tests/test_dashboard_ir_byte_identity.py). It never touches
tests/golden. If anything outside GCP would change, it stops and writes nothing.
"""

import argparse
import importlib.util
import json
import re
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"
GCP_ONLY_PREFIXES = ("gcp/", "convert/gcp", "notifications/gcp")
MARKER_PATTERNS = {
    TESTS / "test_emitter_byte_identity.py": (
        re.compile(
            r"        pytest\.param\(\n            \"notifications/gcp\",\n"
            r"            marks=pytest\.mark\.xfail\(.*?\n            \),\n        \),\n",
            re.S,
        ),
        '        "notifications/gcp",\n',
    ),
    TESTS / "test_dashboard_ir_byte_identity.py": (
        re.compile(r"PENDING_REVIEW = pytest\.mark\.xfail\(.*?\n\)\n\n\n", re.S),
        "",
    ),
}


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TESTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load tests/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_json(path: Path, data: Mapping[str, object], indent: int) -> None:
    path.write_text(json.dumps(data, indent=indent, sort_keys=True) + "\n")


def diff(old: Mapping[str, object], new: Mapping[str, object]) -> str:
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed = sorted(k for k in set(old) & set(new) if old[k] != new[k])
    lines = [f"    added {len(added)}, removed {len(removed)}, changed {len(changed)}"]
    lines += [f"      + {k}" for k in added]
    lines += [f"      - {k}" for k in removed]
    lines += [f"      ~ {k}" for k in changed]
    return "\n".join(lines)


def outside_gcp(old: Mapping[str, object], new: Mapping[str, object]) -> list[str]:
    keys = (set(old) | set(new)) - {k for k in set(old) | set(new) if k.startswith("gcp/")}
    return sorted(k for k in keys if old.get(k) != new.get(k))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the files (default: dry run)")
    apply = parser.parse_args().apply
    writes: list[tuple[Path, Mapping[str, object], int]] = []
    problems: list[str] = []

    emitter = load("test_emitter_byte_identity")
    dashboards = load("test_dashboard_ir_byte_identity")
    grafana_emit = load("test_grafana_emit")
    grafana_rules = load("test_grafana_rules")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        # 1. emitter hashes: only the two gcp groups may move
        path = FIXTURES / "emitter-output-hashes.json"
        old = json.loads(path.read_text())
        produced = emitter.collect(work / "emit")
        new = {
            g: (produced[g] if g in ("convert/gcp", "notifications/gcp") else old[g]) for g in old
        }
        problems += [
            f"emitter group {group} changed but is not GCP"
            for group in old
            if group not in ("convert/gcp", "notifications/gcp") and produced[group] != old[group]
        ]
        print(f"{path.relative_to(ROOT)}")
        for group in ("convert/gcp", "notifications/gcp"):
            print(f"  {group}\n{diff(old[group], new[group])}")
        writes.append((path, new, 2))

        # 2. dashboard hashes
        path = FIXTURES / "dashboard-output-hashes.json"
        old = json.loads(path.read_text())
        got = dashboards._hash_outputs("gcp", work / "dash")
        new = {**{k: v for k, v in old.items() if not k.startswith("gcp/")}, **got}
        print(f"{path.relative_to(ROOT)}\n{diff(old, new)}")
        writes.append((path, new, 2))

        # 3. saved IR of the other sources: gcp/ keys only
        path = FIXTURES / "ir-before-grafana.json"
        old = json.loads(path.read_text())
        now: dict[str, str] = {}
        for source, folder in (
            ("cloudwatch", "cloudwatch"),
            ("cloudwatch", "cloudwatch-real"),
            ("gcp", "gcp"),
            ("dynatrace", "dynatrace"),
        ):
            now |= grafana_emit.convert_hashes(source, folder, work / "ir")
        problems += [f"saved IR {k} changed but is not GCP" for k in outside_gcp(old, now)]
        print(f"{path.relative_to(ROOT)}\n{diff(old, now)}")
        writes.append((path, dict(now), 1))

        # 4. reason categories
        path = FIXTURES / "taxonomy_before_grafana.json"
        old = json.loads(path.read_text())
        monitors = []
        for source, folder in (
            ("cloudwatch", "cloudwatch"),
            ("cloudwatch", "cloudwatch-real"),
            ("gcp", "gcp"),
            ("dynatrace", "dynatrace"),
        ):
            monitors += grafana_rules.REGISTRY[source].convert(FIXTURES / folder)
        categories = {r: grafana_rules.classify(r).value for m in monitors for r in m.reasons}
        print(f"{path.relative_to(ROOT)}\n{diff(old, categories)}")
        for reason in set(old) & set(categories):
            if old[reason] != categories[reason]:
                problems.append(f"category of a reason changed: {reason[:80]}")
        writes.append((path, categories, 1))

    for path, (pattern, _replacement) in MARKER_PATTERNS.items():
        has = bool(pattern.search(path.read_text()))
        print(
            f"{path.relative_to(ROOT)}: strict xfail marker "
            f"{'present, would be removed' if has else 'already gone'}"
        )

    if problems:
        print("\nSTOPPED, nothing written:")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    if not apply:
        print("\nDry run: nothing written. Re-run with --apply after reading the output diff.")
        return 0
    for path, data, indent in writes:
        write_json(path, data, indent)
    for path, (pattern, replacement) in MARKER_PATTERNS.items():
        text = pattern.sub(replacement.replace("\\", "\\\\"), path.read_text())
        path.write_text(
            text.replace('pytest.param("gcp", marks=PENDING_REVIEW)', 'pytest.param("gcp")')
        )
    print("\nWritten. Run the tests, then commit the fixture files and the two test files.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
