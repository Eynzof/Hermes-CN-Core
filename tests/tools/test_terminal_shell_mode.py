"""Per-call shell mode: ``terminal(shell="bash" | "pwsh")``.

The terminal tool must be able to run BOTH shells on a Windows host and must
refuse — loudly, without running the command — a shell mode the host cannot run:

* Windows + Git Bash + PowerShell  → ``shell="bash"`` (Git Bash) and
  ``shell="pwsh"`` (PowerShell 7, else Windows PowerShell 5.1) both work, in the
  same session and the same environment.
* Windows without Git Bash         → ``shell="bash"`` returns an error naming Git
  Bash, never silently runs PowerShell.
* POSIX without PowerShell         → ``shell="pwsh"`` returns an error naming
  PowerShell, never silently runs bash.

Host shape is injected as shell probes, so the "cannot run" arms need no faked OS;
the "both modes work" arm runs the real shells on the host and skips when one is
genuinely absent.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from tools.environments import shell_modes as sm
from tools.terminal_tool import (
    _plan_execution,
    _Rejected,
    _run_foreground,
    terminal_tool,
)

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
PWSH7 = r"C:\Program Files\PowerShell\7\pwsh.exe"
PS51 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
POSIX_BASH = "/usr/bin/bash"


def _probes(
    *, windows: bool, bash: str | None = None, pwsh: str | None = None
) -> sm.ShellProbes:
    return sm.ShellProbes(
        is_windows=windows,
        find_bash=lambda **_: bash,
        find_bash_posix=lambda: bash or POSIX_BASH,
        find_pwsh=lambda: pwsh,
        find_powershell=lambda: PS51 if windows else "powershell",
    )


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    sm.clear_cache()
    monkeypatch.delenv("HERMES_SHELL_TYPE", raising=False)
    monkeypatch.setenv("HERMES_SHELL_TYPE", "auto")
    yield
    sm.clear_cache()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    return tmp_path


@pytest.fixture
def host_without_git_bash(monkeypatch):
    """A host whose only shell is PowerShell (a Windows box without Git for Windows)."""
    probes = _probes(windows=True, bash=None, pwsh=PWSH7)
    monkeypatch.setattr(sm, "default_probes", lambda: probes)
    sm.clear_cache()
    return probes


@pytest.fixture
def host_without_powershell(monkeypatch):
    """A host whose only shell is bash (a Linux box without pwsh installed)."""
    probes = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
    monkeypatch.setattr(sm, "default_probes", lambda: probes)
    sm.clear_cache()
    return probes


# --------------------------------------------------------------------------- #
# Validation: the schema argument itself
# --------------------------------------------------------------------------- #
def test_unknown_shell_mode_is_refused_with_the_valid_names(isolated_home):
    result = json.loads(
        terminal_tool("echo hi", shell="cmd", task_id="shell-mode-unknown")
    )
    assert result["error"]
    assert "bash" in result["error"] and "pwsh" in result["error"]
    assert result.get("exit_code") is None


def test_omitted_shell_means_the_configured_default():
    plan = _plan_execution(
        "echo hi",
        task_id="t-default",
        timeout=None,
        background=False,
        _host_local=False,
    )
    assert plan.shell_mode is None


@pytest.mark.parametrize("value", [None, "", "   ", "auto", "AUTO"])
def test_auto_and_blank_are_treated_as_unset(value):
    plan = _plan_execution(
        "echo hi",
        task_id="t-auto",
        timeout=None,
        background=False,
        _host_local=False,
        shell=value,
    )
    assert plan.shell_mode is None


def test_shell_alias_resolves_to_its_mode():
    plan = _plan_execution(
        "echo hi",
        task_id="t-alias",
        timeout=None,
        background=False,
        _host_local=False,
        shell="Git-Bash",
    )
    assert plan.shell_mode == sm.MODE_BASH


# --------------------------------------------------------------------------- #
# Windows without Git Bash → bash mode is disabled, as an ERROR
# --------------------------------------------------------------------------- #
def test_bash_mode_without_git_bash_errors_and_runs_nothing(
    host_without_git_bash, isolated_home, tmp_path
):
    marker = tmp_path / "must-not-exist.txt"
    result = json.loads(
        terminal_tool(
            f"echo ran > {marker}",
            shell="bash",
            task_id="shell-mode-no-bash",
        )
    )
    assert result["error"]
    assert "Git Bash" in result["error"]
    assert "not available" in result["error"]
    # The whole point of erroring: the command was NOT run under another shell.
    assert not marker.exists()


def test_bash_mode_error_names_the_available_mode(host_without_git_bash):
    result = json.loads(
        terminal_tool("echo hi", shell="bash", task_id="shell-mode-hint")
    )
    assert result["error"]
    assert '"pwsh"' in result["error"]


def test_pwsh_mode_still_works_on_a_host_without_git_bash(
    host_without_git_bash, isolated_home
):
    result = json.loads(
        terminal_tool(
            "Write-Output 'pwsh-ok'",
            shell="pwsh",
            task_id="shell-mode-pwsh-only",
        )
    )
    assert result.get("error") is None, result
    assert "pwsh-ok" in result["output"]


# --------------------------------------------------------------------------- #
# POSIX without PowerShell → pwsh mode is disabled, as an ERROR
# --------------------------------------------------------------------------- #
def test_pwsh_mode_without_powershell_errors(
    host_without_powershell, isolated_home, tmp_path
):
    marker = tmp_path / "must-not-exist-ps.txt"
    result = json.loads(
        terminal_tool(
            f"Write-Output 'ran' > {marker}",
            shell="pwsh",
            task_id="shell-mode-no-pwsh",
        )
    )
    assert result["error"]
    assert "PowerShell" in result["error"]
    assert not marker.exists()


def test_bash_mode_still_works_on_a_host_without_powershell(
    host_without_powershell, isolated_home
):
    result = json.loads(
        terminal_tool("echo bash-ok", shell="bash", task_id="shell-mode-bash-only")
    )
    assert result.get("error") is None, result
    assert "bash-ok" in result["output"]


# --------------------------------------------------------------------------- #
# Backends other than the local one have no host shell to switch
# --------------------------------------------------------------------------- #
def test_shell_argument_is_refused_for_a_remote_backend(monkeypatch):
    monkeypatch.setenv("TERMINAL_ENV", "ssh")
    with pytest.raises(_Rejected) as excinfo:
        _plan_execution(
            "echo hi",
            task_id="t-ssh",
            timeout=None,
            background=False,
            _host_local=False,
            shell="bash",
        )
    assert "local" in json.loads(excinfo.value.result_json)["error"]


# --------------------------------------------------------------------------- #
# The mode reaches the environment (threading check, no real spawn)
# --------------------------------------------------------------------------- #
def test_run_foreground_hands_the_mode_to_the_local_environment(monkeypatch):
    """`_run_foreground` must pass shell_mode only for the local backend."""
    seen: dict = {}

    class _StubEnv:
        cwd = ""

        def execute(self, command, **kwargs):
            seen.update(kwargs)
            return {"output": "ok", "returncode": 0}

    plan = _plan_execution(
        "echo hi",
        task_id="t-thread",
        timeout=None,
        background=False,
        _host_local=False,
        shell="bash",
    )
    _run_foreground(
        "echo hi",
        _StubEnv(),
        plan,
        task_id="t-thread",
        session_id=None,
        session_key="s",
        cwd_key="s",
        workdir=None,
        approval_note=None,
        clear_interrupt=False,
        metered=False,
    )
    assert seen.get("shell_mode") == sm.MODE_BASH


def test_run_foreground_omits_the_mode_when_not_requested():
    seen: dict = {}

    class _StubEnv:
        cwd = ""

        def execute(self, command, **kwargs):
            seen.update(kwargs)
            return {"output": "ok", "returncode": 0}

    plan = _plan_execution(
        "echo hi",
        task_id="t-thread-none",
        timeout=None,
        background=False,
        _host_local=False,
    )
    _run_foreground(
        "echo hi",
        _StubEnv(),
        plan,
        task_id="t-thread-none",
        session_id=None,
        session_key="s",
        cwd_key="s",
        workdir=None,
        approval_note=None,
        clear_interrupt=False,
        metered=False,
    )
    assert "shell_mode" not in seen


# --------------------------------------------------------------------------- #
# The environment resolves per-call modes strictly
# --------------------------------------------------------------------------- #
def test_local_environment_raises_for_an_unavailable_override(monkeypatch):
    """A per-call mode the host cannot run must never degrade to the other shell."""
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd="", timeout=30)
    try:
        probes = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
        monkeypatch.setattr(sm, "default_probes", lambda: probes)
        sm.clear_cache()
        with pytest.raises(sm.ShellModeUnavailable):
            env.execute("echo hi", shell_mode="pwsh", timeout=30)
    finally:
        env.cleanup()


# --------------------------------------------------------------------------- #
# Background spawns honour the mode (the argv the worker gets)
# --------------------------------------------------------------------------- #
def test_shell_for_spawn_without_a_mode_is_the_default():
    """No mode / `auto` → the resolver's own default, exactly as before P-073."""
    from tools.process_registry import _shell_for_spawn

    assert _shell_for_spawn(None) is None
    assert _shell_for_spawn(sm.MODE_AUTO) is None


