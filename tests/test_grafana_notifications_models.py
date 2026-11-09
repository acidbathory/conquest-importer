"""Loaders for Grafana notification objects: secrets are dropped at read time, caps hold."""

import json
from pathlib import Path
from typing import Any

import pytest

from importer.sources.grafana.models import load_rules
from importer.sources.grafana.notifications import schema
from importer.sources.grafana.notifications.contact_points import (
    contact_points_from_payload,
    sanitize_contact_point,
)
from importer.sources.grafana.notifications.loader import load_export
from importer.sources.grafana.notifications.models import MAX_POLICY_DEPTH, MAX_POLICY_NODES
from importer.sources.grafana.notifications.policies import (
    parse_matcher_string,
    parse_policy_tree,
)
from importer.sources.grafana.notifications.rulefiles import (
    as_ruler_group,
    load_rule_file,
    load_rule_groups,
)
from importer.sources.grafana.notifications.templates import (
    DEFAULT_BODIES,
    NEW_RELIC_VARIABLES,
    translate_template,
)
from importer.sources.grafana.notifications.timings import parse_mute_timings

TYPES = [
    "prometheus-alertmanager", "dingding", "discord", "email", "googlechat", "jira", "kafka",
    "LINE", "mqtt", "oncall", "opsgenie", "pagerduty", "pushover", "sensugo", "slack", "sns",
    "teams", "telegram", "threema", "victorops", "webex", "webhook", "wecom", "wechat",
]  # fmt: skip
SECRET = "SECRET-VALUE-9f3a"  # noqa: S105 - a made-up marker for leak tests


def test_the_schema_table_has_all_24_type_strings() -> None:
    assert sorted(schema.type_names()) == sorted(TYPES)
    assert len(schema.type_names()) == 24


@pytest.mark.parametrize(
    ("type_name", "field", "secure", "required"),
    [
        ("slack", "token", True, False),
        ("slack", "url", True, False),
        ("slack", "recipient", False, True),
        ("webhook", "password", True, False),
        ("webhook", "authorization_credentials", True, False),
        ("webhook", "hmacConfig.secret", True, False),
        ("webhook", "headers", False, False),
        ("webhook", "url", False, True),
        ("pagerduty", "integrationKey", True, True),
        ("email", "addresses", False, True),
        ("mqtt", "tlsConfig.clientKey", True, False),
        ("sns", "sigv4.secret_key", True, False),
        ("sns", "sigv4.region", False, False),
        ("telegram", "bottoken", True, True),
        ("LINE", "token", True, True),
        ("teams", "url", False, True),
    ],
)
def test_schema_flags(type_name: str, field: str, secure: bool, required: bool) -> None:
    spec = schema.field_spec(type_name, field)
    assert spec is not None
    assert (spec.secure, spec.required) == (secure, required)


def test_every_secure_field_has_the_secret_kind_and_no_other_does() -> None:
    for spec in schema.load_schema().values():
        assert all((f.kind == "secret") == f.secure for f in spec.fields)


def test_wechat_has_no_v1_config() -> None:
    assert schema.load_schema()["wechat"].v1 is False
    assert all(spec.v1 for name, spec in schema.load_schema().items() if name != "wechat")


def _all_set(type_name: str) -> dict[str, Any]:
    """Every schema field set to a marker that names the field, nested by the dotted path."""
    settings: dict[str, Any] = {}
    for f in schema.load_schema()[type_name].fields:
        node = settings
        *parents, leaf = f.name.split(".")
        for p in parents:
            node = node.setdefault(p, {})
        node[leaf] = {"X-Api-Key": SECRET} if f.kind == "structured" else f"{SECRET}:{f.name}"
    return settings


@pytest.mark.parametrize("type_name", TYPES)
def test_no_value_of_any_field_survives_except_the_safe_kinds(type_name: str) -> None:
    cp = sanitize_contact_point(
        {"uid": "u1", "name": "n", "type": type_name, "settings": _all_set(type_name)}
    )
    text = cp.model_dump_json()
    assert SECRET not in text
    secure = schema.secure_fields(type_name)
    assert secure <= set(cp.fields_set)
    assert not (secure & set(cp.options) | secure & set(cp.labels))


