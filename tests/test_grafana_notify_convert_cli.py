"""End to end: extract (against a fake Grafana) -> convert rules -> convert notifications.

The fixtures are synthetic. LEAK marks a value that must appear in no output; EMAILADDR marks an
email address, which may appear only in the plan files and the Terraform.
"""

import json
import socket
import stat
from pathlib import Path
from typing import Any

import httpx
import pytest
from typer.testing import CliRunner

from hcl_schema import resource_count, schema_errors, undeclared_variables
from importer.cli import app
from importer.core.notification_ir import NotificationPlan, SecretRef
from importer.sources.grafana.notifications import extract as nx

FIXTURES = Path(__file__).parent / "fixtures" / "grafana-notify-convert"
HOST = "https://mystack.grafana.net"
TOKEN = "glsa_AbCdEf123456_TOKENTOKEN"  # noqa: S105 - a made-up value for leak tests
NEVER = ("LEAK", "TOKENTOKEN", "glsa_")
runner = CliRunner()


def body(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text())


def serve(request: httpx.Request) -> httpx.Response:
    table = {
        nx.CONTACT_POINTS: body("contact-points.json"),
        nx.POLICIES: body("policies.json"),
        nx.MUTE_TIMINGS: body("mute-timings.json"),
        nx.TEMPLATES: body("templates.json"),
        nx.DATASOURCES: body("datasources.json"),
    }
    if request.url.path in table:
        return httpx.Response(200, json=table[request.url.path])
    return httpx.Response(404, json={"message": f"no {TOKEN}"})


