import pytest

from importer.core import playbook
from importer.core.playbook import Playbook, fill_template, playbook_for, register_playbook


def test_template_uses_safe_names_and_keeps_tokens_otherwise() -> None:
    text = "SELECT latest(<metric>) FROM Metric FACET <labels>"
    assert (
        fill_template(text, "node_load1", ("cpu", "mode"))
        == "SELECT latest(node_load1) FROM Metric FACET cpu, mode"
    )
    # a name with quotes or spaces never reaches the output
    assert fill_template(text, "x') OR 1=1 --", ("a b",)) == text


@pytest.fixture
def empty_book(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(playbook, "_BOOK", {})
    monkeypatch.setattr(playbook, "_loaded", True)


def test_two_playbooks_for_one_title_are_refused(empty_book: None) -> None:
    p = Playbook("a", "b", ("c",), "d", 5)
    register_playbook("test-title-once", p)
    with pytest.raises(ValueError):
        register_playbook("test-title-once", p)


def test_variant_wins_over_the_general_playbook(empty_book: None) -> None:
    general, special = Playbook("g", "n", ("s",), "d", 5), Playbook("v", "n", ("s",), "d", 5)
    register_playbook("test-title-var", general)
    register_playbook("test-title-var|x", special)
    assert playbook_for("test-title-var", "x") is special
    assert playbook_for("test-title-var", "y") is general
    assert playbook_for("test-title-var") is general


def test_every_playbook_belongs_to_a_remediation_rule() -> None:
    import importer.sources  # noqa: F401
    from importer.core import remediation
    from importer.core.playbook import all_playbooks

    titles = {t for _, t, _ in remediation._COMPILED}
    for key in all_playbooks():
        assert key.split("|")[0] in titles, key


def test_playbooks_are_complete_and_safe() -> None:
    from importer.core.playbook import TOKEN, all_playbooks

    seen = {}
    for key, pb in all_playbooks().items():
        seen[id(pb)] = (key, pb)
    for key, pb in seen.values():
        assert pb.grafana and pb.newrelic and pb.steps and pb.differs, key
        assert 1 <= pb.effort_minutes <= 480, key
        assert all(s.strip() for s in pb.steps), key
        assert pb.links, f"{key}: no documentation link"
        for link in pb.links:
            assert link.url.startswith("https://"), (key, link.url)
        # a template holds only the tokens fill_template knows
        leftover = TOKEN.sub("", pb.template)
        assert "<metric>" not in leftover and "<labels>" not in leftover, key
        # no secret-looking text and no emoji
        text = " ".join([pb.grafana, pb.newrelic, pb.differs, *pb.steps, pb.template])
        assert "api_key" not in text.lower() and "password=" not in text.lower(), key
        assert text.isascii(), key