def test_redacted_values_are_recorded_by_name_only() -> None:
    cp = sanitize_contact_point(
        {
            "name": "pd",
            "type": "pagerduty",
            "settings": {"integrationKey": "[REDACTED]", "severity": "critical", "client": ""},
        }
    )
    assert cp.redacted == ["integrationKey"]
    assert cp.fields_set == ["integrationKey", "severity"]
    assert cp.options == {"severity": "critical"}


def test_unset_secure_fields_are_not_reported_as_set() -> None:
    cp = sanitize_contact_point({"name": "w", "type": "webhook", "settings": {"password": ""}})
    assert cp.fields_set == [] and cp.redacted == []


@pytest.mark.parametrize(
    ("url", "host"),
    [
        ("https://hooks.example.com/services/T0/B0/token123", "hooks.example.com"),
        ("https://hooks.example.com:8443/x?key=abc", "hooks.example.com"),
        ("https://user:pw@evil.example.com/x", ""),
        ("https://good.example.com@evil.example.com/x", ""),
        ("not a url", ""),
        ("https://exa mple.com/", ""),
    ],
)
def test_a_url_keeps_a_host_and_never_a_path_query_or_credentials(url: str, host: str) -> None:
    cp = sanitize_contact_point({"name": "w", "type": "webhook", "settings": {"url": url}})
    assert cp.url_host == host
    dumped = cp.model_dump_json()
    for leak in ("token123", "key=abc", "user:pw", "/services"):
        assert leak not in dumped


def test_a_secure_url_keeps_no_host() -> None:
    cp = sanitize_contact_point(
        {
            "name": "d",
            "type": "discord",
            "settings": {"url": "https://discord.com/api/webhooks/1/x"},
        }
    )
    assert cp.url_host == "" and cp.fields_set == ["url"]


def test_headers_keep_no_names_and_no_values() -> None:
    cp = sanitize_contact_point(
        {"name": "w", "type": "webhook", "settings": {"headers": {"X-Api-Key": SECRET}}}
    )
    assert cp.fields_set == ["headers"]
    assert SECRET not in cp.model_dump_json() and "X-Api-Key" not in cp.model_dump_json()


def test_email_addresses_are_kept_for_email_only() -> None:
    mail = sanitize_contact_point(
        {
            "name": "m",
            "type": "email",
            "settings": {"addresses": "a@example.com;b@example.com\nnot-one"},
        }
    )
    assert mail.addresses == ["a@example.com", "b@example.com"]
    other = sanitize_contact_point(
        {"name": "x", "type": "slack", "settings": {"recipient": "a@example.com"}}
    )
    assert other.addresses == [] and other.labels == {}
    assert "a@example.com" not in other.model_dump_json()
    assert "addresses" in type(mail).model_fields
    assert type(mail).model_fields["addresses"].json_schema_extra == {"sensitive": True}
    assert "a@example.com" not in repr(mail)


def test_a_label_that_looks_like_a_token_is_dropped() -> None:
    ok = sanitize_contact_point(
        {"name": "s", "type": "slack", "settings": {"recipient": "#alerts"}}
    )
    assert ok.labels == {"recipient": "#alerts"}
    for bad in ("0123456789abcdef0123456789abcdef", "a=b", "x@y.z", "https://x.example"):
        cp = sanitize_contact_point({"name": "s", "type": "slack", "settings": {"recipient": bad}})
        assert cp.labels == {}


def test_template_fields_are_classified_not_copied() -> None:
    cp = sanitize_contact_point(
        {
            "name": "s",
            "type": "slack",
            "settings": {
                "title": '{{ template "default.title" . }}',
                "text": "{{ .Labels.password }} " + SECRET,
                "footer": " ",
            },
        }
    )
    assert cp.templates == {"title": "default", "text": "custom", "footer": "empty"}
    assert SECRET not in cp.model_dump_json()


def test_hostile_names_and_types_are_contained() -> None:
    cp = sanitize_contact_point(
        {
            "name": "x\x1b[31m" + "N" * 5000,
            "type": "../../etc/passwd",
            "settings": {"a b": SECRET, "ok_key": SECRET, "k" * 500: SECRET},
        }
    )
    assert cp.type == "other"
    assert len(cp.name) <= 200 and "\x1b" not in cp.name
    assert cp.other_fields == 2 and cp.fields_set == ["ok_key"]
    assert SECRET not in cp.model_dump_json()


