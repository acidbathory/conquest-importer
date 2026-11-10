import pytest

from importer.core.playbook import Playbook, fill_template, playbook_for, register_playbook


def test_template_uses_safe_names_and_keeps_tokens_otherwise() -> None:
    text = "SELECT latest(<metric>) FROM Metric FACET <labels>"
    assert (
        fill_template(text, "node_load1", ("cpu", "mode"))
        == "SELECT latest(node_load1) FROM Metric FACET cpu, mode"
    )
    # a name with quotes or spaces never reaches the output
    assert fill_template(text, "x') OR 1=1 --", ("a b",)) == text


def test_two_playbooks_for_one_title_are_refused() -> None:
    p = Playbook("a", "b", ("c",), "d", 5)
    register_playbook("test-title-once", p)
    with pytest.raises(ValueError):
        register_playbook("test-title-once", p)


def test_variant_wins_over_the_general_playbook() -> None:
    general, special = Playbook("g", "n", ("s",), "d", 5), Playbook("v", "n", ("s",), "d", 5)
    register_playbook("test-title-var", general)
    register_playbook("test-title-var|x", special)
    assert playbook_for("test-title-var", "x") is special
    assert playbook_for("test-title-var", "y") is general
    assert playbook_for("test-title-var") is general
