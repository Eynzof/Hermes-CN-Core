"""Tests for the Windows Git Bash compatibility fixer (bash_fix).

``tools.environments.bash_fix.fix_bash_command`` rewrites verified native
POSIX command words (``open``, ``pbcopy``, ``rev``, ``gtimeout``, ...) to
fallbacks that work under Git Bash on Windows, and normalizes Windows
backslash paths for the shell.  On non-Windows hosts it is a byte-for-byte
no-op; the Windows-specific behavior is tested by patching ``sys.platform``.
"""
from __future__ import annotations


from unittest.mock import MagicMock, patch

import pytest

from tools.environments.bash_fix import BashFix, bash_compatibility_prelude, fix_bash_command


def _fix_for_platform(command: str, platform: str) -> BashFix:
    import tools.environments.bash_fix as bf

    with patch.object(bf.sys, "platform", platform):
        return fix_bash_command(command)


def _fix_for_windows(command: str) -> BashFix:
    return _fix_for_platform(command, "win32")


# ============================================================================
# Result API
# ============================================================================

class TestBashFixResult:
    def test_result_is_immutable(self) -> None:
        result = BashFix("echo ok")
        with pytest.raises((AttributeError, TypeError)):
            result.command = "echo changed"  # type: ignore[misc]

    def test_unchanged_result(self) -> None:
        result = _fix_for_windows("echo ok")
        assert result == BashFix("echo ok")
        assert result.command == "echo ok"
        assert result.replacements == ()
        assert result.path_changes == ()
        assert result.warning == ""
        assert not result.changed

    def test_changed_result_reports_every_command(self) -> None:
        result = _fix_for_windows("gtimeout 1 true; printf x | rev")
        assert result.replacements == ("gtimeout", "rev")
        assert result.changed
        assert "gtimeout" in result.warning
        assert "rev" in result.warning

    @pytest.mark.parametrize("command", ["", " ", "\t\n", "echo ok\n"])
    def test_empty_and_plain_inputs_round_trip(self, command: str) -> None:
        assert _fix_for_windows(command).command == command


# ============================================================================
# Non-Windows is a byte-for-byte no-op
# ============================================================================

class TestNonWindowsNoop:
    @pytest.mark.parametrize("platform", ["linux", "darwin", "freebsd", "cygwin"])
    @pytest.mark.parametrize(
        "command",
        [
            "gtimeout 1 true",
            "printf abc | rev",
            "xdg-open .",
            "open README.md",
            "printf text | pbcopy",
            "pbpaste",
            "wget https://example.com/f.zip",
            "xclip -selection clipboard",
            "xsel -bo",
            "gsed -n 1p file",
            "zip -r out.zip dir",
            "nc -z example.com 80",
            "pgrep bash",
            "tree -L 1 dir",
            "say hello",
            "wl-copy < file",
            "python3 --version",
            "copy src dst",
            "tasklist",
            "taskkill /PID 123 /F",
            "watch date",
            "killall name",
            "column -t file",
            "netcat -z example.com 80",
            "echo D:\\repo\\src",
            "bash cd /c/dev/x && rev",
            "sh -c 'rev <<< abc'",
            "echo hi > nul",
            "cat /tmp/x",
            "/c/Windows",
            "timeout 5 rev",
            "xargs -a file.txt rev",
        ],
    )
    def test_non_windows_is_byte_for_byte_noop(self, platform: str, command: str) -> None:
        assert _fix_for_platform(command, platform) == BashFix(command)


# ============================================================================
# Verified fallback replacements (Windows Git Bash)
# ============================================================================

