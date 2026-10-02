import importlib.util
import json
import re
from pathlib import Path
from types import ModuleType

SCRIPTS = Path(__file__).parent.parent / "scripts"


def load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "create_sample", SCRIPTS / "create_sample_monitors.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_twenty_definitions_with_unique_names() -> None:
    defs = json.loads((SCRIPTS / "sample_monitors.json").read_text())
    assert len(defs) == 20
    assert len({d["name"] for d in defs}) == 20


def test_composite_reference_resolves_to_ids() -> None:
    mod = load_script()
    defs = {d["name"]: d for d in json.loads((SCRIPTS / "sample_monitors.json").read_text())}
    payload = mod.build_payload(
        defs["[sample] 17 Composite CPU and memory"], {"cpu_high": 11, "mem_high": 22}
    )
    assert payload["query"] == "11 && 22"
    assert payload["tags"] == ["conquest-importer-sample"]


def test_every_reference_points_to_an_earlier_monitor() -> None:
    defs = json.loads((SCRIPTS / "sample_monitors.json").read_text())
    seen: set[str] = set()
    for d in defs:
        for ref in re.findall(r"\{ref:([a-z_]+)\}", d["query"]):
            assert ref in seen
        if "ref" in d:
            seen.add(d["ref"])
