r"""Refusal tests: cmd.exe batch jumps have no faithful bash translation.

``goto``/glued ``:label`` lines/``call :label``/``call x.bat``/``exit /b`` cannot
be rewritten to bash: arbitrary jumps and batch return codes have no local text
equivalent, and a translated ``exit N`` would *kill* the persistent Bash session
or block the interactive ``exec bash -i`` startup (design
cmd control-flow design §3.3, probed).  They are refused
through the existing ``BashFix.unsupported`` channel — the command text stays
byte-for-byte and the Bash tool's ``_wrap_command`` turns ``unsupported`` into
a ``ToolError`` before spawning, with the ``_UNSUPPORTED_BODIES`` reason in the
message.

These tests assert the scanner records the refusal key and leaves the text
untouched.  The app-layer ``bash_fix.fix_bash_command`` re-export already
forwards ``unsupported`` (asserted here).  The tool-level refusal shape reuses
the Bash tool's own ``_wrap_command`` (see test_bash_fix.py).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

import tools.environments.bash_fix as bash_fix

from tools.environments.shell_compat import bash_core as _shell_compat


def _fix(command: str):
    return _shell_compat.fix_bash_command(command)


def _fix_app_layer(command: str):
    with patch("tools.environments.bash_fix.sys.platform", "win32"):
        return bash_fix.fix_bash_command(command)


# ── refused (recorded in unsupported, byte-for-byte) ──────────────────────────
REFUSE_CASES = [
    ("goto :end", "goto"),
    ("goto end", "goto"),
    ("goto :eof", "goto"),
    (":label", "goto"),                      # glued label line
    ("echo a; goto :end", "goto"),           # jump around other commands
    (":start\necho hi\ngoto :start", "goto"),  # multi-line batch pasted
    ("call :sub", "call :label"),
    ("call a.BAT", "call :label"),           # case-insensitive .bat
    ("call x.cmd", "call :label"),
    ("call \"some path\\a.bat\"", "call :label"),
    ("exit /b", "exit /b"),
    ("exit /B 2", "exit /b"),
    ("exit /b 0", "exit /b"),
]


@pytest.mark.parametrize(("command", "key"), REFUSE_CASES)
def test_cmd_jumps_are_refused(command: str, key: str) -> None:
    result = _fix(command)
    assert result.command == command, "refused commands stay byte-for-byte"
    assert key in result.unsupported
    assert not result.changed  # refusals are not rewrites (rule of the channel)
    # the warning names the reason (it is what _wrap_command surfaces)
    assert key in result.warning
    assert _UNSUPPORTED_BODIES_HAS(key), key


def _UNSUPPORTED_BODIES_HAS(key: str) -> bool:
    return key in _shell_compat._UNSUPPORTED_BODIES


def test_mixed_errorlevel_and_exit_b_refused() -> None:
    r"""\u00a71 row 27: header rewrites AND the inner ``exit /b`` refuses the run.

    The cmd-if header ``%errorlevel% neq 0`` is translated, but the branch
    body ``exit /b 1`` records an unsupported jump, so the Bash tool refuses
    the whole invocation before spawning -- the rewrite is never executed.
    """


    src = "if %errorlevel% neq 0 (exit /b 1)"
    result = _fix(src)
    assert "exit /b" in result.unsupported
    assert "if (( $? != 0 ))" in result.command  # header did rewrite
    # The tool seam that turns `unsupported` into a surfaced warning lives in
    # tests/tools/test_bash_fix.py::TestWrapCommandBashFix.


@pytest.mark.parametrize(("command", "key"), REFUSE_CASES)
def test_app_layer_forwards_unsupported(command: str, key: str) -> None:
    """The app-layer re-export carries the refusal key (tool turns it into an error)."""
    result = _fix_app_layer(command)
    assert key in result.unsupported
    assert result.command == command


# ── refused even when nested in an executable context ─────────────────────────
def test_goto_inside_command_substitution_refused() -> None:
    result = _fix("x=$(goto :end)")
    assert "goto" in result.unsupported
    assert result.command == "x=$(goto :end)"


def test_exit_slash_b_in_bash_c_refused() -> None:
    result = _fix("bash -c 'echo a; exit /b 1'")
    assert "exit /b" in result.unsupported


# ── NOT refused: valid bash that merely shares a word ──────────────────────────
ALLOWED_CASES = [
    "exit",            # plain exit is valid bash (visible user intent)
    "exit 3",
    "exit $n",
    ":",               # null command
    ": label",         # null command with argument
    ": this is a comment",
    "call prog.exe",   # bare call to a real exe: plain 127, not refused
    "call foo",
    "call",            # call with nothing: not refused
    "echo goto",       # ``goto`` as an argument, not a command word
    "echo :label",
    "rg foo :bar",     # ``:bar`` as an argument
    "echo 'goto :x'",  # quoted data
    "for f in a; do echo $f; done",  # real bash loop
    "set -- 1 2",      # valid bash set (positional params)
]


@pytest.mark.parametrize("command", ALLOWED_CASES)
def test_valid_bash_never_refused(command: str) -> None:
    result = _fix(command)
    assert result.unsupported == (), command
    assert result.command == command or result.changed


def test_exit_slash_b_and_plain_exit_distinguished() -> None:
    assert _fix("exit /b").unsupported == ("exit /b",)
    assert _fix("exit 3").unsupported == ()


# ── tool-level refusal shape (_wrap_command turns unsupported -> ToolError) ──
