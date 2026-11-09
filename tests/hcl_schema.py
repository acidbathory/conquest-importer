"""Check emitted Terraform against an extract of the newrelic provider schema (3.101.12).

The extract (tests/fixtures/provider-schema-extract.json) was made from the offline schema with
`terraform providers schema -json`. This reads our own emitter output, one construct per line, and
reports unknown attributes, unknown blocks, missing required attributes, required list attributes
set to an empty list, blocks used too many or too few times, `var.X` references without a
`variable "X"` block, and (for chosen keys) `=` signs that `terraform fmt` would align.
"""

import json
import re
from pathlib import Path
from typing import Any

EXTRACT = Path(__file__).parent / "fixtures" / "provider-schema-extract.json"
_RESOURCE = re.compile(r'^resource "(\w+)" "[\w-]+" \{$')
_BLOCK = re.compile(r"^(\w+) \{$")
_ATTR = re.compile(r"^(\w+)\s*=\s*(.*)$")
_VAR_REF = re.compile(r"\bvar\.([A-Za-z_][A-Za-z0-9_-]*)")
_VAR_DECL = re.compile(r'^variable "([^"]+)" \{$', re.MULTILINE)
_SKIP = ("terraform", "provider", "variable", "required_providers")


def provider_schema() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(EXTRACT.read_text())
    return data


def _check_block(schema: dict[str, Any], seen: dict[str, Any], where: str) -> list[str]:
    errors: list[str] = []
    for name, flag in schema["attrs"].items():
        if flag == "R" and name not in seen["attrs"]:
            errors.append(f"{where}: required attribute {name} is missing")
        if flag == "R" and seen["attrs"].get(name) == "[]":
            errors.append(f"{where}: required attribute {name} is an empty list")
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
            stack[-1]["attrs"][match.group(1)] = match.group(2)
    return resources


def undeclared_variables(tf: str) -> list[str]:
    """Names used as `var.X` that have no `variable "X"` block in the same text."""
    declared = set(_VAR_DECL.findall(tf))
    return sorted(set(_VAR_REF.findall(tf)) - declared)


def schema_errors(tf: str) -> list[str]:
    schema = provider_schema()
    errors: list[str] = []
    for rtype, tree in _parse(tf):
        if rtype in schema:
            errors += _check_block(schema[rtype], tree, rtype)
    errors += [f"variable {name} is used but not declared" for name in undeclared_variables(tf)]
    return errors


def resource_count(tf: str) -> int:
    schema = provider_schema()
    return sum(1 for rtype, _ in _parse(tf) if rtype in schema)


def fmt_errors(tf: str, keys: frozenset[str] | None = None) -> list[str]:
    """Groups of consecutive attribute lines whose `=` is not aligned the way terraform fmt does.

    Only groups holding a key in `keys` are checked when it is given (the generated conditions
    keep earlier layouts for their older lines, which are pinned byte for byte).
    """
    errors: list[str] = []
    group: list[tuple[str, str, str]] = []  # (indent, key, rest of the line from the '=')

    def flush() -> None:
        if group and (keys is None or any(k in keys for _, k, _ in group)):
            width = max(len(k) for _, k, _ in group)
            for indent, key, rest in group:
                if f"{indent}{key.ljust(width)} = " != f"{indent}{key}{rest.split('=')[0]}= ":
                    errors.append(f"not aligned: {indent}{key}{rest}")
        group.clear()

    for line in tf.splitlines():
        match = re.match(r"^(\s*)(\w+)(\s*=\s.*)$", line)
        if match and (not group or group[0][0] == match.group(1)):
            group.append((match.group(1), match.group(2), match.group(3)))
        else:
            flush()
            if match:
                group.append((match.group(1), match.group(2), match.group(3)))
    flush()
    return errors
