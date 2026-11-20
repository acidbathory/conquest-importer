"""Read untrusted YAML as data, the way a YAML 1.2 reader such as Grafana's would.

PyYAML follows YAML 1.1: `1e+07` is a string there (a float needs a dot), `yes`/`on` are
booleans and `0755` is octal. Exports written by Go tools are YAML 1.2, so this module swaps
in resolvers close to the 1.2 core schema. A leading-zero integer stays a string because Go
readers disagree on it (yaml.v3 reads `0755` as octal 493, the 1.2 core schema as 755).

Anchors, aliases and explicit tags are refused outright: an alias bomb expands in memory, and
no rule export uses them. Nesting is checked on the event stream before anything is built, so
the composer (which recurses) never sees a deep document.
"""

from __future__ import annotations

import re
from typing import Any

import yaml
from yaml.nodes import ScalarNode

from importer.core.json_depth import MAX_JSON_DEPTH, TooDeepError
from importer.core.text import printable

try:  # libyaml parses a 16 MB export in seconds; the pure-Python parser takes far longer
    from yaml import CSafeLoader as _SafeLoader
except ImportError:  # pragma: no cover - PyYAML wheels ship libyaml
    from yaml import SafeLoader as _SafeLoader  # type: ignore[assignment]

_INT = "tag:yaml.org,2002:int"
_CORE_RESOLVERS = (  # (tag, pattern, first characters); YAML 1.2 core schema, minus 0-prefixed
    ("tag:yaml.org,2002:null", r"~|null|Null|NULL|", ["~", "n", "N", ""]),
    ("tag:yaml.org,2002:bool", r"true|True|TRUE|false|False|FALSE", list("tTfF")),
    (_INT, r"[-+]?(?:0|[1-9][0-9]*)|0o[0-7]+|0x[0-9a-fA-F]+", list("-+0123456789")),
    (
        "tag:yaml.org,2002:float",
        r"[-+]?(?:\.[0-9]+|[0-9]+\.[0-9]*)(?:[eE][-+]?[0-9]+)?|[-+]?[0-9]+[eE][-+]?[0-9]+"
        r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)",
        list("-+.0123456789"),
    ),
)
_NO_TAG = (None, "!")  # plain or quoted scalars; `!` is the non-specific tag (a string)


class _Loader(_SafeLoader):
    """The safe loader with the resolvers above in place of PyYAML's YAML 1.1 set."""


def _construct_int(loader: _Loader, node: ScalarNode) -> int:
    value = str(loader.construct_scalar(node))
    return int(value, 0) if value[:2] in ("0o", "0x") else int(value, 10)


_Loader.yaml_implicit_resolvers = {}  # its own table, so SafeLoader itself is left alone
for _tag, _pattern, _first in _CORE_RESOLVERS:
    _Loader.add_implicit_resolver(_tag, re.compile(f"^(?:{_pattern})$"), _first)
_Loader.add_constructor(_INT, _construct_int)


def _where(error: yaml.YAMLError) -> str:
    """The line and PyYAML's fixed wording, without the snippet of input it quotes."""
    if not isinstance(error, yaml.MarkedYAMLError):
        return "it is not valid YAML"
    mark = error.problem_mark or error.context_mark
    line = f" at line {mark.line + 1}" if mark else ""
    return f"it is not valid YAML{line}: {printable(error.problem or 'unreadable')}"


def _check_events(text: str, name: str, max_depth: int) -> None:
    """Refuse anchors, aliases, tags, deep nesting and extra documents before building."""
    depth = documents = 0
    for event in yaml.parse(text, Loader=_Loader):
        if isinstance(event, yaml.AliasEvent) or getattr(event, "anchor", None):
            raise ValueError(f"{name} uses a YAML anchor or alias; pass an export without them")
        if isinstance(event, yaml.DocumentStartEvent):
            documents += 1
            if documents > 1:
                raise ValueError(f"{name} holds more than one YAML document; split the file")
        if getattr(event, "tag", None) not in _NO_TAG:
            raise ValueError(f"{name} uses an explicit YAML tag; pass an export without tags")
        if isinstance(event, yaml.CollectionStartEvent):
            depth += 1
            if depth > max_depth:
                raise TooDeepError(max_depth)
        elif isinstance(event, yaml.CollectionEndEvent):
            depth -= 1


def load_yaml(text: str, name: str, max_depth: int = MAX_JSON_DEPTH) -> Any:
    """The one document in `text` as plain data; `name` (a file name) labels every error."""
    try:
        _check_events(text, name, max_depth)
        return yaml.load(text, Loader=_Loader)  # noqa: S506  (safe constructor, no aliases)
    except yaml.YAMLError as error:
        raise ValueError(f"{name}: {_where(error)}") from None