@pytest.fixture(scope="module")
def exported(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("export") / "notifications"
    with nx.notifications_getter(TOKEN, HOST, transport=httpx.MockTransport(serve)) as get:
        nx.extract_notifications(get, out)
    return out


@pytest.fixture(scope="module")
def alerts(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("alerts") / "alerts"
    result = runner.invoke(
        app,
        ["convert", "--source", "grafana", "--in", str(FIXTURES / "rules.json"), "--out", str(out)],
    )
    assert result.exit_code == 0, result.output
    return out


def convert(
    exported: Path, alerts: Path, out: Path, *extra: str, rules: bool = True, dmap: bool = True
) -> Any:
    args = ["convert-grafana-notifications", "--in", str(exported), "--alerts", str(alerts)]
    args += ["--out", str(out)]
    if rules:
        args += ["--rules", str(FIXTURES / "rules.json")]
    if dmap:
        args += ["--destinations-map", str(FIXTURES / "destinations-map.json")]
    return runner.invoke(app, [*args, *extra])


@pytest.fixture(scope="module")
def converted(exported: Path, alerts: Path, tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("plans") / "out"
    result = convert(exported, alerts, out)
    assert result.exit_code == 0, result.output
    return out


def read_all(folder: Path, *, skip: tuple[str, ...] = ()) -> dict[str, str]:
    return {
        str(p.relative_to(folder)): p.read_text()
        for p in sorted(folder.rglob("*"))
        if p.is_file() and p.name not in skip
    }


# --- what the fixture converts to (pinned: a change here is a decision for a person) ---


def test_the_command_is_registered_and_prints_counts_only(
    exported: Path, alerts: Path, tmp_path: Path
) -> None:
    result = convert(exported, alerts, tmp_path / "o")
    assert result.exit_code == 0
    assert (
        "Read 29 contact points; routed 5 rules. Planned 16 workflows, 16 destinations"
        in result.output
    )
    assert "1 muting rules" in result.output and "18 secrets must be supplied" in result.output
    for marker in (*NEVER, "EMAILADDR"):
        assert marker not in result.output


def test_the_plan_folder_has_the_layout_apply_notifications_reads(converted: Path) -> None:
    plans = sorted((converted / "notification-plans").glob("topic-*.json"))
    assert len(plans) == 21  # 16 workflows and 5 manual-only routes
    loaded = [NotificationPlan.model_validate_json(p.read_text()) for p in plans]
    assert sum(p.workflow is not None for p in loaded) == 16
    assert sum(len(p.typed_destinations) for p in loaded) == 16
    assert len({p.topic for p in loaded}) == len(loaded)
    names = [p.workflow.name for p in loaded if p.workflow]
    assert len(set(names)) == len(names)
    dest_names = [d.name for p in loaded for d in p.typed_destinations]
    assert len(set(dest_names)) == len(dest_names)  # one channel per workflow


def test_apply_notifications_reads_the_folder_in_a_dry_run(converted: Path) -> None:
    result = runner.invoke(
        app, ["apply-notifications", "--in", str(converted), "--account-id", "1"]
    )
    assert result.exit_code == 0, result.output
    assert "16 workflows to create (disabled)" in result.output and "DRY RUN" in result.output


def test_every_verdict_class_and_type_is_in_the_report(converted: Path) -> None:
    report = (converted / "report.md").read_text()
    for verdict in ("CONVERTS", "WEBHOOK TEMPLATE", "MANUAL STEP", "NO EQUIVALENT"):
        assert verdict in report
    for kind in [
        "email",
        "webhook",
        "slack",
        "pagerduty",
        "teams",
        "jira",
        "oncall",
        "opsgenie",
        "victorops",
        "discord",
        "googlechat",
        "telegram",
        "webex",
        "sensugo",
        "kafka",
        "dingding",
        "wecom",
        "prometheus-alertmanager",
        "pushover",
        "sns",
        "threema",
        "LINE",
        "mqtt",
        "wechat",
    ]:
        assert f"| {kind} |" in report, kind
    assert "UNVERIFIED" not in report  # the word is for docs; the report says "unverified:"
    assert "unverified:" in report


def test_the_terraform_is_valid_for_the_provider_and_declares_every_secret(
    converted: Path,
) -> None:
    tf = (converted / "notifications.tf").read_text()
    assert schema_errors(tf) == [] and undeclared_variables(tf) == []
    assert resource_count(tf) >= 16 * 3 + 1
    declared = {
        line.split()[1].strip('"')
        for line in tf.splitlines()
        if line.startswith('variable "nr_secret_')
    }
    example = (converted / "secrets.tfvars.example").read_text()
    assert declared == {line.split(" =")[0] for line in example.splitlines()}
    assert len(declared) == 18
    assert all(d.startswith("nr_secret_") for d in declared)


def test_secrets_are_references_in_every_plan_and_the_map_names_are_used(converted: Path) -> None:
    text = "".join(p.read_text() for p in (converted / "notification-plans").glob("*.json"))
    assert "TEAM_HOOK_URL" in text and "PLATFORM_PD_KEY" in text
    plans = [
        NotificationPlan.model_validate_json(p.read_text())
        for p in (converted / "notification-plans").glob("*.json")
    ]
    refs = {
        r.name
        for p in plans
        for d in p.typed_destinations
        for r in ([d.secure_url.suffix] if d.secure_url else [])
        + [x for x in d.properties.values() if isinstance(x, SecretRef)]
    }
    assert "TEAM_HOOK_URL" in refs and len(refs) == 11  # 10 vendors, alertmanager, the map name


def test_the_muting_rule_is_planned_disabled_and_the_unconvertible_timing_is_reported(
    converted: Path,
) -> None:
    rules = sorted((converted / "muting-rules").glob("rule-*.json"))
    assert len(rules) == 1
    rule = json.loads(rules[0].read_text())
    assert rule["enabled"] is False and rule["schedule"]["time_zone"] == "Europe/Berlin"
    report = (converted / "report.md").read_text()
    assert "Month end" in report and "days of the month not converted" in report
    assert "Grafana active time interval" in report
    assert "(Data team): grafana active time interval not converted" in report
    assert "mute timing no route uses" in report.lower() or "unused mute timing" in report
    tf = (converted / "notifications.tf").read_text()
    assert tf.count('resource "newrelic_alert_muting_rule"') == 1
    strict = tf.count("DONT_NOTIFY_FULLY_OR_PARTIALLY_MUTED_ISSUES")
    assert strict >= 1


def test_the_vendor_payloads_in_the_plans_render_to_json(converted: Path) -> None:
    from importer.sources.grafana.notify_convert import handlebars as hb

    sample = {
        "issueId": "i1",
        "issueTitle": 'a "b" \\ c',
        "state": "CLOSED",
        "stateText": "closed",
        "priority": "HIGH",
        "priorityText": "High",
        "issuePageUrl": "https://one.newrelic.com/x",
        "issueClosedAtUtc": "2026-11-09T00:00:00Z",
    }
    seen = 0
    for path in (converted / "notification-plans").glob("*.json"):
        for dest in NotificationPlan.model_validate_json(path.read_text()).typed_destinations:
            payload = dest.channel_properties.get("payload")
            if payload and dest.type.value == "WEBHOOK":
                json.loads(hb.render(payload, sample))
                seen += 1
    assert seen == 13  # the generic webhook and the 12 webhook-template services


# --- safety ---


def test_no_secret_value_name_or_address_reaches_the_report_or_the_example_files(
    converted: Path,
) -> None:
    files = read_all(converted)
    for rel, text in files.items():
        for marker in NEVER:
            assert marker not in text, (rel, marker)
        if not (rel.startswith("notification-plans/") or rel == "notifications.tf"):
            assert "EMAILADDR" not in text and "data-oncall@example.com" not in text, rel
    assert "EMAILADDR" in files["notifications.tf"]


def test_files_are_readable_by_the_owner_only(converted: Path) -> None:
    for path in converted.rglob("*"):
        if path.is_file():
            assert stat.S_IMODE(path.stat().st_mode) == 0o600, path


def test_the_command_works_offline(
    exported: Path, alerts: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse(*_: object, **__: object) -> None:
        raise AssertionError("the command opened a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    assert convert(exported, alerts, tmp_path / "o").exit_code == 0


def test_two_runs_write_the_same_files(exported: Path, alerts: Path, tmp_path: Path) -> None:
    convert(exported, alerts, tmp_path / "a")
    convert(exported, alerts, tmp_path / "b")
    assert read_all(tmp_path / "a") == read_all(tmp_path / "b")


# --- options ---


def test_without_the_rules_the_policy_on_static_labels_is_undecidable(
    exported: Path, alerts: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o"
    result = convert(exported, alerts, out, rules=False)
    assert result.exit_code == 0
    report = (out / "report.md").read_text()
    assert "Rules that were not routed" in report
    assert "matcher cannot be decided" in report


def test_without_the_map_more_stays_manual(exported: Path, alerts: Path, tmp_path: Path) -> None:
    out = tmp_path / "o"
    result = convert(exported, alerts, out, dmap=False)
    assert result.exit_code == 0
    report = (out / "report.md").read_text()
    assert "email address" in report  # the bare email contact point stays manual
    assert "TEAM_HOOK_URL" not in report and "GRAFANA_GENERICHOO" in report


# --- errors ---


def test_a_missing_input_folder_is_a_clean_error(alerts: Path, tmp_path: Path) -> None:
    result = convert(tmp_path / "nope", alerts, tmp_path / "o")
    assert result.exit_code == 1 and "Error:" in result.output
    assert "Traceback" not in result.output


def test_alerts_without_ir_is_a_clean_error(exported: Path, tmp_path: Path) -> None:
    result = convert(exported, tmp_path, tmp_path / "o")
    assert result.exit_code == 1 and "ir/" in result.output


def test_a_broken_ir_file_is_a_clean_error_that_does_not_quote_it(
    exported: Path, tmp_path: Path
) -> None:
    (tmp_path / "ir").mkdir()
    (tmp_path / "ir" / "x.json").write_text('{"name": "LEAK-in-ir"')
    result = convert(exported, tmp_path, tmp_path / "o")
    assert result.exit_code == 1 and "LEAK" not in result.output


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        '{"version": 1, "contact_points": {"x": {"webhook_url": "https://LEAK.example.com/t"}}}',
        '{"version": 1, "contact_points": {"x": {"pagerduty_key": "LEAK-secret-value-123456"}}}',
        '{"version": 1, "contact_points": {"x": {"colour": "LEAK"}}}',
    ],
)
def test_a_bad_map_is_a_clean_error_that_does_not_echo_values(
    exported: Path, alerts: Path, tmp_path: Path, text: str
) -> None:
    bad = tmp_path / "map.json"
    bad.write_text(text)
    out = tmp_path / "o"
    result = runner.invoke(
        app,
        [
            "convert-grafana-notifications", "--in", str(exported), "--alerts", str(alerts),
            "--out", str(out), "--destinations-map", str(bad),
        ],
    )  # fmt: skip
    assert result.exit_code == 1 and "LEAK" not in result.output
    assert not out.exists() or not any(out.iterdir())


def test_an_existing_output_is_not_overwritten(
    exported: Path, alerts: Path, tmp_path: Path
) -> None:
    out = tmp_path / "o"
    assert convert(exported, alerts, out).exit_code == 0
    before = read_all(out)
    again = convert(exported, alerts, out)
    assert again.exit_code == 1 and "already exists" in again.output
    assert read_all(out) == before