class TestBashFixFallbacks:
    @pytest.mark.parametrize(
        "source,replacement",
        [
            ("gtimeout 3 echo ok", "gtimeout"),
            ("printf 'abc' | rev", "rev"),
            ("xdg-open README.md", "xdg-open"),
            ("open https://example.com", "open"),
            ("printf text | pbcopy", "pbcopy"),
            ("pbpaste > clipboard.txt", "pbpaste"),
            ("wget https://example.com/f.zip", "wget"),
            ("printf text | xclip -selection clipboard", "xclip"),
            ("xsel --clipboard", "xsel"),
            ("gsed -n 1p file", "gsed"),
            ("zip -r out.zip dir", "zip"),
            ("nc -z example.com 80", "nc"),
            ("pgrep bash", "pgrep"),
            ("pkill bash", "pkill"),
            ("tree -L 1 dir", "tree"),
            ("say hello", "say"),
            ("printf text | wl-copy", "wl-copy"),
            ("python3 --version", "python3"),
            ("pip3 list", "pip3"),

            ("copy src.txt dst.txt", "copy"),
            ("move src.txt dst.txt", "move"),
            ("del temp.txt", "del"),
            ("erase temp.txt", "erase"),
            ("ren old.txt new.txt", "ren"),
            ("rename old.txt new.txt", "rename"),
            ("rd emptydir", "rd"),
            ("md newdir", "md"),
            ("chdir subdir", "chdir"),
            ("cls", "cls"),
            ("xcopy srcdir dstdir", "xcopy"),
            ("mklink link.txt target.txt", "mklink"),
            ("findstr pattern file.txt", "findstr"),
            ("fc a.txt b.txt", "fc"),
            ("where cmd", "where"),
            ("tasklist", "tasklist"),
            ("taskkill /PID 1234 /F", "taskkill"),
            ("taskkill /IM notepad.exe", "taskkill"),
            ("systeminfo", "systeminfo"),
            ("watch -n 1 date", "watch"),
            ("killall notepad", "killall"),
            ("pidof notepad", "pidof"),

            ("column -t file", "column"),
            ("netcat -z example.com 80", "netcat"),
        ],
    )
    def test_known_fallback_command_replaced(self, source: str, replacement: str) -> None:
        result = _fix_for_windows(source)
        assert replacement in result.replacements, result
        assert result.changed
        # The fallback function definition is prepended and the original
        # command line survives below it.
        assert result.command.endswith("\n" + source), result.command[-200:]

    def test_open_falls_back_to_start(self) -> None:
        result = _fix_for_windows("open README.md")
        assert 'start "$@"' in result.command

    def test_pbpaste_falls_back_to_get_clipboard(self) -> None:
        result = _fix_for_windows("pbpaste")
        assert "Get-Clipboard" in result.command

    def test_rev_falls_back_to_perl_reverse(self) -> None:
        result = _fix_for_windows("printf abc | rev")
        assert "reverse" in result.command

    def test_wget_falls_back_to_curl(self) -> None:
        result = _fix_for_windows("wget https://example.com/f.zip")
        assert "curl" in result.command

    def test_python3_aliases_to_python(self) -> None:
        result = _fix_for_windows("python3 --version")
        assert 'python "$@"' in result.command

    def test_git_bash_bundled_commands_are_not_rewritten(self) -> None:
        for command in (
            "timeout 1 true",
            "stdbuf -oL echo ok",
            "mktemp",
            "truncate -s 0 file",
            "readlink file",
            "realpath file",
            "stat file",
            "sed -n 1p file",
            "grep value file",
            "find . -name '*.py'",
            "xargs echo",
            "tac file",
            "numfmt 1000",
            "nproc",
            "getconf PATH",
        ):
            assert _fix_for_windows(command) == BashFix(command), command

    def test_commands_without_faithful_mapping_are_preserved(self) -> None:
        for command in (
            "setsid app",
            "flock lockfile app",
            "script transcript.txt",
            "getent passwd",
            "ip address",
            "ss -ltn",
            "lsof file",
            "free -h",
            "systemctl status service",
            "service app status",
            "apt update",
            "apt-get update",
        ):
            assert _fix_for_windows(command) == BashFix(command), command

    def test_netcat_aliases_nc_fallback(self) -> None:
        result = _fix_for_windows("netcat -z example.com 80")
        assert "/dev/tcp" in result.command

    def test_column_falls_back_to_perl(self) -> None:
        result = _fix_for_windows("column -t file")
        assert "perl" in result.command

    def test_tasklist_falls_back_to_get_process(self) -> None:
        result = _fix_for_windows("tasklist")
        assert "Get-Process" in result.command

    def test_taskkill_falls_back_to_stop_process(self) -> None:
        result = _fix_for_windows("taskkill /PID 1234 /F")
        assert "Stop-Process" in result.command

    def test_killall_falls_back_to_stop_process(self) -> None:
        result = _fix_for_windows("killall notepad")
        assert "Stop-Process" in result.command

    def test_watch_falls_back_to_loop(self) -> None:
        result = _fix_for_windows("watch date")
        assert "sleep" in result.command

    def test_mklink_falls_back_to_ln(self) -> None:
        result = _fix_for_windows("mklink link.txt target.txt")
        assert "ln -s" in result.command


