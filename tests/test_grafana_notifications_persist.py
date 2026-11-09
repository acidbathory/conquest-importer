"""Policy trees survive a round trip through the files; extraction is atomic and private."""

import json
import os
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest

from importer.sources.grafana.extract import GrafanaError
from importer.sources.grafana.notifications import extract as nx
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.loader import load_export
from importer.sources.grafana.notifications.models import MAX_POLICY_DEPTH, MAX_POLICY_NODES
from importer.sources.grafana.notifications.payloads import policy_tree_to_payload
from importer.sources.grafana.notifications.policies import parse_matcher_string, parse_policy_tree
from importer.sources.grafana.notifications.policy import RuleLabels, flatten_policy_tree

HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_TOKENTOKEN"  # noqa: S105 - a made-up value for leak tests


def serve(policies: Any, templates: Any = None) -> Any:
    table = {
        nx.CONTACT_POINTS: [
            {"name": "mail", "type": "email", "settings": {"addresses": "a@example.com"}}
        ],
        nx.POLICIES: policies,
        nx.MUTE_TIMINGS: [],
        nx.TEMPLATES: [] if templates is None else templates,
        nx.DATASOURCES: [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path in table:
            return httpx.Response(200, json=table[request.url.path])
        return httpx.Response(404, json={})

    return handler


def run(handler: Any, out: Path) -> nx.NotificationsResult:
    with nx.notifications_getter(TOKEN, HOST, transport=httpx.MockTransport(handler)) as get:
        return nx.extract_notifications(get, out)


def deep(levels: int) -> dict[str, Any]:
    raw: dict[str, Any] = {"receiver": "leaf"}
    for _ in range(levels):
        raw = {"receiver": "n", "routes": [raw]}
    return raw


CUTS = [
    pytest.param(deep(MAX_POLICY_DEPTH + 5), id="depth"),
    pytest.param(
        {"receiver": "r", "routes": [{"receiver": "x"}] * (MAX_POLICY_NODES + 10)}, id="node-cap"
    ),
    pytest.param(
        {"receiver": "r", "routes": [{"receiver": "x"}, "not an object", 3]}, id="non-object-route"
    ),
]


@pytest.mark.parametrize("policies", CUTS)
def test_a_cut_tree_stays_cut_after_the_round_trip(policies: Any, tmp_path: Path) -> None:
    result = run(serve(policies), tmp_path / "o")
    assert any("policy" in w and "cut" in w for w in result.warnings)
    export = load_export(tmp_path / "o")
    assert export.policy is not None and export.policy.incomplete
    plan = flatten_policy_tree(export.policy, [RuleLabels(name="a", by_labels=frozenset())])
    assert plan.entries == []
    assert [(u.rule, u.reason) for u in plan.undecidable] == [("a", R.R_POLICY_INCOMPLETE)]


def test_a_complete_tree_is_not_marked_and_gives_no_warning(tmp_path: Path) -> None:
    result = run(serve(deep(3)), tmp_path / "o")
    assert result.warnings == []
    saved = json.loads((tmp_path / "o" / "policies.json").read_text())
    assert "_incomplete" not in saved
    export = load_export(tmp_path / "o")
    assert export.policy is not None and not export.policy.incomplete


def test_the_marker_is_honoured_by_the_parser_and_unknown_problems_are_ignored() -> None:
    tree = parse_policy_tree({"receiver": "r", "_incomplete": True, "_problems": ["x" * 50, 7]})
    assert tree.incomplete and all(len(p) < 200 for p in tree.problems)
    assert "x" * 50 not in " ".join(tree.problems)


def test_the_payload_round_trips_every_field() -> None:
    raw = {
        "receiver": "root",
        "group_by": ["a"],
        "group_wait": "0s",
        "group_interval": "5m",
        "repeat_interval": "4h",
        "routes": [
            {
                "receiver": "x",
                "object_matchers": [["k", "=~", "a|b"], ["j", "!=", "v"]],
                "continue": True,
                "mute_time_intervals": ["m"],
                "active_time_intervals": ["w"],
                "routes": [{"matchers": ["garbage"]}, {"group_by": []}],
            }
        ],
    }
    tree = parse_policy_tree(raw)
    again = parse_policy_tree(policy_tree_to_payload(tree))
    assert again == tree
    assert again.root.group_wait_seconds == 0 and again.root.routes[0].continue_


# --- atomic and private output ---


def test_a_recursion_error_in_a_response_is_a_clean_error_and_leaves_no_folder(
    tmp_path: Path,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == nx.TEMPLATES:
            return httpx.Response(200, content=b"[" * 200_000)
        response: httpx.Response = serve({"receiver": "r"})(request)
        return response

    out = tmp_path / "o"
    result = run(handler, out)
    assert any("templates" in w and "NOT read" in w for w in result.warnings)
    assert (out / "contact-points.json").exists()


def test_any_failure_while_writing_leaves_no_partial_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = tmp_path / "o"
    monkeypatch.setattr(nx, "MAX_INPUT_BYTES", 10)
    with pytest.raises(GrafanaError):
        run(serve({"receiver": "r"}), out)
    assert not out.exists() and list(tmp_path.iterdir()) == []
    monkeypatch.undo()
    run(serve({"receiver": "r"}), out)  # a re-run works
    assert (out / "policies.json").exists()


def test_a_crash_inside_a_section_leaves_no_temporary_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def boom(*_a: Any) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(nx, "_commit", boom)
    with pytest.raises(RuntimeError):
        run(serve({"receiver": "r"}), tmp_path / "o")
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(os.name != "posix", reason="file modes are POSIX")
def test_files_are_written_for_the_owner_only(tmp_path: Path) -> None:
    run(serve({"receiver": "r"}), tmp_path / "o")
    for path in (tmp_path / "o").rglob("*"):
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode & 0o077 == 0, (path.name, oct(mode))


# --- the parser details ---


@pytest.mark.parametrize(
    "text",
    ['a!="b",c="d"', 'a="b"c', 'a="b\\', 'a="', "a==b", 'a="b" c', "a=b,c", "a=~", 'a="x"y="z"'],
)
def test_malformed_matchers_are_not_matchers(text: str) -> None:
    assert parse_matcher_string(text) is None


@pytest.mark.parametrize("text", ['a="b"', "a=b", 'a=~"x|y"', 'a!=""', 'a=""'])
def test_wellformed_matchers_still_parse(text: str) -> None:
    assert parse_matcher_string(text) is not None


def test_an_explicit_zero_timing_is_kept_not_inherited() -> None:
    tree = parse_policy_tree(
        {"receiver": "r", "group_wait": "30s", "routes": [{"group_wait": "0s", "receiver": "x"}]}
    )
    assert tree.root.group_wait_seconds == 30
    assert tree.root.routes[0].group_wait_seconds == 0
    plan = flatten_policy_tree(tree, [RuleLabels(name="a", by_labels=frozenset())])
    assert plan.entries[0].receiver == "x" and R.R_POLICY_TIMING in plan.entries[0].reasons


def test_a_matcher_value_that_looks_like_a_token_makes_the_node_undecidable() -> None:
    tree = parse_policy_tree(
        {"receiver": "r", "routes": [{"receiver": "x", "matchers": ['team="a@b.example"']}]}
    )
    node = tree.root.routes[0]
    assert node.matchers == [] and node.unparsed_matchers == 1
    assert "a@b.example" not in tree.model_dump_json()


def test_receiver_and_interval_names_that_look_sensitive_become_stable_references() -> None:
    tree = parse_policy_tree(
        {
            "receiver": "oncall@example.com",
            "routes": [{"receiver": "oncall@example.com", "mute_time_intervals": ["a?b"]}],
        }
    )
    assert "@" not in tree.model_dump_json() and "?" not in tree.model_dump_json()
    assert tree.root.receiver == tree.root.routes[0].receiver and tree.root.receiver.startswith(
        "redacted-"
    )


def test_the_command_summary_says_when_the_policy_tree_was_cut(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    monkeypatch.setenv("GRAFANA_URL", HOST)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    monkeypatch.setattr(
        "importer.commands.grafana_notifications.notifications_getter",
        lambda t, b, allow_host=None: nx.notifications_getter(
            t, b, httpx.MockTransport(serve(deep(MAX_POLICY_DEPTH + 5))), allow_host=allow_host
        ),
    )
    result = CliRunner().invoke(
        app, ["extract-grafana-notifications", "--out", str(tmp_path / "o")]
    )
    assert result.exit_code == 0 and "policy tree was cut" in result.output


def test_the_command_help_says_what_is_written_and_what_is_not() -> None:
    from typer.testing import CliRunner

    from importer.cli import app

    text = CliRunner().invoke(app, ["extract-grafana-notifications", "--help"]).output
    for phrase in ("What is written", "rule annotations", "owner only", "Nothing is written if"):
        assert " ".join(phrase.split()) in " ".join(text.split())
