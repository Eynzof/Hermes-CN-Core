"""``terminal(shell="auto")`` — the default mode picks the shell from the command (P-075).

Three rules, and this file pins one section per rule:

* **A host with ONE shell never detects and never reports.**  `auto` can only mean the
  shell that exists, so consulting the parser would be pointless and naming the shell on
  every result would be noise.
* **A host with BOTH shells parses the command** — bash syntax runs in bash, a cmdlet
  runs in PowerShell — and the result says which one answered, because the model cannot
  tell from the output and the wrong shell is a wasted turn.
* **An explicit ``shell="bash"``/``"pwsh"`` is untouched**: no parsing, no report.

Host shape is injected through ``ShellProbes``, so the one-shell arms need no faked OS;
the E2E arms run the real shells and skip when this host has only one.
"""

from __future__ import annotations

import json

import pytest

from tools.environments import shell_modes as sm
from tools.environments.shell_detect import SHELL_BASH, SHELL_PWSH, detect_command_shell
from tools.terminal_tool import (
    _merge_result_metadata,
    _plan_execution,
    _resolve_shell_mode,
    terminal_tool,
)
from tools.terminal_tool_shell_mode import (
    ShellChoice,
    build_dynamic_description,
    shell_result_fields,
)

GIT_BASH = r"C:\Program Files\Git\bin\bash.exe"
PWSH7 = r"C:\Program Files\PowerShell\7\pwsh.exe"
PS51 = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
POSIX_BASH = "/usr/bin/bash"


def _probes(*, windows: bool, bash: str | None = None, pwsh: str | None = None):
    return sm.ShellProbes(
        is_windows=windows,
        find_bash=lambda **_: bash,
        find_bash_posix=lambda: bash or POSIX_BASH,
        find_pwsh=lambda: pwsh,
        find_powershell=lambda: PS51 if windows else "powershell",
    )


WINDOWS_BOTH = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
WINDOWS_PWSH_ONLY = _probes(windows=True, bash=None, pwsh=PWSH7)
POSIX_BASH_ONLY = _probes(windows=False, bash=POSIX_BASH, pwsh=None)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    sm.clear_cache()
    monkeypatch.setenv("HERMES_SHELL_TYPE", "auto")
    yield
    sm.clear_cache()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path / ".hermes"))
    return tmp_path


def _fake_host(monkeypatch, probes):
    """Make ``sm.default_probes()`` answer with *probes* for the whole test."""
    monkeypatch.setattr(sm, "default_probes", lambda: probes)
    sm.clear_cache()
    return probes


# --------------------------------------------------------------------------- #
# resolve_auto_shell — the decision, in isolation
# --------------------------------------------------------------------------- #
def test_one_shell_host_never_detects(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_PWSH_ONLY)
    # A command that is unambiguously POSIX: on this host it still must go to PowerShell,
    # because that is the only shell. `dual=False` is what keeps the result silent.
    result = sm.resolve_auto_shell("x=abc; echo ${x}")
    assert result == sm.AutoResolution(sm.MODE_PWSH, "pwsh", False, False)


def test_one_bash_host_never_detects(monkeypatch):
    _fake_host(monkeypatch, POSIX_BASH_ONLY)
    result = sm.resolve_auto_shell("Get-ChildItem -Recurse")
    assert result == sm.AutoResolution(sm.MODE_BASH, "bash", False, False)