# ============================================================================
# Heredoc trailing control operators are moved to the redirection line
# ============================================================================

class TestBashFixHeredocTrailingOperators:
    @pytest.mark.parametrize(
        "source,expected",
        [
            (
                "cat <<EOF\nhi\nEOF\n&& echo done\n",
                "cat <<EOF && echo done\nhi\nEOF\n",
            ),
            (
                "cat <<'EOF'\nhi $HOME\nEOF\n|| echo failed\n",
                "cat <<'EOF' || echo failed\nhi $HOME\nEOF\n",
            ),
            (
                "cat <<-EOF\n\thi\n\tEOF\n;\necho after\n",
                "cat <<-EOF ; echo after\n\thi\n\tEOF\n",
            ),
            (
                "cat <<EOF\nhi\nEOF\n| wc -l\n",
                "cat <<EOF | wc -l\nhi\nEOF\n",
            ),
        ],
    )
    def test_operator_moved_to_redirection_line(
        self, source: str, expected: str
    ) -> None:
        result = _fix_for_windows(source)
        assert result.command == expected

    @pytest.mark.parametrize(
        "source",
        [
            # No trailing operator: untouched.
            "cat <<EOF\nhi\nEOF\necho after\n",
            # Unterminated heredoc: untouched.
            "cat <<EOF\nhi\n",
            # ``&>`` is a redirection, not a list terminator: untouched.
            "cat <<EOF\nhi\nEOF\n&> log\n",
            # Operator already on the redirection line: untouched.
            "cat <<EOF && echo done\nhi\nEOF\n",
        ],
    )
    def test_without_trailing_operator_unchanged(self, source: str) -> None:
        assert _fix_for_windows(source) == BashFix(source)


# ============================================================================
# Shell-aware scanning: only executable command words are rewritten
# ============================================================================

