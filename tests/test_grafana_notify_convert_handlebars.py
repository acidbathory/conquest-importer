"""The small Handlebars subset used to check payload templates: render, validate, escape."""

import json

import pytest

from importer.sources.grafana.notify_convert import handlebars as hb

DATA = {
    "issueTitle": 'He said "hi" \\ and\nleft',
    "state": "ACTIVATED",
    "stateText": "active",
    "issuePageUrl": "https://one.newrelic.com/x?a=1&b=2",
    "accumulations": {"conditionName": ["a", "b"], "tag": {"team": ["t1"]}},
    "empty": "",
}


@pytest.mark.parametrize(
    ("template", "expected"),
    [
        ("{{stateText}}", "active"),
        ("{{ stateText }}", "active"),
        ("{{json state}}", '"ACTIVATED"'),
        ("{{json accumulations.conditionName}}", '["a", "b"]'),
        ("{{escape issueTitle}}", json.dumps(DATA["issueTitle"])[1:-1]),
        ("{{#if state}}yes{{else}}no{{/if}}", "yes"),
        ("{{#if empty}}yes{{else}}no{{/if}}", "no"),
        ("{{#if missing}}yes{{/if}}", ""),
        ("{{#unless empty}}u{{/unless}}", "u"),
        ('{{#eq state "ACTIVATED"}}on{{else}}off{{/eq}}', "on"),
        ('{{#eq state "CLOSED"}}on{{else}}off{{/eq}}', "off"),
        ("{{#each accumulations.conditionName}}<{{this}}>{{/each}}", "<a><b>"),
        ('{{#if state}}{{#eq state "CLOSED"}}c{{else}}o{{/eq}}{{/if}}', "o"),
        ("a{{missing}}b", "ab"),
        ("{{accumulations.tag.team}}", "t1"),
    ],
)
def test_render(template: str, expected: str) -> None:
    assert hb.render(template, DATA) == expected


def test_a_plain_variable_is_html_escaped_like_handlebars() -> None:
    assert hb.render("{{issuePageUrl}}", DATA) == "https://one.newrelic.com/x?a&#x3D;1&amp;b&#x3D;2"


@pytest.mark.parametrize(
    ("template", "fragment"),
    [
        ("{{#if x}}open", "unclosed"),
        ("{{/if}}", "unexpected"),
        ("{{#if x}}{{/each}}", "does not close"),
        ("{{{raw}}}", "triple"),
        ("{{#with x}}{{/with}}", "unsupported"),
        ("{{lookup a b}}", "unsupported"),
        ("{{json}}", "variable"),
        ("{{json a b}}", "variable"),
        ("{{#eq state}}x{{/eq}}", "eq"),
        ("{{ unterminated", "unterminated"),
        ("{{else}}", "else"),
    ],
)
def test_bad_templates_are_refused(template: str, fragment: str) -> None:
    problems = hb.check(template)
    assert any(fragment in p for p in problems), problems
    with pytest.raises(ValueError, match="template"):
        hb.render(template, DATA)


@pytest.mark.parametrize("name", ["issueTitle", "accumulations.tag.team", "priorityText"])
def test_real_variables_pass(name: str) -> None:
    assert hb.check("{{" + name + "}}") == []


@pytest.mark.parametrize("name", ["issueTittle", "accumulations.tags.team", "payload", "a.b"])
def test_invented_variables_are_found(name: str) -> None:
    assert hb.check("{{" + name + "}}") == [f"unknown variable {name}"]


def test_variables_lists_every_name_once() -> None:
    template = '{{json issueId}} {{#if state}}{{state}}{{/if}} {{#eq priority "HIGH"}}{{/eq}}'
    assert hb.variables(template) == {"issueId", "state", "priority"}


def test_the_variable_list_has_the_documented_keys() -> None:
    assert len(hb.NR_VARIABLES) == 72
    assert {"issueId", "workflowName", "accumulations.conditionName"} <= hb.NR_VARIABLES


def test_output_is_bounded() -> None:
    big = "{{#each items}}" + "x" * 1000 + "{{/each}}"
    with pytest.raises(ValueError, match="too large"):
        hb.render(big, {"items": list(range(10_000))})


def test_json_positions_must_use_json_or_escape() -> None:
    assert hb.json_position_problems('{"a": {{json issueId}}, "b": "{{escape issueTitle}}"}') == []
    assert hb.json_position_problems('{"a": "{{issueTitle}}"}') == [
        "plain variable issueTitle in a JSON body"
    ]
