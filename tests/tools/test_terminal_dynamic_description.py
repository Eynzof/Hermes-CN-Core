"""The terminal tool's runtime-adaptive description and `shell` enum.

``_detect_shell_for_description`` and ``_build_dynamic_terminal_description`` in
``tools/terminal_tool.py`` translate "what can this host run?" into the sentence
the model reads and into the ``shell`` enum it may choose from.

Host shape arrives as a :class:`tools.environments.shell_modes.ShellProbes` value
(platform as data), so the Windows shapes are covered on any CI host without
patching ``platform.system`` or pretending ``shutil.which`` is something else.
The host-native arms exercise the real probes.
"""

from __future__ import annotations

import json

import pytest

from tools.environments import shell_modes as sm
from tools.registry import registry
from tools.terminal_tool import (
    TERMINAL_TOOL_DESCRIPTION,
    _build_dynamic_terminal_description,
    _detect_shell_for_description,
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


WINDOWS_BOTH = _probes(windows=True, bash=GIT_BASH, pwsh=PWSH7)
WINDOWS_PWSH_ONLY = _probes(windows=True, bash=None, pwsh=PWSH7)
WINDOWS_PS51_ONLY = _probes(windows=True, bash=None, pwsh=None)
POSIX_BASH_ONLY = _probes(windows=False, bash=POSIX_BASH, pwsh=None)
POSIX_BOTH = _probes(windows=False, bash=POSIX_BASH, pwsh="/usr/bin/pwsh")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    sm.clear_cache()
    monkeypatch.delenv("HERMES_SHELL_TYPE", raising=False)
    yield
    sm.clear_cache()


# --------------------------------------------------------------------------- #
# _detect_shell_for_description — the shell a call WITHOUT `shell=` runs
# --------------------------------------------------------------------------- #
def test_detect_windows_with_git_bash_is_bash():
    assert _detect_shell_for_description(WINDOWS_BOTH) == "bash"


def test_detect_windows_without_git_bash_is_pwsh7_when_installed():
    assert _detect_shell_for_description(WINDOWS_PWSH_ONLY) == "pwsh"


def test_detect_windows_without_git_bash_or_pwsh7_is_windows_powershell():
    assert _detect_shell_for_description(WINDOWS_PS51_ONLY) == "powershell"


def test_detect_posix_is_bash():
    assert _detect_shell_for_description(POSIX_BASH_ONLY) == "bash"


def test_configured_pwsh_mode_is_reported_when_the_host_can_run_it(monkeypatch):
    monkeypatch.setenv("HERMES_SHELL_TYPE", "pwsh")
    assert _detect_shell_for_description(WINDOWS_BOTH) == "pwsh"


def test_configured_bash_mode_that_cannot_run_reports_the_shell_that_will_run(
    monkeypatch,
):
    """`terminal.shell: bash` on a host without Git Bash runs PowerShell — say so."""
    monkeypatch.setenv("HERMES_SHELL_TYPE", "bash")
    assert _detect_shell_for_description(WINDOWS_PWSH_ONLY) == "pwsh"


def test_unknown_configured_mode_does_not_raise():
    """A typo'd config value must not break schema assembly."""
    import os

    old = os.environ.get("HERMES_SHELL_TYPE")
    os.environ["HERMES_SHELL_TYPE"] = "fish"
    try:
        assert _detect_shell_for_description(WINDOWS_BOTH) in (
            "bash",
            "pwsh",
            "powershell",
        )
    finally:
        if old is None:
            os.environ.pop("HERMES_SHELL_TYPE", None)
        else:
            os.environ["HERMES_SHELL_TYPE"] = old


# --------------------------------------------------------------------------- #
# _build_dynamic_terminal_description — sentence + `shell` enum
# --------------------------------------------------------------------------- #
def test_build_names_both_modes_and_keeps_them_in_the_enum():
    out = _build_dynamic_terminal_description(WINDOWS_BOTH)
    desc = out["description"]
    assert 'shell="bash"' in desc and 'shell="pwsh"' in desc
    assert out["parameters"]["properties"]["shell"]["enum"] == [
        sm.MODE_BASH,
        sm.MODE_PWSH,
    ]


def test_build_windows_without_git_bash_drops_bash_from_the_enum():
    out = _build_dynamic_terminal_description(WINDOWS_PWSH_ONLY)
    desc = out["description"]
    assert out["parameters"]["properties"]["shell"]["enum"] == [sm.MODE_PWSH]
    assert "bash mode is disabled" in desc
    assert "Git Bash" in desc  # the reason, so the model knows how to enable it


def test_build_posix_without_powershell_drops_pwsh_from_the_enum():
    out = _build_dynamic_terminal_description(POSIX_BASH_ONLY)
    desc = out["description"]
    assert out["parameters"]["properties"]["shell"]["enum"] == [sm.MODE_BASH]
    assert 'Only `shell="bash"`' in desc
    assert "PowerShell mode is disabled" in desc


def test_build_powershell_uses_powershell_sentence_and_cmdlet_refs():
    desc = _build_dynamic_terminal_description(WINDOWS_PS51_ONLY)["description"]
    assert "Windows PowerShell environment" in desc
    assert "Do NOT use Get-Content/cat/type (use read_file)" in desc
    assert "Select-String/findstr (use search_files)" in desc
    assert "Out-Host -Paging" in desc
    assert "Do NOT use cat/head/tail (use read_file)" not in desc
    assert "Execute shell commands on a Linux environment." not in desc


def test_build_pwsh_uses_pwsh_sentence():
    desc = _build_dynamic_terminal_description(WINDOWS_PWSH_ONLY)["description"]
    assert "PowerShell 7 (pwsh)" in desc
    assert "Do NOT use Get-Content/cat/type (use read_file)" in desc


def test_build_posix_bash_keeps_linux_references():
    desc = _build_dynamic_terminal_description(POSIX_BASH_ONLY)["description"]
    assert "Linux environment" in desc
    assert "Do NOT use cat/head/tail (use read_file)" in desc
    assert "Get-Content/cat/type" not in desc


def test_build_windows_bash_uses_git_bash_sentence():
    desc = _build_dynamic_terminal_description(WINDOWS_BOTH)["description"]
    assert "bash (Git Bash / MSYS) environment on Windows" in desc
    assert "Linux environment" not in desc
    assert "Do NOT use cat/head/tail (use read_file)" in desc
    assert "Get-Content/cat/type" not in desc


def test_static_description_contains_phrases_the_dynamic_path_rewrites():
    # The dynamic build is a set of str.replace() calls; if the static text drifts
    # they become silent no-ops and the model sees the Linux phrasing on Windows.
    for phrase in (
        "Execute shell commands on a Linux environment.",
        "The host OS, shell, and terminal backend are stated in your environment section",
        "Do NOT use cat/head/tail (use read_file)",
        "grep/rg/find/ls (use search_files)",
        "sed/awk (use patch)",
        "echo/heredoc file creation (use write_file)",
        "Pipe git output to cat if it might page.",
    ):
        assert phrase in TERMINAL_TOOL_DESCRIPTION


def test_build_keeps_the_schema_serializable_for_api_calls():
    """The override replaces `parameters` wholesale — it must stay valid JSON schema."""
    out = _build_dynamic_terminal_description(WINDOWS_PWSH_ONLY)
    json.dumps(out)
    assert out["parameters"]["type"] == "object"
    assert "command" in out["parameters"]["properties"]
    assert out["parameters"]["required"] == ["command"]


# --------------------------------------------------------------------------- #
# Host-native arms: the real probes on the machine running the suite
# --------------------------------------------------------------------------- #
@pytest.mark.platforms("windows")
def test_windows_host_description_matches_its_own_availability():
    out = _build_dynamic_terminal_description()
    available = sm.available_modes()
    assert out["parameters"]["properties"]["shell"]["enum"] == [
        m for m in sm.MODES if m in available
    ]
    # PowerShell mode is always runnable on Windows (5.1 ships with the OS).
    assert sm.MODE_PWSH in available
    assert "Windows" in out["description"]


@pytest.mark.platforms("posix")
def test_posix_host_offers_bash_and_says_so():
    out = _build_dynamic_terminal_description()
    assert sm.MODE_BASH in sm.available_modes()
    assert out["parameters"]["properties"]["shell"]["enum"][0] == sm.MODE_BASH


# --------------------------------------------------------------------------- #
# Registry integration
# --------------------------------------------------------------------------- #
def test_terminal_registered_with_dynamic_override():
    entry = registry.get_entry("terminal")
    assert entry is not None
    # Compare against the function on the CURRENT module object, not the one
    # imported at this file's collection time: an earlier test may have reloaded
    # tools.terminal_tool (importlib.reload), which re-registers the entry.
    import tools.terminal_tool as live_terminal_tool

    assert (
        entry.dynamic_schema_overrides
        is live_terminal_tool._build_dynamic_terminal_description
    )


def test_registry_schema_carries_the_shell_parameter():
    definitions = registry.get_definitions({"terminal"})
    schema = next(
        d["function"] for d in definitions if d["function"]["name"] == "terminal"
    )
    shell_spec = schema["parameters"]["properties"]["shell"]
    assert shell_spec["type"] == "string"
    assert set(shell_spec["enum"]) <= set(sm.MODES)