class TestBashFixCommandPositions:
    # Plain command positions: the fallback definition is prepended and the
    # original line survives verbatim below it.
    @pytest.mark.parametrize(
        "source",
        [
            "rev",
            "'rev' <<< abc",
            '"rev" <<< abc',
            r"\rev <<< abc",
            'r""ev <<< abc',
            "  rev  ",
            "true; rev",
            "true && rev",
            "false || rev",
            "printf x | rev",
            "rev & wait",
            "echo first\nrev",
            "(rev)",
            "{ rev; }",
            "! rev",
            "if rev; then echo yes; fi",
            "while rev; do break; done",
            "until rev; do break; done",
            "for x in one; do rev; done",
            "result=$(rev)",
            "echo $(rev)",
            "echo `rev`",
            'echo "$(rev)"',
            "diff <(rev) file",
            "cat >(rev)",
            "FOO=bar rev",
            "FOO=bar BAR=baz rev",
            ">output rev",
            "2>/dev/null rev",
            "time rev",
        ],
    )
    def test_rewrites_only_executable_command_words(self, source: str) -> None:
        result = _fix_for_windows(source)
        assert result.replacements == ("rev",), result
        assert result.changed
        assert result.command.endswith("\n" + source), result.command[-200:]

    # Executable wrappers rewrite the command word inline to a self-contained
    # ``/usr/bin/bash -c 'definitions; rev "$@"' --`` runner (fallback
    # functions cannot be reached through ``command``/``env``/``exec``).
    @pytest.mark.parametrize(
        "source",
        [
            "command rev",
            "command -- rev",
            "env rev",
            "env -i rev",
            "env FOO=bar rev",
            "nohup rev",
            "exec rev",
        ],
    )
    def test_executable_wrapper_uses_inline_runner(self, source: str) -> None:
        result = _fix_for_windows(source)
        assert result.replacements == ("rev",), result
        assert result.changed
        assert "/usr/bin/bash -c" in result.command, result.command[-200:]
        assert not result.command.endswith("\n" + source), result.command[-200:]

    def test_quoted_data_is_not_a_command(self) -> None:
        # rev as data (argument / quoted) must NOT be replaced.
        result = _fix_for_windows('echo "rev"')
        assert result == BashFix('echo "rev"')
        result = _fix_for_windows("echo 'rev'")
        assert result == BashFix("echo 'rev'")
        result = _fix_for_windows("touch rev")
        assert result == BashFix("touch rev")

    def test_heredoc_body_is_not_scanned_as_commands(self) -> None:
        source = "cat <<'EOF'\nopen me\nrev me\nEOF"
        result = _fix_for_windows(source)
        assert result == BashFix(source)

    def test_comment_content_is_not_scanned(self) -> None:
        source = "# open this file\nrev"  # trailing rev IS a command
        result = _fix_for_windows(source)
        assert result.replacements == ("rev",)

        source2 = "echo ok # open me\n"
        assert _fix_for_windows(source2) == BashFix(source2)


# ============================================================================
# Windows path normalization
# ============================================================================

class TestBashFixWindowsPaths:
    def test_drive_path_rewritten(self) -> None:
        result = _fix_for_windows(r"cat D:\repo\src\file.txt")
        assert result.path_changes
        assert "D:/repo/src/file.txt" in result.command

    def test_cd_d_flag_dropped(self) -> None:
        result = _fix_for_windows(r"cd /d D:\work")
        assert "cd  D:/work" in result.command.replace("cd /d", "cd")
        assert "cd /d" not in result.command

    def test_unc_path_rewritten(self) -> None:
        result = _fix_for_windows(r"dir \\server\share\dir")
        assert "//server/share/dir" in result.command

    def test_quoted_paths_are_data_and_left_alone(self) -> None:
        source = r"echo 'D:\x\y' \"C:\z\""
        result = _fix_for_windows(source)
        assert result.path_changes == ()
        assert result.command == source


# ============================================================================
# Git Bash virtual absolute path rewriting
# ============================================================================

class TestBashFixVirtualPaths:
    """Git Bash virtual absolute paths get native Windows spellings.

    ``/tmp/x`` maps to the user's Windows temp directory and ``/c/x`` to
    ``C:/x``; native Windows executables cannot resolve the virtual
    spellings.  Assertions derive the temp dir at runtime so they hold on
    any machine.
    """

    @staticmethod
    def _win_temp() -> str:
        import tempfile

        return tempfile.gettempdir().replace("\\", "/")

    def test_tmp_path_rewritten_to_windows_temp(self) -> None:
        result = _fix_for_windows("cat /tmp/out.txt")
        assert result.path_changes == ("/tmp/out.txt",)
        assert f"{self._win_temp()}/out.txt" in result.command

    def test_exact_tmp_rewritten(self) -> None:
        result = _fix_for_windows("ls /tmp")
        assert result.path_changes == ("/tmp",)
        assert result.command.endswith(self._win_temp())

    def test_tmp_lookalike_not_rewritten(self) -> None:
        source = "ls /tmpfoo"
        assert _fix_for_windows(source) == BashFix(source)

    def test_drive_mount_rewritten(self) -> None:
        result = _fix_for_windows("cat /c/Windows/system.ini")
        assert result.path_changes == ("/c/Windows/system.ini",)
        assert "C:/Windows/system.ini" in result.command
        result = _fix_for_windows("ls /d/work")
        assert "D:/work" in result.command

    def test_bare_drive_mount_left_alone(self) -> None:
        # A single-letter mount must never be mistaken for the cmd.exe
        # ``cd /d`` flag's drive path.
        source = "cd /c"
        assert _fix_for_windows(source) == BashFix(source)

    def test_command_word_virtual_path_rewritten(self) -> None:
        result = _fix_for_windows("/c/tools/rg.exe *.txt")
        assert result.path_changes == ("/c/tools/rg.exe",)
        assert "C:/tools/rg.exe *.txt" in result.command

    def test_quoted_virtual_path_is_data_and_left_alone(self) -> None:
        source = "cat '/tmp/x' \"/c/y\""
        result = _fix_for_windows(source)
        assert result.path_changes == ()
        assert result.command == source