def test_two_shell_host_parses_bash(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    result = sm.resolve_auto_shell("for f in *.txt; do wc -l \"$f\"; done")
    assert result.mode == sm.MODE_BASH and result.detected and result.dual


def test_two_shell_host_parses_powershell(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    result = sm.resolve_auto_shell("Get-ChildItem -Recurse | Select-Object -First 5")
    assert result.mode == sm.MODE_PWSH and result.detected and result.dual


def test_two_shell_host_keeps_the_default_for_a_neutral_command(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    result = sm.resolve_auto_shell("git status")
    assert result.mode == sm.MODE_BASH
    assert result.detected is False  # nothing was claimed about the command
    assert result.dual is True  # …but the choice still gets reported


def test_configured_mode_pins_auto_without_detecting(monkeypatch):
    """`terminal.shell: pwsh` is a preference; a POSIX command must not overrule it."""
    _fake_host(monkeypatch, WINDOWS_BOTH)
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    result = sm.resolve_auto_shell("for f in *.txt; do echo $f; done")
    assert result == sm.AutoResolution(sm.MODE_PWSH, "pwsh", False, True)


def test_configured_mode_that_cannot_run_still_parses(monkeypatch):
    """A pinned mode the host lacks is not a pin: the command decides again."""
    _fake_host(monkeypatch, POSIX_BASH_ONLY)
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    assert sm.resolve_auto_shell("Get-ChildItem").mode == sm.MODE_BASH


def test_auto_resolution_survives_a_non_string_command(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    result = sm.resolve_auto_shell(None)
    assert result.mode in sm.MODES and result.detected is False


# --------------------------------------------------------------------------- #
# resolve_shell_mode — what the tool gets, and what it will report
# --------------------------------------------------------------------------- #
def test_explicit_mode_is_not_parsed_and_not_reported(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    choice = _resolve_shell_mode("bash", "local", "Get-ChildItem")
    assert choice == ShellChoice(sm.MODE_BASH, "bash")
    assert choice.plan_fields() == {"shell_mode": sm.MODE_BASH, "shell_notice": None}


def test_auto_on_a_two_shell_host_is_reported(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    choice = _resolve_shell_mode("auto", "local", "Write-Output ('x' + 2)")
    assert choice.mode == sm.MODE_PWSH and choice.notice is True
    assert choice.plan_fields()["shell_notice"] == "pwsh"


def test_auto_on_a_one_shell_host_is_not_reported(monkeypatch):
    _fake_host(monkeypatch, POSIX_BASH_ONLY)
    choice = _resolve_shell_mode(None, "local", "Get-ChildItem")
    assert choice.mode == sm.MODE_BASH
    assert choice.plan_fields() == {"shell_mode": sm.MODE_BASH, "shell_notice": None}


def test_auto_on_a_remote_backend_stays_with_the_sandbox(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    choice = _resolve_shell_mode("auto", "docker", "Get-ChildItem")
    assert choice == ShellChoice(None, None, auto=True)
    assert choice.plan_fields() == {"shell_mode": None, "shell_notice": None}


def test_an_explicit_mode_on_a_remote_backend_is_still_refused():
    from tools.terminal_tool import _Rejected

    with pytest.raises(_Rejected):
        _resolve_shell_mode("bash", "docker", "echo hi")


# --------------------------------------------------------------------------- #
# The result envelope
# --------------------------------------------------------------------------- #
def test_result_names_the_shell_only_when_the_plan_says_so():
    notice = _plan_execution(
        "echo hi", task_id="t", timeout=None, background=False, _host_local=False
    )
    notice.shell_notice = "bash"
    data = json.loads(_merge_result_metadata('{"output": "hi", "exit_code": 0}', plan=notice))
    assert data["shell"] == "bash"
    assert data["shell_mode"] == "auto"

    silent = _plan_execution(
        "echo hi", task_id="t", timeout=None, background=False, _host_local=False
    )
    silent.shell_notice = None
    data = json.loads(_merge_result_metadata('{"output": "hi", "exit_code": 0}', plan=silent))
    assert "shell" not in data and "shell_mode" not in data


def test_error_envelopes_keep_their_shape():
    fields = shell_result_fields(type("P", (), {"shell_notice": "bash"})())
    assert fields == {"shell": "bash", "shell_mode": "auto"}
    assert shell_result_fields(None) == {}
    assert shell_result_fields(object()) == {}


def test_plan_fields_describe_powershell_51_distinctly():
    """`pwsh` and `powershell` are different binaries; the report keeps them apart."""
    choice = ShellChoice("pwsh", "powershell", auto=True, notice=True)
    assert choice.plan_fields()["shell_notice"] == "powershell"


# --------------------------------------------------------------------------- #
# The description tells the model which rule is in force
# --------------------------------------------------------------------------- #
def test_two_shell_description_says_auto_chooses_per_command():
    desc = build_dynamic_description(WINDOWS_BOTH)["description"]
    assert "ONE shell per command" in desc
    assert "or PowerShell on Windows" in desc
    assert "picks one per command" in desc


def test_one_shell_description_says_auto_is_that_shell():
    desc = build_dynamic_description(WINDOWS_PWSH_ONLY)["description"]
    assert "no detection" in desc


def test_pinned_mode_is_named_in_the_description(monkeypatch):
    _fake_host(monkeypatch, WINDOWS_BOTH)
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    desc = build_dynamic_description(WINDOWS_BOTH)["description"]
    assert "pinned by `terminal.shell`" in desc


# --------------------------------------------------------------------------- #
# Detect → run, end to end (real shells; skipped when this host has one)
# --------------------------------------------------------------------------- #
def _needs_both_shells() -> bool:
    available = sm.available_modes()
    return sm.MODE_BASH in available and sm.MODE_PWSH in available


@pytest.mark.platforms("windows")
def test_auto_runs_a_posix_command_in_bash_and_says_so(isolated_home):
    if not _needs_both_shells():
        pytest.skip("host lacks Git Bash or PowerShell")
    result = json.loads(
        terminal_tool('x=abc; echo "auto:${x:1}"', task_id="auto-bash")
    )
    assert result.get("error") is None, result
    assert "auto:bc" in result["output"], result["output"]
    assert result["shell"] == SHELL_BASH
    assert result["shell_mode"] == "auto"


@pytest.mark.platforms("windows")
def test_auto_runs_a_cmdlet_in_powershell_and_says_so(isolated_home):
    if not _needs_both_shells():
        pytest.skip("host lacks Git Bash or PowerShell")
    result = json.loads(
        terminal_tool("Write-Output ('auto:' + (2+2))", task_id="auto-pwsh")
    )
    assert result.get("error") is None, result
    assert "auto:4" in result["output"], result["output"]
    assert result["shell"] in ("pwsh", "powershell")
    assert result["shell_mode"] == "auto"


@pytest.mark.platforms("windows")
def test_auto_reports_the_neutral_command_it_sent_to_the_default(isolated_home):
    if not _needs_both_shells():
        pytest.skip("host lacks Git Bash or PowerShell")
    result = json.loads(terminal_tool("echo neutral-ok", task_id="auto-neutral"))
    assert result.get("error") is None, result
    assert "neutral-ok" in result["output"]
    # `echo` runs in both shells, so the parser claims nothing — the host default runs it
    # and the result still names it, because on this host there WAS a choice.
    default = sm.default_mode()
    assert result["shell"] in ("bash", "pwsh", "powershell")
    assert result["shell_mode"] == "auto"
    assert detect_command_shell("echo neutral-ok").detected is False
    assert (result["shell"] == "bash") is (default == sm.MODE_BASH)


@pytest.mark.platforms("windows")
def test_explicit_mode_returns_no_shell_report(isolated_home):
    result = json.loads(
        terminal_tool("echo explicit-ok", shell="bash", task_id="auto-explicit")
    )
    assert result.get("error") is None, result
    assert "shell" not in result and "shell_mode" not in result


@pytest.mark.platforms("windows")
def test_auto_background_spawn_is_reported_too(isolated_home):
    if not _needs_both_shells():
        pytest.skip("host lacks Git Bash or PowerShell")
    result = json.loads(
        terminal_tool(
            "x=abc; echo \"bg:${x:1}\"",
            background=True,
            task_id="auto-bg",
        )
    )
    assert result.get("error") is None, result
    assert result["shell"] == SHELL_BASH
    assert result["shell_mode"] == "auto"