def test_shell_for_spawn_resolves_a_mode_this_host_can_run():
    """The helper hands `_scope_argv` a real (shell_type, path) pair."""
    from tools.process_registry import _shell_for_spawn

    available = sm.available_modes()
    assert available, "every host has at least one shell"
    shell_type, path = _shell_for_spawn(available[0])
    assert path
    assert shell_type in ("bash", "pwsh", "powershell")


@pytest.mark.platforms("windows")
def test_background_argv_uses_the_requested_git_bash():
    """`terminal(shell="bash", background=true)` builds a `bash -lc` argv."""
    from tools.process_registry import ProcessRegistry

    probes = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
    shell = sm.resolve_shell_for_mode(sm.MODE_BASH, probes)
    registry = ProcessRegistry()
    session = SimpleNamespace(cwd=r"C:\work", systemd_unit="")
    argv = registry._scope_argv(session, "echo hi", "unit", "Local", shell=shell)
    assert argv[0] == GIT_BASH
    assert "-lc" in argv


# --------------------------------------------------------------------------- #
# Both modes, one environment, real shells (host-native)
# --------------------------------------------------------------------------- #
@pytest.mark.platforms("windows")
def test_one_session_runs_bash_and_pwsh(isolated_home):
    """`shell="bash"` and `shell="pwsh"` must reach DIFFERENT shells in one session.

    Each command uses syntax only its own shell can execute successfully:
    ``x=abc; echo "bash:${x:1}"`` is POSIX (PowerShell parses ``x=abc`` as a
    command name and has no ``${var:offset}`` slicing), and
    ``Write-Output ('ps:' + (2+2))`` is a PowerShell cmdlet with PowerShell string
    concatenation (bash rejects the parenthesised expression outright).  A silent
    fallback to one shell therefore fails the expected-output assertion instead of
    passing.  No external binary is relied on — ``printf`` and friends can sit on
    PATH for both shells on a Git-for-Windows host.
    """
    if (
        sm.MODE_BASH not in sm.available_modes()
        or sm.MODE_PWSH not in sm.available_modes()
    ):
        pytest.skip("host lacks Git Bash or PowerShell")

    bash_result = json.loads(
        terminal_tool(
            'x=abc; echo "bash:${x:1}"',
            shell="bash",
            task_id="shell-mode-both",
        )
    )
    assert bash_result.get("error") is None, bash_result
    assert "bash:bc" in bash_result["output"], bash_result["output"]

    ps_result = json.loads(
        terminal_tool(
            "Write-Output ('ps:' + (2+2))",
            shell="pwsh",
            task_id="shell-mode-both",
        )
    )
    assert ps_result.get("error") is None, ps_result
    assert "ps:4" in ps_result["output"], ps_result["output"]

    # …and the default (no `shell=`) path still runs in the configured shell.
    default_result = json.loads(
        terminal_tool("echo default-ok", task_id="shell-mode-both")
    )
    assert default_result.get("error") is None, default_result
    assert "default-ok" in default_result["output"]