# ============================================================================
# nul redirection target rewriting
# ============================================================================

class TestBashFixNulRedirection:
    """Unquoted output-redirection targets ``nul``/``NUL`` become /dev/null.

    Git Bash treats ``nul`` as an ordinary filename and would silently
    create an empty file literally named ``nul``; quoted spellings are
    intentional filenames and input redirections never create a file.
    """

    def test_output_redirect_rewritten(self) -> None:
        result = _fix_for_windows("echo hi > nul")
        assert result.nul_fixes == ("nul",)
        assert result.command == "echo hi > /dev/null"
        assert result.changed

    def test_fd_prefixed_and_uppercase_rewritten(self) -> None:
        result = _fix_for_windows("cmd 2> NUL")
        assert result.nul_fixes == ("NUL",)
        assert result.command == "cmd 2> /dev/null"

    def test_append_redirect_rewritten(self) -> None:
        result = _fix_for_windows("cmd >> nul")
        assert result.command == "cmd >> /dev/null"

    def test_input_redirect_left_alone(self) -> None:
        source = "cmd < nul"
        assert _fix_for_windows(source) == BashFix(source)

    def test_quoted_nul_is_an_intentional_filename(self) -> None:
        source = "echo hi > 'nul'"
        assert _fix_for_windows(source) == BashFix(source)

    def test_plain_argument_left_alone(self) -> None:
        source = "echo nul"
        assert _fix_for_windows(source) == BashFix(source)

    def test_warning_mentions_dev_null(self) -> None:
        result = _fix_for_windows("echo hi > nul")
        assert "/dev/null" in result.warning
        assert "`nul`" in result.warning


# ============================================================================
# Redundant shell wrapper removal
# ============================================================================

