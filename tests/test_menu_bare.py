"""Plain `importer` with no arguments: menu in a terminal, help otherwise."""

import pytest

import importer.cli as cli
import importer.commands.menu as menu_command


class _FakeMenu:
    ran = 0

    def __init__(self, *_: object, **__: object) -> None: ...

    def run(self) -> int:
        _FakeMenu.ran += 1
        return 0


def test_bare_call_in_a_terminal_opens_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMenu.ran = 0
    monkeypatch.setattr("sys.argv", ["importer"])
    monkeypatch.setattr(cli, "has_terminal", lambda: True)
    monkeypatch.setattr(menu_command, "has_terminal", lambda: True)
    monkeypatch.setattr(menu_command, "Menu", _FakeMenu)
    with pytest.raises(SystemExit) as stop:
        cli.app()
    assert stop.value.code == 0 and _FakeMenu.ran == 1


def test_bare_call_with_piped_input_shows_help_as_before(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _FakeMenu.ran = 0
    monkeypatch.setattr("sys.argv", ["importer"])
    monkeypatch.setattr(cli, "has_terminal", lambda: False)
    monkeypatch.setattr(menu_command, "Menu", _FakeMenu)
    with pytest.raises(SystemExit) as stop:
        cli.app()
    assert stop.value.code == 2 and _FakeMenu.ran == 0
    assert "Usage" in capsys.readouterr().out


def test_help_flag_never_opens_the_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    _FakeMenu.ran = 0
    monkeypatch.setattr("sys.argv", ["importer", "--help"])
    monkeypatch.setattr(cli, "has_terminal", lambda: True)
    monkeypatch.setattr(menu_command, "Menu", _FakeMenu)
    with pytest.raises(SystemExit) as stop:
        cli.app()
    assert stop.value.code == 0 and _FakeMenu.ran == 0
