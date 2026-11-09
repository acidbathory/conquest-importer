"""A custom template that redefines a default name makes the default calls custom."""

import json
from pathlib import Path

from importer.sources.grafana.notifications.loader import load_export, read_templates
from importer.sources.grafana.notifications.models import ContactPoint
from importer.sources.grafana.notifications.templates import (
    make_template,
    mark_overridden,
    translate_template,
)

CALL = '{{ template "default.title" . }}'


def test_the_names_a_template_defines_are_kept_and_the_text_is_not() -> None:
    t = make_template(
        "n", '{{ define "default.title" }}LEAK{{ end }}{{ define "other" }}x{{ end }}'
    )
    assert t.defines == ["default.title", "other"] and t.text is None
    assert "LEAK" not in t.model_dump_json()


def test_define_names_are_validated_and_capped() -> None:
    text = "".join(f'{{{{ define "n{i}" }}}}{{{{ end }}}}' for i in range(500))
    assert len(make_template("n", text).defines) <= 50
    assert make_template("n", '{{ define "a b`c" }}{{ end }}').defines == []


def test_a_default_call_is_custom_when_the_name_is_redefined() -> None:
    assert translate_template(CALL).status == "default"
    assert translate_template(CALL, overridden=frozenset({"default.title"})).status == "custom"
    assert translate_template(CALL, overridden=frozenset({"default.message"})).status == "default"


def test_contact_points_follow_the_templates_of_the_export() -> None:
    cps = [
        ContactPoint(
            name="a", type="slack", templates={"title": "default", "text": "empty", "x": "custom"}
        ),
        ContactPoint(name="b", type="slack", templates={"title": "default"}),
    ]
    mark_overridden(cps, [make_template("t", '{{ define "default.title" }}x{{ end }}')])
    assert cps[0].templates == {"title": "custom", "text": "custom", "x": "custom"}
    unrelated = [ContactPoint(name="c", type="slack", templates={"title": "default"})]
    mark_overridden(unrelated, [make_template("t", '{{ define "mine" }}x{{ end }}')])
    assert unrelated[0].templates == {"title": "default"}


def test_the_loader_applies_it(tmp_path: Path) -> None:
    (tmp_path / "contact-points.json").write_text(
        json.dumps([{"name": "a", "type": "slack", "templates": {"title": "default"}}])
    )
    (tmp_path / "templates.json").write_text(
        json.dumps([{"name": "t", "template": '{{ define "default.message" }}x{{ end }}'}])
    )
    assert load_export(tmp_path).contact_points[0].templates == {"title": "custom"}


def test_read_templates_keeps_the_defines_of_a_saved_file() -> None:
    (t,) = read_templates(
        [{"name": "t", "sha256": "a" * 64, "length": 3, "defines": ["default.title", "a b"]}]
    )
    assert t.defines == ["default.title"]