class TestBashFixShellWrappers:
    """Leading ``bash``/``sh``/``dash``/``ash`` invocations are unwrapped.

    The Bash tool already runs the command via bash, so a redundant shell
    word (``bash cd /c/dev/x && ...``) or inline-script form
    (``bash -c '...'``) is removed — except where bash would open a
    script file, semantics would change, or an outer command wrapper
    provides the executable context.
    """

    def test_prefix_form_unwrapped(self) -> None:
        result = _fix_for_windows("bash cd /c/dev/x && grep foo t.txt")
        assert result.shell_wrappers == ("bash",)
        assert result.command == "cd C:/dev/x && grep foo t.txt"

    @pytest.mark.parametrize("shell", ["sh", "dash", "ash"])
    def test_other_posix_shells_unwrapped(self, shell: str) -> None:
        result = _fix_for_windows(f"{shell} pwd")
        assert result.shell_wrappers == (shell,)
        assert result.command == "pwd"

    def test_c_form_unwrapped_and_inner_scanned(self) -> None:
        result = _fix_for_windows(r"bash -c 'cd C:\x && rev'")
        assert result.shell_wrappers == ("bash -c",)
        assert result.replacements == ("rev",)
        assert result.path_changes == (r"C:\x",)
        assert "export -f rev" in result.command
        assert result.command.endswith("cd C:/x && rev")

    def test_login_cluster_c_form_unwrapped(self) -> None:
        result = _fix_for_windows("bash -lc 'rev <<< abc'")
        assert result.shell_wrappers == ("bash -c",)
        assert result.replacements == ("rev",)
        assert result.command.endswith("rev <<< abc")

    def test_script_invocations_kept(self) -> None:
        for source in (
            "bash script.sh",
            "bash ./deploy.sh",
            "bash ../tools/check.sh",
            "bash sub/dir/tool",
        ):
            assert _fix_for_windows(source) == BashFix(source), source

    def test_semantic_options_kept(self) -> None:
        for source in ("bash -e echo hi", "bash --norc echo hi"):
            assert _fix_for_windows(source) == BashFix(source), source

    def test_trailing_script_argv_kept(self) -> None:
        source = "bash -c 'echo hi' arg0"
        assert _fix_for_windows(source) == BashFix(source)

    def test_assignment_prefix_kept(self) -> None:
        source = "VAR=x bash -c 'echo $VAR'"
        assert _fix_for_windows(source) == BashFix(source)

    def test_bare_shell_kept(self) -> None:
        for source in ("bash", "bash && echo hi"):
            assert _fix_for_windows(source) == BashFix(source), source

    def test_under_command_wrapper_c_form_kept_and_exported(self) -> None:
        # ``env`` execs argv and cannot call shell functions, so the
        # ``bash -c`` wrapper stays; the fallback is exported for the
        # nested shell to inherit.
        result = _fix_for_windows("env bash -c 'rev <<< abc'")
        assert result.shell_wrappers == ()
        assert result.replacements == ("rev",)
        assert "env bash -c" in result.command
        assert "export -f rev" in result.command

    def test_under_command_wrapper_prefix_form_drops_shell_word(self) -> None:
        result = _fix_for_windows("nohup bash cd /c/dev/x")
        assert result.shell_wrappers == ("bash",)
        assert result.command == "nohup cd C:/dev/x"

    def test_warning_mentions_removed_wrapper(self) -> None:
        result = _fix_for_windows("bash cd /c/dev/x")
        assert "Removed redundant shell wrapper" in result.warning
        assert "`bash`" in result.warning


# ============================================================================
# Command wrappers (timeout/stdbuf/nice/xargs, gtimeout/watch fallbacks)
# ============================================================================

class TestBashFixCommandWrappers:
    """Wrapper commands keep their wrapped command word scannable.

    ``timeout`` consumes its DURATION operand before the command word;
    exec'ing wrappers (``timeout``/``xargs``/...) receive the standalone
    runner because they cannot invoke shell functions; same-shell
    wrappers (``watch``) resolve the word against the prefix functions.
    """

    def test_timeout_duration_operand_consumed(self) -> None:
        result = _fix_for_windows("timeout 5 rev")
        assert result.replacements == ("rev",)
        assert "timeout 5 /usr/bin/bash -c" in result.command

    def test_timeout_option_and_duration_consumed(self) -> None:
        result = _fix_for_windows("timeout -s KILL 5s rev")
        assert result.replacements == ("rev",)
        assert "timeout -s KILL 5s /usr/bin/bash -c" in result.command

    def test_stdbuf_and_nice_wrappers(self) -> None:
        result = _fix_for_windows("stdbuf -oL rev")
        assert "stdbuf -oL /usr/bin/bash -c" in result.command
        result = _fix_for_windows("nice -n 5 rev")
        assert "nice -n 5 /usr/bin/bash -c" in result.command

    def test_xargs_path_option_value(self) -> None:
        result = _fix_for_windows("xargs -a file.txt rev")
        assert result.replacements == ("rev",)
        assert "xargs -a file.txt /usr/bin/bash -c" in result.command
        result = _fix_for_windows(r"xargs --arg-file=D:\f.txt rev")
        assert "--arg-file=D:/f.txt" in result.command
        assert result.path_changes == (r"--arg-file=D:\f.txt",)

    def test_gtimeout_fallback_wrapper(self) -> None:
        result = _fix_for_windows("gtimeout 5 rev")
        assert result.replacements == ("gtimeout", "rev")
        assert "export -f gtimeout" in result.command
        assert "gtimeout 5 /usr/bin/bash -c" in result.command

    def test_watch_quoted_operand_scanned(self) -> None:
        result = _fix_for_windows("watch -n1 'rev <<< abc'")
        assert result.replacements == ("watch", "rev")
        assert "export -f watch" in result.command
        assert "export -f rev" in result.command
        assert result.command.endswith("watch -n1 'rev <<< abc'")

    def test_watch_fallback_body_evals_in_current_shell(self) -> None:
        result = _fix_for_windows("watch date")
        assert result.replacements == ("watch",)
        assert 'eval "$*"' in result.command

    def test_unquoted_watch_operand_resolves_against_functions(self) -> None:
        # No runner rewrite: watch is a same-shell wrapper, so the bare
        # word keeps resolving to the exported fallback function.
        result = _fix_for_windows("watch rev")
        assert result.replacements == ("watch", "rev")
        assert result.command.endswith("watch rev")


