"""Regenerate the pinned outputs that the combined wave 2 work changes on purpose.

    uv run python scripts/improve/regenerate_wave2_pins.py           # dry run
    uv run python scripts/improve/regenerate_wave2_pins.py --apply   # write it

Read docs/improvement/proposals/2026-11-11-wave2-combined-output-diff.md first: running this with
--apply is the owner's approval of that output. It rewrites exactly the four hashes in
tests/fixtures/dashboard-output-hashes.json that the proposal lists (the GCP sample dashboard
58944063589550: dashboard-ir json, nerdgraph json, dashboards.tf and report.md). It stops, and
writes nothing, if

- any other key of that file would change (added, removed or different),
- any other pinned group would change: the emitter groups, the saved IR of all sources, the
  reason categories (the same checks as regenerate_gcp_pins.py, read-only here),
- or tests/golden differs from HEAD.

It never touches tests/golden.
"""

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[2]
TESTS = ROOT / "tests"
FIXTURES = TESTS / "fixtures"
LISTED = (
    "gcp/dashboard-ir/58944063589550.json",
    "gcp/dashboards.tf",
    "gcp/nerdgraph/58944063589550.json",
    "gcp/report.md",
)
SOURCES = (
    ("cloudwatch", "cloudwatch"),
    ("cloudwatch", "cloudwatch-real"),
    ("gcp", "gcp"),
    ("dynatrace", "dynatrace"),
)


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, TESTS / f"{name}.py")
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load tests/{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def differing(old: Mapping[str, object], new: Mapping[str, object]) -> list[str]:
    return sorted(k for k in set(old) | set(new) if old.get(k) != new.get(k))


def golden_changed() -> bool:
    out = subprocess.run(
        ["git", "diff", "--name-only", "HEAD", "--", "tests/golden"],  # noqa: S607
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return bool(out.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    apply = parser.parse_args().apply
    problems: list[str] = []

    emitter = load("test_emitter_byte_identity")
    dashboards = load("test_dashboard_ir_byte_identity")
    grafana_emit = load("test_grafana_emit")
    grafana_rules = load("test_grafana_rules")

    if golden_changed():
        problems.append("tests/golden differs from HEAD; a human must review it first")

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        path = FIXTURES / "dashboard-output-hashes.json"
        old: dict[str, str] = json.loads(path.read_text())
        got: dict[str, str] = {}
        for source in ("cloudwatch", "gcp"):
            got |= dashboards._hash_outputs(source, work / f"dash-{source}")
        changed = differing(old, got)
        print(f"{path.relative_to(ROOT)}: {len(changed)} hash(es) differ")
        for key in changed:
            mark = "listed" if key in LISTED else "NOT LISTED"
            print(f"  ~ {key}  ({mark})")
        problems += [
            f"dashboard output {k} changed but is not listed" for k in changed if k not in LISTED
        ]
        problems += [f"listed output {k} did not change" for k in LISTED if k not in changed]
        new = {**old, **{k: got[k] for k in LISTED if k in got}}

        pinned = json.loads((FIXTURES / "emitter-output-hashes.json").read_text())
        produced = emitter.collect(work / "emit")
        problems += [f"emitter group {g} would change" for g in pinned if produced[g] != pinned[g]]

        saved = json.loads((FIXTURES / "ir-before-grafana.json").read_text())
        now: dict[str, str] = {}
        for source, folder in SOURCES:
            now |= grafana_emit.convert_hashes(source, folder, work / "ir")
        problems += [f"saved IR {k} would change" for k in differing(saved, now)]

        categories_pinned = json.loads((FIXTURES / "taxonomy_before_grafana.json").read_text())
        monitors = []
        for source, folder in SOURCES:
            monitors += grafana_rules.REGISTRY[source].convert(FIXTURES / folder)
        categories = {r: grafana_rules.classify(r).value for m in monitors for r in m.reasons}
        problems += [
            f"category of a reason would change: {r[:80]}"
            for r in set(categories_pinned) & set(categories)
            if categories_pinned[r] != categories[r]
        ]

    if problems:
        print("\nSTOPPED, nothing written:")
        print("\n".join(f"  {p}" for p in problems))
        return 1
    if not apply:
        print("\nDry run: nothing written. Re-run with --apply after reading the output diff.")
        return 0
    path.write_text(json.dumps(new, indent=2, sort_keys=True) + "\n")
    print("\nWritten. Run the tests, then commit tests/fixtures/dashboard-output-hashes.json.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
