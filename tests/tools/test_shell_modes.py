"""Shell-mode availability and resolution (``tools/environments/shell_modes.py``).

The terminal tool offers two shell modes — ``bash`` and ``pwsh`` — and this module
decides which of them *this* host can run, which binary implements each, and what
the model is told.  The four host shapes that matter are covered with fake
:class:`ShellProbes`, so the rules are exercised without pretending to be another
OS:

* Windows with Git Bash + PowerShell 7       → both modes, ``bash`` default
* Windows with PowerShell 5.1 only           → ``pwsh`` mode only (bash disabled)
* POSIX with bash only                       → ``bash`` mode only (pwsh disabled)
* POSIX with bash + PowerShell 7 installed   → both modes, ``bash`` default
"""

from __future__ import annotations

import pytest

from tools.environments import shell_modes as sm

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
PWSH7 = r"C:\Program Files\PowerShell\7\pwsh.exe"
PS51 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
POSIX_BASH = "/usr/bin/bash"


def _probes(
    *,
    windows: bool,
    bash: str | None = None,
    pwsh: str | None = None,
    powershell: str | None = None,
) -> sm.ShellProbes:
    """A probe set for one host shape; ``None`` means "not installed"."""
    return sm.ShellProbes(
        is_windows=windows,
        find_bash=lambda **_: bash,
        find_bash_posix=lambda: bash or POSIX_BASH,
        find_pwsh=lambda: pwsh,
        find_powershell=lambda: powershell or (PS51 if windows else "powershell"),
    )


@pytest.fixture(autouse=True)
def _reset_cache(monkeypatch):
    """The real-probe cache is keyed by mode only — never leak it across tests."""
    sm.clear_cache()
    monkeypatch.delenv("HERMES_SHELL_TYPE", raising=False)
    yield
    sm.clear_cache()


# --------------------------------------------------------------------------- #
# normalize_mode
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("raw", ["bash", "BASH", " bash ", "git-bash", "gitbash", "sh"])
def test_bash_spellings_normalize(raw):
    assert sm.normalize_mode(raw) == sm.MODE_BASH


@pytest.mark.parametrize(
    "raw", ["pwsh", "PowerShell", "powershell", "ps", "ps5", "ps7"]
)
def test_pwsh_spellings_normalize(raw):
    assert sm.normalize_mode(raw) == sm.MODE_PWSH


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_unset_means_auto(raw):
    assert sm.normalize_mode(raw) == sm.MODE_AUTO


def test_unknown_mode_is_a_clear_error():
    with pytest.raises(sm.UnknownShellMode) as excinfo:
        sm.normalize_mode("cmd")
    # The message has to name the valid modes: it reaches the model verbatim.
    assert "bash" in str(excinfo.value) and "pwsh" in str(excinfo.value)


# --------------------------------------------------------------------------- #
# availability / default_mode
# --------------------------------------------------------------------------- #
def test_windows_with_git_bash_offers_both_modes():
    probes = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
    assert sm.available_modes(probes) == (sm.MODE_BASH, sm.MODE_PWSH)
    assert sm.default_mode(probes) == sm.MODE_BASH


def test_windows_without_git_bash_disables_bash_mode():
    probes = _probes(windows=True, bash=None, pwsh=None, powershell=PS51)
    assert sm.available_modes(probes) == (sm.MODE_PWSH,)
    assert sm.default_mode(probes) == sm.MODE_PWSH
    assert sm.is_available(sm.MODE_BASH, probes) is False
    # The reason must name the missing install, not a generic failure.
    assert "Git Bash" in sm.unavailability_reason(sm.MODE_BASH, probes)


def test_windows_without_pwsh7_still_offers_pwsh_mode_via_51():
    probes = _probes(windows=True, bash=GIT_BASH, pwsh=None, powershell=PS51)
    assert sm.is_available(sm.MODE_PWSH, probes) is True
    assert sm.resolve_shell_for_mode(sm.MODE_PWSH, probes) == ("powershell", PS51)


def test_posix_without_powershell_disables_pwsh_mode():
    probes = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
    assert sm.available_modes(probes) == (sm.MODE_BASH,)
    assert sm.default_mode(probes) == sm.MODE_BASH
    assert sm.is_available(sm.MODE_PWSH, probes) is False
    assert "PowerShell" in sm.unavailability_reason(sm.MODE_PWSH, probes)


