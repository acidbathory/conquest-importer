"""Hostile fixtures through the whole read side: no secret-like value in any output.

LEAK marks a value that must never be written anywhere. HOSTILE marks an untrusted name, which
may appear in the export files (it identifies the object) but never in a reason, a step, a note
or what a command prints. EMAILADDR marks an email address: contact-points.json only.
"""

import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from importer.cli import app
from importer.sources.grafana.datapath.plan import build_files
from importer.sources.grafana.notifications import extract as nx
from importer.sources.grafana.notifications import reasons as R
from importer.sources.grafana.notifications.loader import load_export
from importer.sources.grafana.notifications.muting import mute_timing_to_muting
from importer.sources.grafana.notifications.policy import RuleLabels, flatten_policy_tree
from importer.sources.grafana.notifications.promrules import to_grafana_rule
from importer.sources.grafana.notifications.templates import translate_template
from importer.sources.grafana.notifications.verdicts import Verdict, contact_point_verdict

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-notifications"
HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_TOKENTOKEN"  # noqa: S105 - a made-up value for leak tests
NEVER = ("LEAK", "TOKENTOKEN")


def body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def serve(request: httpx.Request) -> httpx.Response:
    table = {
        nx.CONTACT_POINTS: body("contact-points.json"),
        nx.POLICIES: body("policies.json"),
        nx.MUTE_TIMINGS: body("mute-timings.json"),
        nx.TEMPLATES: body("templates.json"),
        nx.DATASOURCES: body("datasources.json"),
        nx.ruler_path("prom-main"): body("ruler-prom-main.json"),
    }
    path = request.url.path
    if path in table:
        return httpx.Response(200, json=table[path])
    return httpx.Response(404, json={"message": f"no {TOKEN}"})


@pytest.fixture
def exported(tmp_path: Path) -> Path:
    with nx.notifications_getter(TOKEN, HOST, transport=httpx.MockTransport(serve)) as get:
        nx.extract_notifications(get, tmp_path / "export")
    return tmp_path / "export"


def all_text(folder: Path) -> str:
    return "".join(p.read_text() for p in sorted(folder.rglob("*")) if p.is_file())


def test_nothing_secret_is_written_by_the_extractor(exported: Path) -> None:
    text = all_text(exported)
    for marker in NEVER:
        assert marker not in text, marker


def test_email_addresses_are_only_in_the_contact_point_file(exported: Path) -> None:
    for path in exported.rglob("*"):
        if path.is_file():
            assert ("EMAILADDR" in path.read_text()) == (path.name == "contact-points.json")


def test_the_odd_contact_point_and_data_source_are_contained(exported: Path) -> None:
    export = load_export(exported)
    odd = next(c for c in export.contact_points if c.name == "HOSTILE-odd")
    assert odd.type == "other" and odd.other_fields == 1
    assert [d.uid for d in export.datasources] == ["prom-main", "loki-main", "tempo-main", "azure"]


def test_the_verdicts_carry_no_name_or_secret(exported: Path) -> None:
    export = load_export(exported)
    verdicts = {c.uid: contact_point_verdict(c) for c in export.contact_points}
    text = " ".join(
        [*(s for v in verdicts.values() for s in (*v.manual_steps, *v.reasons, v.target))]
    )
    for marker in (*NEVER, "HOSTILE", "EMAILADDR"):
        assert marker not in text
    kinds = {c.type: verdicts[c.uid].verdict for c in export.contact_points}
    assert kinds["email"] is Verdict.CONVERTS and kinds["slack"] is Verdict.MANUAL_STEP
    assert kinds["mqtt"] is Verdict.NO_EQUIVALENT and kinds["wecom"] is Verdict.NO_EQUIVALENT
    webhook = verdicts["cp-hook"]
    assert R.R_CP_WEBHOOK_SETTINGS in webhook.reasons


def test_the_hostile_policy_is_planned_without_a_hang_and_without_names(exported: Path) -> None:
    export = load_export(exported)
    assert export.policy is not None
    rules = [
        RuleLabels(name="A", labels={"severity": "critical", "team": "t"}, by_labels=frozenset()),
        RuleLabels(name="B", labels={"env": "prod1"}, by_labels=frozenset({"service"})),
        RuleLabels(name="C", labels={"team": "x"}, by_labels=None),
    ]
    start = time.monotonic()
    plan = flatten_policy_tree(export.policy, rules)
    assert time.monotonic() - start < 5.0
    assert plan.entries and plan.undecidable
    text = json.dumps(
        {
            "reasons": plan.reasons,
            "notes": [n for e in plan.entries for n in (*e.notes, *e.reasons)],
            "undecidable": [u.model_dump() for u in plan.undecidable],
        }
    )
    for marker in (*NEVER, "HOSTILE"):
        assert marker not in text
    assert R.R_POLICY_REGEX in plan.reasons  # the (a+)+ route