# ============================================================================
# bash_compatibility_prelude
# ============================================================================

class TestBashCompatibilityPrelude:
    def test_win32_returns_definitions(self) -> None:
        import tools.environments.bash_fix as bf

        with patch.object(bf.sys, "platform", "win32"):
            prelude = bash_compatibility_prelude()
        assert "open()" in prelude
        assert "export -f" in prelude

    def test_non_windows_returns_empty(self) -> None:
        import tools.environments.bash_fix as bf

        with patch.object(bf.sys, "platform", "linux"):
            assert bash_compatibility_prelude() == ""


# ============================================================================
# LocalEnvironment raw-command coverage
# ============================================================================

class _FakeProc:
    """Stand-in for subprocess.Popen with a real pid attribute."""

    pid = 424242

    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class TestWrapCommandBashFix:
    """_wrap_command covers the RAW command with bash_fix on Windows Git Bash.

    The base bash wrapper embeds the command inside ``eval '<escaped>'`` (a
    single-quoted region), which the bash_fix scanner treats as data — so the
    fix MUST run on the raw command before wrapping, mirroring kimix
    ``bash_tool._prepare_command``.  A wrapper-level scan would be a silent
    no-op (the original integration bug).
    """

    def _wrap(self, command: str, shell_type: str = "bash"):
        import tools.environments.bash_fix as bf

        from tools.environments import local as local_mod
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        env._shell_type = shell_type
        with patch.object(bf.sys, "platform", "win32"), \
             patch.object(
                 LocalEnvironment,
                 "_wrap_command_powershell",
                 return_value="<pwsh wrapper>",
             ):
            wrapped = env._wrap_command(command, r"C:\tmp")
        return env, wrapped

    def test_bash_fix_covers_raw_command(self) -> None:
        env, wrapped = self._wrap("open README.md")
        # The `open` fallback definition lands inside the wrapper (it was
        # embedded as the raw command before the eval quoting).
        assert 'start "$@"' in wrapped
        # The warning is recorded on the environment for LLM surfacing.
        assert env._bash_fix_warnings
        assert "open" in env._bash_fix_warnings

    def test_windows_path_rewritten_in_raw_command(self) -> None:
        env, wrapped = self._wrap(r"echo D:\repo\src")
        assert "D:/repo/src" in wrapped
        assert env._bash_fix_warnings
        assert "D:\\repo\\src" in env._bash_fix_warnings

    def test_unchanged_command_no_warning(self) -> None:
        env, wrapped = self._wrap("echo ok")
        # With P-058's git-bash-first Windows default the resolved shell path is
        # a real Git Bash, so the P-052 MSYSTEM neutralization prepends
        # ``export MSYSTEM=;`` inside the eval.  Assert the command still lands
        # un-fixed in the eval region (the bash_fix contract), not the exact
        # eval spelling.
        assert "eval '" in wrapped and "echo ok" in wrapped
        assert getattr(env, "_bash_fix_warnings", None) is None

    def test_powershell_shell_skips_bash_fix(self) -> None:
        # PowerShell commands are handled by _wrap_command_powershell and must
        # never pass through the bash fixer (backslashes are native there).
        env, wrapped = self._wrap("Write-Output 'C:\\x'", shell_type="powershell")
        assert wrapped == "<pwsh wrapper>"
        assert getattr(env, "_bash_fix_warnings", None) is None