def test_unknown_type_keeps_only_names() -> None:
    cp = sanitize_contact_point(
        {
            "name": "n",
            "type": "newthing",
            "settings": {"url": "https://h.example.com/p", "k": SECRET},
        }
    )
    assert (
        cp.fields_set == ["k", "url"] and cp.url_host == "" and SECRET not in cp.model_dump_json()
    )


def test_nesting_bomb_in_settings_is_bounded() -> None:
    deep: dict[str, Any] = {}
    node = deep
    for _ in range(5000):
        node["a"] = {}
        node = node["a"]
    node["leaf"] = SECRET
    big = {f"k{i}": SECRET for i in range(100_000)}
    for settings in (deep, big):
        cp = sanitize_contact_point({"name": "n", "type": "webhook", "settings": settings})
        assert len(cp.fields_set) <= 200 and SECRET not in cp.model_dump_json()


def test_export_shape_groups_receivers_under_one_name() -> None:
    found = contact_points_from_payload(
        [
            {
                "name": "team",
                "receivers": [
                    {"uid": "1", "type": "email", "settings": {"addresses": "a@example.com"}},
                    {"uid": "2", "type": "slack", "settings": {"token": "[REDACTED]"}},
                ],
            }
        ]
    )
    assert [(c.name, c.type) for c in found] == [("team", "email"), ("team", "slack")]


def test_a_fan_out_of_receivers_is_capped() -> None:
    payload = [{"name": "t", "receivers": [{"type": "email"}] * 50_000}]
    assert len(contact_points_from_payload(payload)) == 10_000


# --- matchers and the policy tree ---


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('severity="critical"', ("severity", "=", "critical")),
        ('team=~"a|b"', ("team", "=~", "a|b")),
        ("env!=prod", ("env", "!=", "prod")),
        ('x !~ "a.*"', ("x", "!~", "a.*")),
        ('msg="say \\"hi\\""', ("msg", "=", 'say "hi"')),
        ('"quoted name"="v"', ("quoted name", "=", "v")),
        ('a=""', ("a", "=", "")),
    ],
)
def test_matcher_strings(text: str, expected: tuple[str, str, str]) -> None:
    found = parse_matcher_string(text)
    assert found is not None and (found.name, found.op, found.value) == expected


@pytest.mark.parametrize("text", ["", "novalue", "=x", "a>b", "a=" + "x" * 2000])
def test_bad_matcher_strings_are_not_matchers(text: str) -> None:
    assert parse_matcher_string(text) is None


def test_policy_tree_reads_every_matcher_spelling() -> None:
    tree = parse_policy_tree(
        {
            "receiver": "root",
            "group_by": ["grafana_folder", "alertname"],
            "group_wait": "30s",
            "repeat_interval": "4h",
            "routes": [
                {
                    "receiver": "a",
                    "matchers": ['team="x"', "garbage"],
                    "object_matchers": [["env", "=~", "prod|stage"], ["bad", "??", "x"]],
                    "match": {"k": "v"},
                    "match_re": {"r": "v.*"},
                    "continue": True,
                    "mute_time_intervals": ["nights"],
                    "routes": [{"receiver": "b"}],
                }
            ],
        }
    )
    root, child = tree.root, tree.root.routes[0]
    assert root.group_by == ["grafana_folder", "alertname"]
    assert (root.group_wait_seconds, root.repeat_interval_seconds) == (30, 14400)
    assert [(m.name, m.op) for m in child.matchers] == [
        ("team", "="), ("env", "=~"), ("k", "="), ("r", "=~")
    ]  # fmt: skip
    assert child.unparsed_matchers == 2 and child.continue_
    assert child.mute_time_intervals == ["nights"] and child.routes[0].receiver == "b"
    assert not tree.incomplete


def test_root_matchers_are_ignored_and_reported() -> None:
    tree = parse_policy_tree({"receiver": "r", "matchers": ['a="b"']})
    assert tree.root.matchers == [] and tree.problems


def test_a_tree_deeper_than_the_cap_is_marked_incomplete() -> None:
    raw: dict[str, Any] = {"receiver": "leaf"}
    for _ in range(MAX_POLICY_DEPTH + 10):
        raw = {"receiver": "n", "routes": [raw]}
    tree = parse_policy_tree(raw)
    assert tree.incomplete and tree.problems


