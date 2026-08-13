"""Tests for the PowerShell-aware quoting validator/repairer (pwsh_fix).

``tools.environments.pwsh_fix.fix_pwsh_command`` validates a command under
real PowerShell quoting rules and repairs it where possible (unclosed
strings / here-strings / comments get their closing token appended, a
trailing line comment / ``--%`` marker / dangling continuation gets a
newline so the ``try{...}catch{...}`` wrapper in
``tools.environments.local._wrap_command_powershell`` is not swallowed).

Sections:

    A. valid commands that a naive double-quote parity check would reject;
    B. genuinely unbalanced commands that get repaired;
    C. unrepairable commands (empty, dangling continuation backtick, bare
       ``--%``) -> ``None``;
    D. wrapper-safety cases (trailing comment / ``--%``) -> newline appended;
    E. already-valid commands -> returned unchanged;
    F. LocalEnvironment wrapper integration.

Classes that execute against a real pwsh are skipped when pwsh is missing.
"""
from __future__ import annotations


import shutil
import subprocess
import sys
from unittest.mock import patch

import pytest

from tools.environments.pwsh_fix import PwshFix, fix_pwsh_command

PWSH = shutil.which("pwsh")
NEEDS_PWSH = pytest.mark.skipif(PWSH is None, reason="pwsh is not installed")