def test_the_hostile_mute_timings_convert_what_is_exact_and_report_the_rest(
    exported: Path,
) -> None:
    export = load_export(exported)
    plans = {t.name: mute_timing_to_muting(t) for t in export.mute_timings}
    nights = plans["HOSTILE-nights"]
    assert len(nights.schedules) == 1 and nights.schedules[0].time_zone == "Europe/Berlin"
    assert set(nights.manual) == {R.R_MUTE_DAYS, R.R_MUTE_LOCATION}
    for plan in plans.values():
        text = " ".join([*plan.manual, *plan.notes])
        assert not any(m in text for m in (*NEVER, "HOSTILE"))


def test_templates_keep_a_hash_and_are_never_translated(exported: Path) -> None:
    export = load_export(exported)
    assert [t.text for t in export.templates] == [None, None]
    for raw in body("templates.json"):
        result = translate_template(raw["template"])
        assert result.status == "custom" and result.handlebars == ""
        assert not any(m in repr(result) for m in (*NEVER, "HOSTILE", "injected"))


def test_rule_files_load_and_carry_what_the_translator_needs(exported: Path) -> None:
    export = load_export(exported)
    (group,) = [g for g in export.rule_groups if g.kind == "prometheus"]
    assert [r.name for r in group.rules][-1] == "job:errors:rate5m"
    assert "bad-for" not in all_text(exported)  # a rule with an unreadable duration was left out
    rules = {r.name: to_grafana_rule(r, group, "prom-main") for r in group.rules}
    alert = rules["HOSTILE-alert | `x`"]
    assert [n.refId for n in alert.data] == ["A", "B", "C"] and alert.labels["team"] == "pay"
    assert rules["job:errors:rate5m"].record is not None


def test_the_guide_for_the_hostile_export_has_no_names_or_secrets(exported: Path) -> None:
    export = load_export(exported)
    files = build_files("us", datasources=export.datasources, metrics=["up"])
    text = "".join(files.values())
    for marker in (*NEVER, "HOSTILE", "prom-main"):
        assert marker not in text
    assert "azure" not in text.replace("grafana-azure-monitor-datasource", "")  # uid, not type
    assert "grafana-azure-monitor-datasource: 1" in text


def test_the_commands_print_no_secret_name_or_address(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GRAFANA_URL", HOST)
    monkeypatch.setenv("GRAFANA_SA_TOKEN", TOKEN)
    monkeypatch.setattr(
        "importer.commands.grafana_notifications.notifications_getter",
        lambda t, b, allow_host=None: nx.notifications_getter(
            t, b, httpx.MockTransport(serve), allow_host=allow_host
        ),
    )
    runner = CliRunner()
    extract = runner.invoke(app, ["extract-grafana-notifications", "--out", str(tmp_path / "e")])
    plan = runner.invoke(
        app,
        ["plan-grafana-datapath", "--out", str(tmp_path / "p"), "--export", str(tmp_path / "e")],
    )
    assert extract.exit_code == 0 and plan.exit_code == 0, (extract.output, plan.output)
    for result in (extract, plan):
        for marker in (*NEVER, "HOSTILE", "EMAILADDR"):
            assert marker not in result.output


def test_a_huge_tree_and_a_huge_settings_object_stay_bounded() -> None:
    from importer.sources.grafana.notifications.contact_points import sanitize_contact_point
    from importer.sources.grafana.notifications.policies import parse_policy_tree

    start = time.monotonic()
    tree = parse_policy_tree(
        {
            "receiver": "r",
            "routes": [{"receiver": f"c{i}", "matchers": [f'k="{i}"']} for i in range(50_000)],
        }
    )
    cp = sanitize_contact_point(
        {"name": "n", "type": "webhook", "settings": {f"k{i}": "LEAK" for i in range(200_000)}}
    )
    plan = flatten_policy_tree(tree, [RuleLabels(name="a", by_labels=frozenset())])
    assert time.monotonic() - start < 10
    assert tree.incomplete and plan.undecidable[0].reason == R.R_POLICY_INCOMPLETE
    assert len(cp.fields_set) <= 200 and "LEAK" not in cp.model_dump_json()