def test_a_tree_with_too_many_routes_is_marked_incomplete() -> None:
    tree = parse_policy_tree(
        {"receiver": "r", "routes": [{"receiver": "x"}] * (MAX_POLICY_NODES * 2)}
    )
    assert tree.incomplete and tree.node_count <= MAX_POLICY_NODES


def test_a_non_object_route_is_skipped() -> None:
    tree = parse_policy_tree({"receiver": "r", "routes": ["x", 3, {"receiver": "ok"}]})
    assert [r.receiver for r in tree.root.routes] == ["ok"] and tree.incomplete


def test_a_tree_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(ValueError, match="JSON object"):
        parse_policy_tree([])


# --- mute timings ---


def test_mute_timings_are_read_with_numbers_and_names() -> None:
    found = parse_mute_timings(
        [
            {
                "name": "nights",
                "time_intervals": [
                    {
                        "times": [{"start_time": "22:00", "end_time": "24:00"}, {"start_time": 3}],
                        "weekdays": ["monday:friday", 6, True],
                        "location": "Europe/Berlin",
                    }
                ],
            },
            {"name": ""},
            "junk",
        ]
    )
    assert [m.name for m in found] == ["nights"]
    interval = found[0].intervals[0]
    assert [(t.start, t.end) for t in interval.times] == [("22:00", "24:00")]
    assert interval.weekdays == ["monday:friday", "6"] and interval.location == "Europe/Berlin"


# --- templates ---


def test_default_templates_map_to_fixed_bodies() -> None:
    for name, body in DEFAULT_BODIES.items():
        result = translate_template('{{ template "' + name + '" . }}')
        assert (result.status, result.handlebars) == ("default", body)
    assert translate_template("  ").status == "empty"


def test_the_default_bodies_use_only_documented_variables() -> None:
    import re

    for body in DEFAULT_BODIES.values():
        assert set(re.findall(r"\{\{\s*(\w+)", body)) <= NEW_RELIC_VARIABLES


@pytest.mark.parametrize(
    "text",
    [
        "{{ .Status }}: {{ .CommonLabels.alertname }}",
        '{{ define "x" }}{{ end }}{{ template "x" . }}',
        '{{ template "default.title" . }} extra',
        '{{ template "default.unknown" . }}',
        "{{ range .Alerts }}{{ .Labels.password }}{{ end }}",
    ],
)
def test_a_custom_template_is_reported_and_never_translated(text: str) -> None:
    result = translate_template(text + SECRET)
    assert result.status == "custom" and result.handlebars == ""
    assert SECRET not in repr(result) and "range" not in result.reason


def test_a_custom_template_gets_hints_only_from_the_map() -> None:
    result = translate_template("{{ .Status }} {{ .SilenceURL }} {{ .Evil }}")
    assert result.hints == (
        "SilenceURL -> no equivalent (a silence is a muting rule in New Relic)",
        "Status -> stateText (active or closed instead of firing or resolved)",
    )


# --- rule files ---

RULE_FILE = {
    "groups": [
        {
            "name": "g",
            "interval": "1m",
            "labels": {"team": "pay", "severity": "info"},
            "rules": [
                {
                    "alert": "HighErrors",
                    "expr": "sum(rate(errors_total[5m])) > 5",
                    "for": "10m",
                    "keep_firing_for": "5m",
                    "labels": {"severity": "critical"},
                    "annotations": {"summary": "errors", "bad": 3},
                },
                {"record": "job:errors:rate5m", "expr": "sum(rate(errors_total[5m]))"},
                {"alert": "NoExpr"},
                {"alert": "BadFor", "expr": "up == 0", "for": "soon"},
            ],
        }
    ]
}


def test_prometheus_rule_groups_are_read_with_group_labels_merged() -> None:
    (group,) = load_rule_groups(RULE_FILE)
    assert (group.name, group.interval_seconds, group.skipped) == ("g", 60, 2)
    alert, record = group.rules
    assert (alert.kind, alert.name, alert.for_seconds, alert.keep_firing_for_seconds) == (
        "alert", "HighErrors", 600, 300
    )  # fmt: skip
    assert alert.labels == {"team": "pay", "severity": "critical"}
    assert alert.annotations == {"summary": "errors"}
    assert record.kind == "record"