def _run_pwsh(cmd: str, timeout: int = 30) -> tuple[int, str]:
    """Run *cmd* via real pwsh; return (exit_code, stdout+stderr)."""
    r = subprocess.run(
        [PWSH, "-NoP", "-NonI", "-C", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return r.returncode, (r.stdout + r.stderr).strip()


# ============================================================================
# C. Irreparable input -> None
# ============================================================================

class TestFixPwshCommandIrreparable:
    @pytest.mark.parametrize(
        "cmd",
        [
            "",
            "   ",
            "\t\n ",
            "`",                       # dangling line-continuation backtick
            "Write-Output `",
            "Get-ChildItem `",
            "--%",                     # --% with no command before it
            "--% foo",
        ],
    )
    def test_returns_none(self, cmd: str) -> None:
        assert fix_pwsh_command(cmd) is None


# ============================================================================
# A. Valid commands that a naive parity check would reject (odd `"` count)
# ============================================================================

NAIVE_REJECTED: list[str] = [
    # double quote inside a single-quoted string
    'Write-Output \'a"b\'',
    # backtick-escaped quote inside a double-quoted string
    'Write-Output "a`"b"',
    # lone backtick-escaped quote at the top level
    "Write-Output `\"",
    # single-quoted strings with '' escapes and a double quote inside
    'Write-Output \'can\'t " do\'',
    # double-quoted here-string containing a lone double quote
    '$x = @"\nline " quote\n"@\nWrite-Output $x',
    # single-quoted here-string containing a double quote
    "$x = @'\nline \" quote\n'@\nWrite-Output $x",
    # quotes inside a line comment
    '# comment " quote\nWrite-Output ok',
    'Write-Output ok # "',
    # quote inside a block comment
    '<# comment " quote #>\nWrite-Output ok',
    # quote after the --% stop-parsing marker (rest of line is literal)
    'cmd /c echo --% "hello world',
    # string concatenation with a double quote inside single quotes
    'Write-Output ("a" + \'b"\')',
    # double-quoted string containing a backtick-escaped quote only
    'Write-Output "`""',
]

NAIVE_REJECTED_RUN_CLEAN: list[tuple[str, str]] = [
    ('Write-Output \'a"b\'', 'a"b'),
    ('Write-Output "a`"b"', 'a"b'),
    ('Write-Output \'can\'\'t " do\'', "can't \" do"),
    ('$x = @"\nline " quote\n"@\nWrite-Output $x', 'line " quote'),
    ("$x = @'\nline \" quote\n'@\nWrite-Output $x", 'line " quote'),
    ('# comment " quote\nWrite-Output ok', "ok"),
    ('<# comment " quote #>\nWrite-Output ok', "ok"),
    pytest.param(
        'cmd /c echo --% "hello world',
        '"hello world',
        marks=pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is Windows-only"),
    ),
    ('Write-Output ("a" + \'b"\')', 'ab"'),
]

TRICKY_RUN_CLEAN: list[tuple[str, str]] = [
    ('Write-Output \'He said "hi"\'', 'He said "hi"'),
    ('Write-Output "a""b"', 'a"b'),                  # doubled-quote escape
    ("Write-Output `\"hi`\"", '"hi"'),
    ('$x = \'it\'\'s "fine"\'; Write-Output $x', 'it\'s "fine"'),
    ('Write-Output "a$( "b" )c"', "abc"),            # $(...) sub-expression
    ('Write-Output "`"hello`""', '"hello"'),
]


class TestFixPwshCommandValid:
    @pytest.mark.parametrize("cmd", NAIVE_REJECTED)
    def test_valid_commands_never_return_none(self, cmd: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None, f"parser rejected valid command: {cmd!r}"
        assert isinstance(fix, PwshFix)

    @NEEDS_PWSH
    @pytest.mark.parametrize("cmd,expected", NAIVE_REJECTED_RUN_CLEAN)
    def test_naive_rejected_fixed_and_runs_clean(self, cmd: str, expected: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None, f"parser rejected valid command: {cmd!r}"
        rc, out = _run_pwsh(fix.command)
        assert rc == 0, f"fixed command failed: {fix.command!r} -> rc={rc} out={out!r}"
        assert expected in out, f"unexpected output for {fix.command!r}: {out!r}"

    @NEEDS_PWSH
    @pytest.mark.parametrize("cmd,expected", TRICKY_RUN_CLEAN)
    def test_tricky_quoting_still_runs_clean(self, cmd: str, expected: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None, f"parser rejected valid command: {cmd!r}"
        rc, out = _run_pwsh(fix.command)
        assert rc == 0, f"fixed command failed: {fix.command!r} -> rc={rc} out={out!r}"
        assert expected in out, f"unexpected output for {fix.command!r}: {out!r}"


# ============================================================================
# B. Genuinely unbalanced commands -> repaired by the parser
# ============================================================================

REPAIR_CASES: list[tuple[str, str]] = [
    # unclosed double-quoted string
    ('Write-Output "hello', 'Write-Output "hello"'),
    ('Write-Output "a" "b', 'Write-Output "a" "b"'),
    ('Write-Output "" "', 'Write-Output "" ""'),
    # unclosed single-quoted string
    ("Write-Output 'hello", "Write-Output 'hello'"),
    ("'it''s", "'it''s'"),
    # double-quoted string containing an unclosed single quote
    ('"a\'b', '"a\'b"'),
    # unclosed double-quoted string ending in a backtick: the backtick would
    # escape a single appended quote, so two quotes are appended
    ('Write-Output "a`', 'Write-Output "a`""'),
    # even run of trailing backticks: a single quote is enough
    ('"a``', '"a``"'),
    # unclosed here-strings
    ('@"\nunclosed here-string', '@"\nunclosed here-string\n"@'),
    ("@'\nunclosed here-string", "@'\nunclosed here-string\n'@"),
    # unclosed block comment
    ("Write-Output ok <# unclosed comment", "Write-Output ok <# unclosed comment#>"),
]


class TestFixPwshCommandRepairsUnbalanced:
    @pytest.mark.parametrize("cmd,fixed", REPAIR_CASES)
    def test_repairs_unbalanced(self, cmd: str, fixed: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None, f"parser could not repair: {cmd!r}"
        assert fix.command == fixed, (
            f"unexpected repair: expected {fixed!r}, got {fix.command!r}"
        )
        assert fix.warning, "repair should carry a warning"
        assert fix.changed

    @NEEDS_PWSH
    @pytest.mark.parametrize("cmd,fixed", REPAIR_CASES)
    def test_repaired_command_runs(self, cmd: str, fixed: str) -> None:
        rc0, _ = _run_pwsh(cmd)
        assert rc0 != 0, f"original command should fail: {cmd!r}"
        fix = fix_pwsh_command(cmd)
        assert fix is not None
        rc1, out = _run_pwsh(fix.command)
        assert rc1 == 0, f"repaired command failed: {fix.command!r} -> {out!r}"

    def test_repair_warning_messages_are_meaningful(self) -> None:
        assert "double-quoted" in fix_pwsh_command('Write-Output "x').warning
        assert "single-quoted" in fix_pwsh_command("Write-Output 'x").warning
        assert "here-string" in fix_pwsh_command('@"\nx').warning
        assert "block comment" in fix_pwsh_command("x <# c").warning


# ============================================================================
# D. Wrapper-safety: trailing line comment / --% marker need a trailing newline
# ============================================================================

class TestFixPwshCommandWrapperSafety:
    @pytest.mark.parametrize(
        "cmd,expected_suffix",
        [
            ("Write-Output ok # done", "\n"),
            ("cmd /c echo --% foo", "\n"),
            ('Write-Output ok # " done', "\n"),
            # comment-only commands need a real statement for the wrapper
            ("# just a comment", "\n$null"),
            ("<# just a comment", "#>\n$null"),
        ],
    )
    def test_trailing_comment_or_stop_parsing_appends_newline(
        self, cmd: str, expected_suffix: str
    ) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None
        assert fix.changed, f"expected a modification for {cmd!r}"
        assert fix.command == cmd + expected_suffix
        assert "newline" in fix.warning

    @NEEDS_PWSH
    @pytest.mark.parametrize(
        "cmd,expected_out",
        [
            ("Write-Output ok # done", "ok"),
            pytest.param(
                "cmd /c echo --% foo",
                "foo",
                marks=pytest.mark.skipif(sys.platform != "win32", reason="cmd.exe is Windows-only"),
            ),
            ('Write-Output ok # " done', "ok"),
        ],
    )
    def test_wrapped_command_runs_clean(self, cmd: str, expected_out: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None and fix.changed
        # Simulate the local wrapper shape: try{<cmd>}catch{...}
        wrapped = "try{" + fix.command + "}catch{$_|Out-String|Write-Error;exit 1}"
        rc, out = _run_pwsh(wrapped)
        assert rc == 0, f"wrapped command failed: {wrapped!r} -> {out!r}"
        assert expected_out in out

    @pytest.mark.parametrize(
        "cmd,expected_suffix",
        [
            # continuation into nothing
            ("Write-Output `\n", "\n"),
            # continuation target is the last line
            ("Get-ChildItem `\n-Filter *.ps1", "\n"),
            # continuation + unclosed double-quoted string (repair + newline)
            ('Write-Output `\n"hello', '"\n'),
        ],
    )
    def test_trailing_continuation_appends_newline(
        self, cmd: str, expected_suffix: str
    ) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None
        assert fix.changed, f"expected a modification for {cmd!r}"
        assert fix.command == cmd + expected_suffix
        assert "continuation" in fix.warning


# ============================================================================
# E. Already-valid commands -> unchanged
# ============================================================================

class TestFixPwshCommandUnchanged:
    @pytest.mark.parametrize(
        "cmd",
        [
            "Get-Location",
            "1 + 2",
            'Write-Output "hi"',
            "Write-Output 'a\"b'",        # valid, even though the quote count is odd
            "git status; cargo test",
            'Write-Output "a""b"',
            'Write-Output "a$( "b" )c"',
            '$x = @"\nhi\n"@',
            'Write-Output "a`"b"',
            "$x = 5 # trailing comment\nWrite-Output $x",
            "Write-Output (Get-Date -Format 'yyyy')",
            "if ($true) { Write-Output 'ok' }",
        ],
    )
    def test_unchanged_no_warning(self, cmd: str) -> None:
        fix = fix_pwsh_command(cmd)
        assert fix is not None
        assert fix.command == cmd
        assert fix.warning == ""
        assert not fix.changed


# ============================================================================
# Parser corner cases (unit level, no pwsh needed)
# ============================================================================

class TestFixPwshCommandParserCorners:
    def test_here_string_opener_not_recognized_when_glued_to_word(self) -> None:
        fix = fix_pwsh_command('$x = a@"\nhello\n"@')
        assert fix is not None
        assert fix.command == '$x = a@"\nhello\n"@'

    def test_hash_glued_to_word_is_not_a_comment(self) -> None:
        fix = fix_pwsh_command('Write-Output foo#c "x"')
        assert fix is not None
        assert fix.command == 'Write-Output foo#c "x"'

    def test_block_comment_does_not_nest(self) -> None:
        fix = fix_pwsh_command('<# one <# two #> three #>\nWrite-Output ok')
        assert fix is not None
        assert fix.command == '<# one <# two #> three #>\nWrite-Output ok'

    def test_multi_here_strings(self) -> None:
        cmd = '$a = @"\nx\n"@\n$b = @\'\ny\n\'@\nWrite-Output $a$b'
        fix = fix_pwsh_command(cmd)
        assert fix is not None
        assert fix.command == cmd
        assert not fix.changed

    def test_stop_parsing_in_middle_ignores_quotes_after(self) -> None:
        fix = fix_pwsh_command('cmd /c echo --% "a" b\nWrite-Output ok')
        assert fix is not None
        assert fix.command == 'cmd /c echo --% "a" b\nWrite-Output ok'

    def test_crlf_line_endings_in_here_string(self) -> None:
        fix = fix_pwsh_command('@"\r\ncontent " q\r\n"@')
        assert fix is not None
        assert not fix.changed

    def test_pwsh_fix_dataclass_api(self) -> None:
        f = fix_pwsh_command('Write-Output "x')
        assert isinstance(f, PwshFix)
        assert f.command == 'Write-Output "x"'
        assert f.changed is True
        assert fix_pwsh_command("Get-Location").changed is False

    def test_fast_path_returns_plain_command_unchanged(self) -> None:
        # Commands without quote/comment/continuation/here-string/--% chars
        # skip the scanner entirely (performance fast path).
        for cmd in ("Get-Location", "git status; cargo test", "   Get-Date   "):
            fix = fix_pwsh_command(cmd)
            assert fix is not None
            assert fix.command == cmd
            assert not fix.changed

    @pytest.mark.parametrize(
        "cmd",
        [
            '"',
            '""',
            '"""',
            '"\'"',
            "''",
            "'''",
            "<#",
            "#>",
            "--%",
            '"$(',
            "'$( \"x\" )",
            '"a`"',
            "'a`b'",
            'Write-Output "a$( "b',
            '"a\'b"c',
            "'it''",
            '@"',
            "@'",
            "# comment `",
            'Write-Output "a"; "b',
            "Write-Output 'a' 'b",
            '`" `" `"',
        ],
    )
    def test_never_raises(self, cmd: str) -> None:
        # The parser must never throw on arbitrary input.
        result = fix_pwsh_command(cmd)
        assert result is None or isinstance(result, PwshFix)

    @pytest.mark.parametrize(
        "cmd",
        [
            "$(" * 5000 + ")" * 5000,              # deep $( nesting
            '"' + "$(" * 5000 + ")" * 5000 + '"',  # same inside a dq string
            "(" * 100000,                          # pathological paren run
            "a `\n" * 5000,                        # many line continuations
        ],
        ids=["deep-subexpr", "deep-subexpr-in-dq", "paren-run", "many-continuations"],
    )
    def test_deeply_nested_input_never_raises(self, cmd: str) -> None:
        result = fix_pwsh_command(cmd)
        assert result is None or isinstance(result, PwshFix)


# ============================================================================
# F. LocalEnvironment wrapper integration
# ============================================================================

class TestLocalPwshWrapperIntegration:
    """fix_pwsh_command runs inside LocalEnvironment's PowerShell wrappers."""

    def _env(self):
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "powershell"
        return env

    def test_unclosed_quote_repaired_inside_wrapper(self) -> None:
        env = self._env()
        wrapped = env._wrap_command_powershell('Write-Output "hello', r"C:\tmp")

        # The repaired command (closing quote appended) lands in the
        # Invoke-Expression literal, and a repair warning reaches the LLM.
        assert "Invoke-Expression 'Write-Output \"hello\"'" in wrapped
        assert env._pwsh_warnings
        assert "appended a closing `\"`" in env._pwsh_warnings[0]

    def test_unclosed_single_quote_repaired_for_pwsh_too(self) -> None:
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "pwsh"  # transform is skipped, fix still applies
        wrapped = env._wrap_command_powershell("Write-Output 'hello", r"C:\tmp")

        assert "Invoke-Expression 'Write-Output ''hello'''" in wrapped
        assert env._pwsh_warnings
        assert "appended a closing `'`" in env._pwsh_warnings[0]

    def test_unrepairable_command_warns_but_runs_as_is(self) -> None:
        env = self._env()
        wrapped = env._wrap_command_powershell("Write-Output `", r"C:\tmp")

        # Dangling continuation backtick cannot be repaired; the command stays
        # inside the wrapper's error guard with an explanatory warning.
        assert "Invoke-Expression 'Write-Output `'" in wrapped
        assert env._pwsh_warnings
        assert "could not be validated" in env._pwsh_warnings[0]

    def test_balanced_command_adds_no_fix_warning(self) -> None:
        env = self._env()
        env._wrap_command_powershell('Write-Output "hi"', r"C:\tmp")
        # Balanced quoting -> no fix warning; transform produced none either.
        assert env._pwsh_warnings == []

    def test_trailing_comment_gets_newline_before_wrapper(self) -> None:
        env = self._env()
        wrapped = env._wrap_command_powershell("Write-Output ok # done", r"C:\tmp")

        # The trailing comment is terminated so the wrapper's catch block and
        # closing quote are never swallowed.
        assert "Invoke-Expression 'Write-Output ok # done\n'" in wrapped
        assert env._pwsh_warnings
        assert "newline" in env._pwsh_warnings[0]

    def test_session_wrapper_applies_fix(self) -> None:
        env = self._env()
        body = env._wrap_command_powershell_session('Write-Output "hello', r"C:\tmp")
        assert "Invoke-Expression 'Write-Output \"hello\"'" in body
        assert env._pwsh_warnings

    def test_background_script_applies_fix(self) -> None:
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command='Write-Output "hello',
            cwd=r"C:\tmp",
            shell_type="powershell",
        )
        assert "Invoke-Expression 'Write-Output \"hello\"'" in script

    def test_fix_runs_before_transform(self) -> None:
        """fix_pwsh_command must run on the RAW command BEFORE pwsh_transform."""
        from tools.environments import local as local_mod
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "powershell"
        calls: list[str] = []

        def fake_fix(cmd: str) -> PwshFix:
            calls.append("fix")
            return PwshFix(cmd, "")

        def fake_transform(cmd: str) -> tuple[str, list[str]]:
            calls.append("transform")
            return cmd, []

        with patch.object(local_mod, "fix_pwsh_command", side_effect=fake_fix), \
             patch.object(local_mod, "pwsh_transform", side_effect=fake_transform):
            env._wrap_command_powershell("git status", r"C:\tmp")

        assert calls == ["fix", "transform"]

    def test_fix_and_transform_warnings_both_recorded(self) -> None:
        from tools.environments import local as local_mod
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "powershell"
        with patch.object(
            local_mod,
            "fix_pwsh_command",
            return_value=PwshFix('Write-Output "x"', "fix warning"),
        ), patch.object(
            local_mod,
            "pwsh_transform",
            return_value=("Write-Output x", ["transform warning"]),
        ):
            env._wrap_command_powershell('Write-Output "x', r"C:\tmp")

        # Fix warnings first, then transform warnings — both reach the LLM.
        assert env._pwsh_warnings == ["fix warning", "transform warning"]

    def test_session_wrapper_fix_runs_before_transform(self) -> None:
        from tools.environments import local as local_mod
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = "powershell"
        calls: list[str] = []

        def fake_fix(cmd: str) -> PwshFix:
            calls.append("fix")
            return PwshFix(cmd, "")

        def fake_transform(cmd: str) -> tuple[str, list[str]]:
            calls.append("transform")
            return cmd, []

        with patch.object(local_mod, "fix_pwsh_command", side_effect=fake_fix), \
             patch.object(local_mod, "pwsh_transform", side_effect=fake_transform):
            env._wrap_command_powershell_session("git status", r"C:\tmp")

        assert calls == ["fix", "transform"]

    def test_background_script_fix_runs_before_transform(self) -> None:
        from tools.environments import local as local_mod

        calls: list[str] = []

        def fake_fix(cmd: str) -> PwshFix:
            calls.append("fix")
            return PwshFix(cmd, "")

        def fake_transform(cmd: str) -> tuple[str, list[str]]:
            calls.append("transform")
            return cmd, []

        with patch.object(local_mod, "fix_pwsh_command", side_effect=fake_fix), \
             patch.object(local_mod, "pwsh_transform", side_effect=fake_transform):
            local_mod._build_powershell_background_script(
                command="echo hi",
                cwd=r"C:\tmp",
                shell_type="powershell",
            )

        assert calls == ["fix", "transform"]
