"""Check emitted Terraform against an extract of the newrelic provider schema (3.101.12).

The extract (tests/fixtures/provider-schema-extract.json) was made from the offline schema with
`terraform providers schema -json`. This reads our own emitter output, one construct per line, and
reports unknown attributes, unknown blocks, missing required attributes and blocks used too many
or too few times.
"""

import json
import re
from pathlib import Path
from typing import Any

EXTRACT = Path(__file__).parent / "fixtures" / "provider-schema-extract.json"
_RESOURCE = re.compile(r'^resource "(\w+)" "[\w-]+" \{$')
_BLOCK = re.compile(r"^(\w+) \{$")
_ATTR = re.compile(r"^(\w+)\s*=")
_SKIP = ("terraform", "provider", "variable", "required_providers")


def provider_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(EXTRACT.read_text())
    return data


def _check_block(schema: dict[str, Any], seen: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    for name, flag in schema["attrs"].items():
        if flag == "R" and name not in seen["attrs"]:
            errors.append(f"{where}: required attribute {name} is missing")
    errors += [
        f"{where}: unknown attribute {name}"
        for name in seen["attrs"]
        if name not in schema["attrs"]
    ]
    for name, children in seen["blocks"].items():
        sub = schema["blocks"].get(name)
        if sub is None:
            errors.append(f"{where}: unknown block {name}")
            continue
        if sub["max"] and len(children) > sub["max"]:
            errors.append(f"{where}.{name}: {len(children)} blocks, at most {sub['max']}")
        for child in children:
            errors += _check_block(sub, child, f"{where}.{name}")
    for name, sub in schema["blocks"].items():
        if sub["min"] and name not in seen["blocks"]:
            errors.append(f"{where}: block {name} is required")
    return errors


def _parse(tf: str) -> list[tuple[str, dict[str, Any]]]:
    """(resource type, tree) for every resource block; a tree is {attrs: {...}, blocks: {...}}."""
    resources: list[tuple[str, dict[str, Any]]] = []
    stack: list[dict[str, Any]] = []
    skipping = 0
    for raw in tf.splitlines():
        line = raw.strip()
        if skipping:
            skipping += line.endswith("{") - (line == "}")
            continue
        if line.split(" ")[0] in _SKIP and line.endswith("{"):
            skipping = 1
            continue
        if match := _RESOURCE.match(line):
            tree: dict[str, Any] = {"attrs": {}, "blocks": {}}
            resources.append((match.group(1), tree))
            stack = [tree]
        elif stack and (match := _BLOCK.match(line)):
            child: dict[str, Any] = {"attrs": {}, "blocks": {}}
            stack[-1]["blocks"].setdefault(match.group(1), []).append(child)
            stack.append(child)
        elif stack and line == "}":
            stack.pop()
        elif stack and (match := _ATTR.match(line)):
            stack[-1]["attrs"][match.group(1)] = True
    return resources


def schema_errors(tf: str) -> list[str]:
    schema = provider_schema()
    errors: list[str] = []
    for rtype, tree in _parse(tf):
        if rtype in schema:
            errors += _check_block(schema[rtype], tree, rtype)
    return errors


def resource_count(tf: str) -> int:
    schema = provider_schema()
    return sum(1 for rtype, _ in _parse(tf) if rtype in schema)