class TestRunBashPassthrough:
    """_run_bash no longer applies bash_fix — the raw command is already
    covered by _wrap_command before it is embedded in the wrapper."""

    def _run(self, command: str, *, login: bool = False):
        import tools.environments.bash_fix as bf

        from tools.environments import local as local_mod
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=r"C:\tmp", timeout=30)
        # Force the bash path: on Windows the default shell type resolves to
        # pwsh, and _run_bash would dispatch to _run_powershell instead.
        env._shell_type = "bash"
        with patch.object(bf.sys, "platform", "win32"), \
             patch.object(local_mod, "_find_bash_posix", return_value="/usr/bin/bash"), \
             patch.object(local_mod, "_prepare_bash_cmd", side_effect=lambda c: c), \
             patch.object(local_mod, "_resolve_shell_init_files", return_value=[]), \
             patch.object(local_mod, "_resolve_safe_cwd", side_effect=lambda c: c), \
             patch.object(local_mod, "_make_run_env", return_value={}), \
             patch.object(local_mod.subprocess, "Popen", return_value=_FakeProc()) as popen_mock:
            env._run_bash(command, login=login)
        return env, popen_mock

    def test_command_passed_verbatim_without_fix(self) -> None:
        env, popen = self._run("open README.md")

        args = popen.call_args.args[0]
        assert args[1] == "-c", args
        # No fallback definitions prepended here — the fix ran in _wrap_command
        # on the raw command before the wrapper was built.
        assert args[2] == "open README.md"
        assert getattr(env, "_bash_fix_warnings", None) is None

    def test_login_probe_runs_verbatim(self) -> None:
        # login=True is used by init_session's env snapshot probe; it must stay
        # byte-for-byte as prepared so the snapshot captures no fallback fns.
        env, popen = self._run("open README.md", login=True)

        args = popen.call_args.args[0]
        assert args[1] == "-l", args
        cmd = args[args.index("-c") + 1]
        assert "open README.md" in cmd
        assert 'start "$@"' not in cmd
        assert getattr(env, "_bash_fix_warnings", None) is None


# ============================================================================
# terminal_tool surfaces bash_fix_warnings in JSON
# ============================================================================

class TestTerminalToolSurfacesBashFixWarnings:
    """terminal_tool() includes bash_fix_warnings in its JSON when the
    underlying execute() result carries them, mirroring pwsh_warnings."""

    def _run_with_fake_env_result(self, exec_result, tmp_path, task_id):
        import orjson

        import tools.terminal_tool as tt

        fake_env = MagicMock()
        fake_env.cwd = str(tmp_path)
        fake_env.env = {}
        fake_env.execute.return_value = exec_result

        with patch.object(tt, "_create_environment", return_value=fake_env), \
             patch.dict(tt._active_environments, {}, clear=True):
            raw = tt.terminal_tool("echo hi", task_id=task_id)
        return orjson.loads(raw)

    def test_json_includes_bash_fix_warnings(self, tmp_path):
        parsed = self._run_with_fake_env_result(
            {
                "output": "hello world",
                "returncode": 0,
                "bash_fix_warnings": (
                    "Added Windows Git Bash fallback(s) for native "
                    "command(s): `open`."
                ),
            },
            tmp_path,
            task_id="bash-fix-present",
        )
        assert "open" in parsed["bash_fix_warnings"]

    def test_no_warnings_key_when_absent(self, tmp_path):
        parsed = self._run_with_fake_env_result(
            {"output": "hello world", "returncode": 0},
            tmp_path,
            task_id="bash-fix-absent",
        )
        assert "bash_fix_warnings" not in parsed