def test_posix_with_powershell_installed_offers_both_modes():
    probes = _probes(windows=False, bash=POSIX_BASH, pwsh="/usr/bin/pwsh")
    assert sm.available_modes(probes) == (sm.MODE_BASH, sm.MODE_PWSH)
    assert sm.default_mode(probes) == sm.MODE_BASH
    assert sm.resolve_shell_for_mode(sm.MODE_PWSH, probes) == ("pwsh", "/usr/bin/pwsh")


def test_configured_mode_wins_when_available(monkeypatch):
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    probes = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
    assert sm.default_mode(probes) == sm.MODE_PWSH


def test_configured_mode_that_cannot_run_falls_back_for_the_description(monkeypatch):
    """A stale `terminal.shell: pwsh` must not make the description claim PowerShell."""
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    probes = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
    assert sm.default_mode(probes) == sm.MODE_BASH


# --------------------------------------------------------------------------- #
# resolve_shell_for_mode — the strict contract the executor relies on
# --------------------------------------------------------------------------- #
def test_resolve_bash_returns_git_bash_on_windows():
    probes = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
    assert sm.resolve_shell_for_mode(sm.MODE_BASH, probes) == ("bash", GIT_BASH)


def test_resolve_pwsh_prefers_powershell_7():
    probes = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
    assert sm.resolve_shell_for_mode(sm.MODE_PWSH, probes) == ("pwsh", PWSH7)


def test_resolve_bash_without_git_bash_raises_instead_of_switching_shell():
    probes = _probes(windows=True, bash=None, pwsh=PWSH7)
    with pytest.raises(sm.ShellModeUnavailable) as excinfo:
        sm.resolve_shell_for_mode(sm.MODE_BASH, probes)
    exc = excinfo.value
    assert exc.mode == sm.MODE_BASH
    assert exc.available == (sm.MODE_PWSH,)
    assert "Git Bash" in str(exc)


def test_resolve_pwsh_without_powershell_raises_instead_of_switching_shell():
    probes = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
    with pytest.raises(sm.ShellModeUnavailable) as excinfo:
        sm.resolve_shell_for_mode(sm.MODE_PWSH, probes)
    assert excinfo.value.mode == sm.MODE_PWSH
    assert excinfo.value.available == (sm.MODE_BASH,)


def test_resolve_auto_defers_to_the_configured_resolver(monkeypatch):
    """`auto` keeps the fork's existing bash → pwsh → powershell preference chain."""
    import tools.environments.local as local_env

    monkeypatch.setattr(local_env, "_resolve_shell", lambda: ("powershell", PS51))
    assert sm.resolve_shell_for_mode(sm.MODE_AUTO) == ("powershell", PS51)


# --------------------------------------------------------------------------- #
# describe_modes — what the model is told
# --------------------------------------------------------------------------- #
def test_describe_both_modes_names_the_default():
    text = sm.describe_modes(_probes(windows=True, bash=GIT_BASH, pwsh=PWSH7))
    assert 'shell="bash"' in text and 'shell="pwsh"' in text
    assert "Default" in text


def test_describe_windows_without_git_bash_says_bash_is_disabled():
    text = sm.describe_modes(_probes(windows=True, bash=None, pwsh=PWSH7))
    assert "disabled" in text.lower()
    assert '`shell="bash"` returns an error' in text
    assert "Git Bash" in text
    # Nothing in it may invite POSIX syntax on this host.
    assert '`shell="bash"` runs POSIX syntax' not in text


def test_describe_posix_without_powershell_says_only_bash_is_allowed():
    text = sm.describe_modes(_probes(windows=False, bash=POSIX_BASH, pwsh=None))
    assert "Only" in text and "bash" in text
    assert '`shell="pwsh"` returns an error' in text


# --------------------------------------------------------------------------- #
# shell_mode_scope — the per-call ContextVar
# --------------------------------------------------------------------------- #
def test_scope_sets_and_restores_the_mode():
    assert sm.current_shell_mode() is None
    with sm.shell_mode_scope("PowerShell"):
        assert sm.current_shell_mode() == sm.MODE_PWSH
    assert sm.current_shell_mode() is None


def test_nested_scopes_restore_the_outer_mode():
    with sm.shell_mode_scope("bash"):
        with sm.shell_mode_scope("pwsh"):
            assert sm.current_shell_mode() == sm.MODE_PWSH
        assert sm.current_shell_mode() == sm.MODE_BASH


def test_shell_type_mode_maps_powershell_51_to_pwsh_mode():
    assert sm.shell_type_mode("bash") == sm.MODE_BASH
    assert sm.shell_type_mode("pwsh") == sm.MODE_PWSH
    assert sm.shell_type_mode("powershell") == sm.MODE_PWSH
