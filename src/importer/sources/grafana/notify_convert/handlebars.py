"""A small Handlebars subset: enough to check and render the payload templates this package ships.

Supported: `{{var}}` (HTML-escaped, as Handlebars does), `{{json var}}`, `{{escape var}}`,
`{{#if var}}`, `{{#unless var}}`, `{{#eq var "text"}}`, `{{#each var}}` with `{{this}}`, and
`{{else}}`. Nothing else: no partials, no triple braces, no other helpers. A template that uses
anything else is refused, so a shipped payload cannot depend on behaviour this module does not test.

What the real New Relic engine does for `json` and `escape` is UNVERIFIED. Here `json` is
`json.dumps` of the value and `escape` is the content of a JSON string (no quotes), which is
what a payload needs. A variable that is missing renders as the empty string here; New Relic fails
on a missing nested value, so shipped templates guard anything optional with `{{#if}}`.
"""

import html
import json
import re
from dataclasses import dataclass, field
from typing import Any

MAX_OUTPUT = 100_000
MAX_EACH = 1000
MAX_DEPTH = 20

# The variable list of docs/research/newrelic-notifications-and-nrql-reference.md, section 1.2
# (New Relic's "custom variables" page). `accumulations.tag.<name>` and `[latest <enrichment>]`
# are open-ended and handled in `is_known`.
NR_VARIABLES = frozenset(
    [
        "accumulations.conditionDescription",
        "accumulations.conditionFamilyId",
        "accumulations.conditionName",
        "accumulations.conditionProduct",
        "accumulations.deepLinkUrl",
        "accumulations.evaluation.metricValueFunction",
        "accumulations.evaluation.name",
        "accumulations.nrqlQuery",
        "accumulations.origins",
        "accumulations.policyName",
        "accumulations.runbookUrl",
        "accumulations.sources",
        "accumulations.tag.account",
        "accumulations.tag.affectedService",
        "accumulations.tag.assignmentGroup",
        "accumulations.tag.causeService",
        "acknowledgedBy",
        "activatedAt",
        "annotations.description",
        "annotations.title",
        "annotations.wildcard",
        "closedBy",
        "closedAt",
        "closedIncidentsCount",
        "createdAt",
        "dataMLModules.components",
        "dataMLModules.goldenSignals",
        "dataMLModules.suggestedResponders",
        "entitiesData.entities",
        "entitiesData.ids",
        "entitiesData.kinds",
        "entitiesData.names",
        "entitiesData.types",
        "impactedEntitiesCount",
        "incidentIds",
        "isCorrelated",
        "isAcknowledged",
        "issueAckUrl",
        "issueCloseUrl",
        "issuePageUrl",
        "issueActivatedAtUtc",
        "issueClosedAtUtc",
        "issueCreatedAtUtc",
        "issueAcknowledgedAt",
        "issueClosedAt",
        "issueDurationMs",
        "issueDurationText",
        "issueId",
        "issueTitle",
        "issueUpdatedAt",
        "labels.accountIds",
        "labels.aggregationKeys",
        "labels.originalAccountIds",
        "labels.policyIds",
        "labels.targetId",
        "mutingState",
        "nrAccountId",
        "openIncidentsCount",
        "owner",
        "policyUrl",
        "priority",
        "priorityText",
        "realIssueCount",
        "state",
        "stateText",
        "status",
        "totalIncidents",
        "triggeredAt",
        "triggerEvent",
        "updatedAt",
        "violationChartUrl",
        "workflowName",
    ]
)

_SEGMENT = r"(?:[A-Za-z_][A-Za-z0-9_:-]{0,63}|\[[^\]\s][^\]]{0,63}\])"
PATH = re.compile(rf"{_SEGMENT}(?:\.{_SEGMENT}){{0,8}}")
_INDEX = re.compile(r"\.\[\d{1,3}\]")
_PIECE = re.compile(r"\[[^\]]+\]|[^.\[\]]+")
_LITERAL = re.compile(r'"([^"\\]{0,200})"')
_HELPERS = {"json", "escape"}
_BLOCKS = {"if", "unless", "eq", "each"}
_HTML_EXTRA = {"=": "&#x3D;", "`": "&#x60;", "'": "&#x27;"}