def test_the_ruler_listing_and_api_shapes_are_read() -> None:
    ruler = {"ns1": RULE_FILE["groups"], "ns2": RULE_FILE["groups"]}
    assert sorted(g.namespace for g in load_rule_groups(ruler, "loki")) == ["ns1", "ns2"]
    api = {"status": "success", "data": {"groups": [{"name": "g", "file": "f", "rules": []}]}}
    assert load_rule_groups(api)[0].namespace == "f"


def test_a_rule_file_with_no_readable_group_is_refused() -> None:
    with pytest.raises(ValueError, match="could not be read"):
        load_rule_groups({"groups": [{"rules": []}]})


def test_yaml_is_refused_with_the_fix(tmp_path: Path) -> None:
    path = tmp_path / "rules.yml"
    path.write_text("groups: []\n")
    with pytest.raises(ValueError, match="yq -o=json"):
        load_rule_file(path)


def test_a_rule_file_nested_100000_deep_is_a_clean_error(tmp_path: Path) -> None:
    path = tmp_path / "rules.json"
    path.write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ValueError, match="nested too deeply"):
        load_rule_file(path)


def test_normalised_rules_carry_what_the_translator_reads(tmp_path: Path) -> None:
    """The ruler dict loads through the existing Grafana loader with every field intact."""
    (group,) = load_rule_groups({"ns": RULE_FILE["groups"]})
    path = tmp_path / "ruler.json"
    path.write_text(json.dumps({"ns": [as_ruler_group(group)]}))
    rules = {r.title: r for r in load_rules(path)}
    alert = rules["HighErrors"]
    assert alert.datasource_managed and alert.record is None
    assert (alert.for_seconds, alert.keep_firing_for_seconds) == (600, 300)
    assert alert.labels == {"team": "pay", "severity": "critical"}
    assert alert.interval_seconds == 60 and alert.group == "g" and alert.folder == "ns"
    assert "job:errors:rate5m" in rules


# --- the folder loader ---


def test_the_loader_reads_a_folder_and_keeps_only_type_and_uid_of_data_sources(
    tmp_path: Path,
) -> None:
    (tmp_path / "contact-points.json").write_text(
        json.dumps([{"name": "w", "type": "webhook", "settings": {"password": SECRET}}])
    )
    (tmp_path / "datasources.json").write_text(
        json.dumps(
            [
                {"type": "prometheus", "uid": "p1", "name": "Prom", "basicAuthUser": SECRET,
                 "url": "https://u:" + SECRET + "@h.example.com"},
                {"type": "loki", "uid": "bad uid!"},
                {"type": "tempo"},
            ]
        )
    )  # fmt: skip
    (tmp_path / "templates.json").write_text(
        json.dumps([{"name": "t", "template": '{{ define "t" }}' + SECRET + "{{ end }}"}])
    )
    (tmp_path / "policies.json").write_text(json.dumps({"receiver": "w"}))
    (tmp_path / "mute-timings.json").write_text(json.dumps([{"name": "n"}]))
    export = load_export(tmp_path)
    assert [(d.type, d.uid) for d in export.datasources] == [("prometheus", "p1")]
    assert [t.name for t in export.templates] == ["t"] and export.templates[0].text is None
    assert export.policy is not None and [m.name for m in export.mute_timings] == ["n"]
    assert SECRET not in export.model_dump_json()
    assert export.problems == []


def test_a_broken_file_is_a_problem_not_a_crash(tmp_path: Path) -> None:
    (tmp_path / "contact-points.json").write_text("{not json")
    (tmp_path / "policies.json").write_text("[" * 100_000)
    (tmp_path / "mute-timings.json").write_text(json.dumps([{"name": "ok"}]))
    export = load_export(tmp_path)
    assert len(export.problems) == 2 and [m.name for m in export.mute_timings] == ["ok"]


def test_the_loader_refuses_a_missing_folder(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not a folder"):
        load_export(tmp_path / "nope")


def test_a_sanitised_file_cannot_smuggle_addresses_into_other_types(tmp_path: Path) -> None:
    (tmp_path / "contact-points.json").write_text(
        json.dumps([{"name": "x", "type": "slack", "addresses": ["a@example.com"]}])
    )
    assert load_export(tmp_path).contact_points[0].addresses == []
