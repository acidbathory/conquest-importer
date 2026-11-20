"""Grafana rule exports in YAML (the default of Alerting > Export and the export API)."""

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
import yaml

from importer.core.ir import Confidence
from importer.core.safe_yaml import load_yaml
from importer.sources.grafana import models
from importer.sources.grafana.models import load_rules
from importer.sources.grafana.translate import R_VALUE, translate

FIXTURES = Path(__file__).parent / "fixtures"
EXPORT = FIXTURES / "grafana-yaml" / "export.yaml"


def monitors(path: Path) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in translate(load_rules(path))]


@pytest.mark.parametrize("fixture", sorted((FIXTURES / "grafana").glob("*.json")), ids=str)
def test_a_yaml_export_converts_the_same_monitors_as_the_json(
    tmp_path: Path, fixture: Path
) -> None:
    as_yaml = tmp_path / f"{fixture.stem}.yaml"
    as_yaml.write_text(yaml.safe_dump(json.loads(fixture.read_text()), sort_keys=False))
    assert monitors(as_yaml) == monitors(fixture)


@pytest.mark.parametrize("suffix", [".yaml", ".yml"])
def test_a_folder_with_a_yaml_export_is_read(tmp_path: Path, suffix: str) -> None:
    shutil.copy(EXPORT, tmp_path / f"export{suffix}")
    assert [r.uid for r in load_rules(tmp_path)] == ["yaml-exp", "yaml-quoted"]


def test_a_folder_may_hold_json_and_yaml(tmp_path: Path) -> None:
    shutil.copy(EXPORT, tmp_path / "a.yaml")
    shutil.copy(FIXTURES / "grafana" / "export_alert_rules.json", tmp_path / "b.json")
    uids = {r.uid for r in load_rules(tmp_path)}
    assert {"yaml-exp", "yaml-quoted"} < uids and len(uids) > 2


def test_an_unquoted_exponent_is_a_number_and_a_quoted_one_stays_manual() -> None:
    by_key = {m.source_key: m for m in translate(load_rules(EXPORT))}
    plain, quoted = by_key["yaml-exp"], by_key["yaml-quoted"]
    assert plain.confidence != Confidence.MANUAL, plain.reasons
    assert [t.value for t in plain.thresholds] == [1e7]
    assert quoted.confidence == Confidence.MANUAL and R_VALUE in quoted.reasons


def test_yaml_1_1_booleans_and_octals_stay_strings() -> None:
    rule = load_rules(EXPORT)[0]
    assert rule.labels == {
        "enabled": "yes",
        "paging": "no",
        "sticky": "on",
        "quiet": "off",
        "mode": "0755",
        "code": "0755",
    }


@pytest.mark.parametrize(
    ("text", "value"),
    [
        ("1e+07", 1e7),
        ("1E7", 1e7),
        ("-2.5e-3", -2.5e-3),
        ("2.5", 2.5),
        (".5", 0.5),
        ("12", 12),
        ("-3", -3),
        ("0", 0),
        ("0x1F", 31),
        ("0o17", 15),
        ("true", True),
        ("False", False),
        ("null", None),
        ("~", None),
        ("", None),
        ("yes", "yes"),
        ("NO", "NO"),
        ("on", "on"),
        ("y", "y"),
        ("0755", "0755"),
        ("2024-01-02", "2024-01-02"),
        ("1_000", "1_000"),
        ("1:30", "1:30"),
        ("'1e+07'", "1e+07"),
        ('"12"', "12"),
    ],
)
def test_scalars_read_as_yaml_1_2(text: str, value: Any) -> None:
    (loaded,) = load_yaml(f"v: {text}\n", "x.yaml").values()
    assert loaded == value and type(loaded) is type(value)


@pytest.mark.parametrize("text", [".inf", "-.Inf", ".nan", ".NaN"])
def test_infinity_and_nan_are_floats_that_stay_manual(tmp_path: Path, text: str) -> None:
    path = tmp_path / "x.yaml"
    path.write_text(EXPORT.read_text().replace("- 1e+07", f"- {text}"))
    monitor = next(m for m in translate(load_rules(path)) if m.source_key == "yaml-exp")
    assert monitor.confidence == Confidence.MANUAL and R_VALUE in monitor.reasons


@pytest.mark.parametrize(
    "text",
    [
        "a: &x [1, 2]\nb: *x\n",
        "a: &x 1\n",
        "a: [&x {}, *x]\n",
        "base: &b {k: 1}\nother:\n  <<: *b\n",
    ],
)
def test_anchors_and_aliases_are_refused_with_the_file_name(tmp_path: Path, text: str) -> None:
    path = tmp_path / "bomb.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match=r"bomb\.yaml.*(anchor|alias)"):
        load_rules(path)


def test_an_alias_bomb_is_refused_before_it_expands(tmp_path: Path) -> None:
    lines = ["a0: &a0 [x, x, x, x, x, x, x, x, x, x]"]
    lines += [f"a{i}: &a{i} [{', '.join([f'*a{i - 1}'] * 10)}]" for i in range(1, 12)]
    path = tmp_path / "bomb.yml"
    path.write_text("\n".join(lines) + "\n")
    with pytest.raises(ValueError, match="anchor"):
        load_rules(path)


@pytest.mark.parametrize("text", ["a: !!binary aGVsbG8=\n", "a: !!python/name:os.system\n"])
def test_explicit_tags_are_refused(tmp_path: Path, text: str) -> None:
    path = tmp_path / "tagged.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match=r"tagged\.yaml.*tag"):
        load_rules(path)


@pytest.mark.parametrize(
    "text",
    ["[" * 100_000 + "]" * 100_000, "a:\n  " + "- " * 300 + "x\n"],
    ids=["flow", "block"],
)
def test_a_deeply_nested_yaml_is_a_clean_error(tmp_path: Path, text: str) -> None:
    path = tmp_path / "deep.yaml"
    path.write_text(text)
    with pytest.raises(ValueError, match="nested too deeply"):
        load_rules(path)


def test_a_yaml_file_over_the_size_limit_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(models, "MAX_INPUT_BYTES", 100)
    shutil.copy(EXPORT, tmp_path / "export.yaml")
    with pytest.raises(ValueError, match="larger than"):
        load_rules(tmp_path)


def test_invalid_yaml_names_the_file_and_line_without_echoing_content(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("a: 1\nsecret-looking: [unclosed\n")
    with pytest.raises(ValueError, match=r"broken\.yaml.*line") as caught:
        load_rules(path)
    assert "secret-looking" not in str(caught.value)


def test_more_than_one_document_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "two.yaml"
    path.write_text("apiVersion: 1\ngroups: []\n---\napiVersion: 1\ngroups: []\n")
    with pytest.raises(ValueError, match=r"two\.yaml"):
        load_rules(path)