# --------------------------------------------------------------------------- #
# Each mode runs through its own compatibility layer
# --------------------------------------------------------------------------- #
@pytest.mark.platforms("windows")
def test_bash_mode_always_applies_the_git_bash_command_fix(isolated_home):
    """bash mode = Git Bash + ``fix_bash_command``.

    ``rev`` has no Git Bash equivalent binary; a native-POSIX command word is only
    satisfied by the fallback definition ``tools/environments/bash_fix.py`` injects.
    Without the fixer the command fails with "rev: command not found".
    """
    if sm.MODE_BASH not in sm.available_modes():
        pytest.skip("host has no Git Bash")
    result = json.loads(
        terminal_tool("echo abc | rev", shell="bash", task_id="shell-mode-bashfix")
    )
    assert result.get("error") is None, result
    assert "cba" in result["output"], result["output"]


@pytest.mark.platforms("windows")
def test_pwsh_mode_downlevels_ps7_syntax_for_windows_powershell(
    monkeypatch, isolated_home
):
    """pwsh mode with no PowerShell 7 = 5.1 + ``pwsh_transform``.

    ``&&`` is PS7-only: Windows PowerShell 5.1 raises a ParserError on it.  Both
    statements producing output is the proof that ``tools/environments/pwsh_fix.py``
    translated the command before 5.1 parsed it.
    """
    import tools.environments.local as local_env

    probes = _probes(windows=True, bash=GIT_BASH, pwsh=None)  # PowerShell 7 absent
    monkeypatch.setattr(sm, "default_probes", lambda: probes)
    sm.clear_cache()
    # The 5.1 binary must really exist for this arm to mean anything.
    import os

    if not os.path.isfile(PS51):
        pytest.skip("Windows PowerShell 5.1 is not installed")
    monkeypatch.setattr(local_env, "_find_powershell", lambda: PS51)

    result = json.loads(
        terminal_tool(
            "Write-Output 'one' && Write-Output 'two'",
            shell="pwsh",
            task_id="shell-mode-downlevel",
        )
    )
    assert result.get("error") is None, result
    assert "one" in result["output"] and "two" in result["output"], result["output"]