class TemplateError(ValueError):
    """A template this module will not check or render. The text names no template content."""


@dataclass
class _Node:
    kind: str  # text | var | block
    text: str = ""  # text: the text; var: the path; block: the argument path
    mode: str = ""  # var: plain | json | escape; block: if | unless | eq | each
    literal: str = ""  # eq only
    body: list["_Node"] = field(default_factory=list)
    other: list["_Node"] = field(default_factory=list)  # the else branch


def _tags(source: str) -> list[tuple[str, str]]:
    """(kind, content) pieces: text, or the stripped content of a tag."""
    pieces: list[tuple[str, str]] = []
    pos = 0
    while True:
        start = source.find("{{", pos)
        if start < 0:
            pieces.append(("text", source[pos:]))
            return pieces
        if source.startswith("{{{", start):
            raise TemplateError("triple braces are not supported")
        end = source.find("}}", start)
        if end < 0:
            raise TemplateError("unterminated tag")
        pieces.append(("text", source[pos:start]))
        pieces.append(("tag", source[start + 2 : end].strip()))
        pos = end + 2


def _path(word: str) -> str:
    if word != "this" and not PATH.fullmatch(word):
        raise TemplateError(f"not a variable: {word[:40]!r}")
    return word


def _tag_node(content: str) -> _Node:
    """A variable or helper tag (not a block open or close)."""
    words = content.split()
    if not words:
        raise TemplateError("empty tag")
    if words[0] in _HELPERS:
        if len(words) != 2:
            raise TemplateError(f"{words[0]} needs one variable")
        return _Node("var", _path(words[1]), words[0])
    if len(words) > 1:
        raise TemplateError(f"unsupported helper {words[0][:20]}")
    return _Node("var", _path(words[0]), "plain")


def _open_node(content: str) -> _Node:
    name, _, rest = content[1:].strip().partition(" ")
    if name not in _BLOCKS:
        raise TemplateError(f"unsupported block {name[:20]}")
    rest = rest.strip()
    if name != "eq":
        return _Node("block", _path(rest.split()[0]) if rest else "", name)
    arg, _, literal = rest.partition(" ")
    found = _LITERAL.fullmatch(literal.strip())
    if not arg or found is None:
        raise TemplateError('eq needs a variable and a quoted text, like {{#eq state "CLOSED"}}')
    return _Node("block", _path(arg), "eq", found.group(1))


def parse(source: str) -> list[_Node]:
    root: list[_Node] = []
    stack: list[tuple[_Node, bool]] = []  # (block, in the else branch)

    def current() -> list[_Node]:
        if not stack:
            return root
        block, in_else = stack[-1]
        return block.other if in_else else block.body

    for kind, content in _tags(source):
        if kind == "text":
            if content:
                current().append(_Node("text", content))
        elif content.startswith("#"):
            node = _open_node(content)
            current().append(node)
            stack.append((node, False))
            if len(stack) > MAX_DEPTH:
                raise TemplateError("blocks are nested too deeply")
        elif content.startswith("/"):
            name = content[1:].strip()
            if not stack:
                raise TemplateError(f"unexpected {{{{/{name[:20]}}}}}")
            if stack[-1][0].mode != name:
                raise TemplateError(
                    f"{{{{/{name[:20]}}}}} does not close {{{{#{stack[-1][0].mode}}}}}"
                )
            stack.pop()
        elif content == "else":
            if not stack or stack[-1][1]:
                raise TemplateError("else outside a block")
            stack[-1] = (stack[-1][0], True)
        else:
            current().append(_tag_node(content))
    if stack:
        raise TemplateError(f"unclosed {{{{#{stack[-1][0].mode}}}}}")
    return root


