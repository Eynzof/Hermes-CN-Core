"""should_use_color() precedence — P-032.

The desktop terminal (Hermes-CN-Desktop src/commands/terminal.rs) spawns Hermes
with FORCE_COLOR=1 / CLICOLOR_FORCE=1 even though stdout may not look like a TTY
to the frozen runtime. These assert the precedence:
NO_COLOR / TERM=dumb (off) > FORCE_COLOR / CLICOLOR_FORCE (on) > isatty().
"""

import sys

import pytest

from hermes_cli import colors


class _FakeStdout:
    def __init__(self, is_tty: bool) -> None:
        self._is_tty = is_tty

    def isatty(self) -> bool:
        return self._is_tty


@pytest.fixture(autouse=True)
def _clear_color_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in ("NO_COLOR", "FORCE_COLOR", "CLICOLOR_FORCE", "TERM"):
        monkeypatch.delenv(var, raising=False)


def _set_tty(monkeypatch: pytest.MonkeyPatch, is_tty: bool) -> None:
    monkeypatch.setattr(colors.sys, "stdout", _FakeStdout(is_tty))


def test_no_color_beats_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    """NO_COLOR is highest priority — even with a force flag and a TTY."""
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("FORCE_COLOR", "1")
    _set_tty(monkeypatch, True)
    assert colors.should_use_color() is False


def test_term_dumb_beats_force_color(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TERM", "dumb")
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    _set_tty(monkeypatch, True)
    assert colors.should_use_color() is False


def test_force_color_enables_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FORCE_COLOR", "1")
    _set_tty(monkeypatch, False)
    assert colors.should_use_color() is True


def test_clicolor_force_enables_without_tty(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLICOLOR_FORCE", "1")
    _set_tty(monkeypatch, False)
    assert colors.should_use_color() is True


def test_force_color_zero_does_not_force(monkeypatch: pytest.MonkeyPatch) -> None:
    """FORCE_COLOR=0 is not a force; falls back to the TTY check."""
    monkeypatch.setenv("FORCE_COLOR", "0")
    _set_tty(monkeypatch, False)
    assert colors.should_use_color() is False


def test_falls_back_to_tty_when_unforced(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_tty(monkeypatch, False)
    assert colors.should_use_color() is False
    _set_tty(monkeypatch, True)
    assert colors.should_use_color() is True