def _walk(nodes: list[_Node]) -> list[_Node]:
    found: list[_Node] = []
    for node in nodes:
        found.append(node)
        found += _walk(node.body) + _walk(node.other)
    return found


def variables(source: str) -> set[str]:
    """Every variable path the template reads (not `this`)."""
    return {n.text for n in _walk(parse(source)) if n.kind != "text" and n.text not in ("", "this")}


def is_known(path: str) -> bool:
    """Is the path a New Relic notification variable (or a tag or enrichment path)?"""
    plain = _INDEX.sub("", path)
    return (
        plain in NR_VARIABLES
        or bool(re.fullmatch(r"accumulations\.tag\.[A-Za-z0-9_:-]{1,64}", plain))
        or bool(re.fullmatch(r"\[latest [A-Za-z0-9_]{1,64}\]", plain))
    )


def check(source: str) -> list[str]:
    """Problems with the template: syntax this module does not support, unknown variables."""
    try:
        names = sorted(variables(source))
    except TemplateError as exc:
        return [str(exc)]
    return [f"unknown variable {n}" for n in names if not is_known(n)]


def json_position_problems(source: str) -> list[str]:
    """A JSON body may print a value only through `json` or `escape`; a plain `{{x}}` is not
    JSON-safe (Handlebars does not escape a backslash or a newline)."""
    plain = sorted({n.text for n in _walk(parse(source)) if n.kind == "var" and n.mode == "plain"})
    return [f"plain variable {n} in a JSON body" for n in plain]


# --- rendering ---


def _lookup(path: str, stack: list[Any]) -> Any:
    if path == "this":
        return stack[-1]
    value: Any = stack[0]
    for piece in _PIECE.findall(path):
        if piece.startswith("[") and piece[1:-1].isdigit():
            index = int(piece[1:-1])
            value = value[index] if isinstance(value, list) and index < len(value) else None
        elif isinstance(value, dict):
            value = value.get(piece)
        else:
            return None
        if value is None:
            return None
    return value


def _truthy(value: Any) -> bool:
    return value not in (None, "", False, [], {}, 0)


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ",".join(_text(v) for v in value)
    return str(value)


def _html(text: str) -> str:
    return "".join(_HTML_EXTRA.get(c) or html.escape(c, quote=True) for c in text)


def _show(node: _Node, value: Any) -> str:
    if node.mode == "json":
        return json.dumps(value if value is not None else "")
    if node.mode == "escape":
        return json.dumps(_text(value))[1:-1]
    return _html(_text(value))


def _render(nodes: list[_Node], stack: list[Any], out: list[str], size: list[int]) -> None:
    for node in nodes:
        if node.kind == "text":
            piece = node.text
        elif node.kind == "var":
            piece = _show(node, _lookup(node.text, stack))
        else:
            _render_block(node, stack, out, size)
            continue
        size[0] += len(piece)
        if size[0] > MAX_OUTPUT:
            raise TemplateError("template output too large")
        out.append(piece)


def _render_block(node: _Node, stack: list[Any], out: list[str], size: list[int]) -> None:
    value = _lookup(node.text, stack)
    if node.mode == "each":
        items = value[:MAX_EACH] if isinstance(value, list) else []
        for item in items:
            _render(node.body, [*stack, item], out, size)
        if not items:
            _render(node.other, stack, out, size)
        return
    held = {
        "if": _truthy(value),
        "unless": not _truthy(value),
        "eq": _text(value) == node.literal,
    }[node.mode]
    _render(node.body if held else node.other, stack, out, size)


def render(source: str, data: dict[str, Any]) -> str:
    """Render the template with `data`; raises TemplateError (a ValueError) on unsupported text."""
    try:
        nodes = parse(source)
        out: list[str] = []
        _render(nodes, [data], out, [0])
    except TemplateError as exc:
        raise TemplateError(f"template: {exc}") from None
    return "".join(out)
