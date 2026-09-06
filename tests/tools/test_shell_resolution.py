"""Tests for the refactored shell-resolution logic in ``tools/environments/local.py``.

Covers ``_find_bash_posix()``, ``_find_powershell()``, and ``_resolve_shell()``
after the migration to PowerShell as the default shell on Windows.
Git Bash is still available as an optional explicit shell (``shell:bash``).
"""

import contextlib
import os
from pathlib import Path
from unittest import mock

import pytest

from tools.environments.local import (
    _find_bash,
    _find_bash_posix,
    _find_powershell,
    _find_pwsh,
    _is_windows_apps_stub,
    _is_wsl_bash_launcher,
    _resolve_shell,
)


@contextlib.contextmanager
def _whitelist_fs(*allowed_paths):
    """Make ``os.path.isfile`` and ``Path.exists`` truthy only for *allowed_paths*."""
    allowed = {os.path.normcase(str(p)) for p in allowed_paths}

    def _isfile(path):
        return os.path.normcase(str(path)) in allowed

    def _exists(_self):
        return os.path.normcase(str(_self)) in allowed

    with mock.patch("os.path.isfile", _isfile), mock.patch("pathlib.Path.exists", _exists):
        yield


class TestFindBashPosix:
    """_find_bash_posix() finds bash on non-Windows or optionally on Windows (shell:bash)."""

    def test_non_windows_returns_bash_or_fallback(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", False)
        with mock.patch("shutil.which", return_value="/usr/bin/bash"):
            assert _find_bash_posix() == "/usr/bin/bash"

    def test_non_windows_falls_back_to_sensible_defaults(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", False)
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=False):
                with mock.patch.dict(os.environ, {}, clear=True):
                    assert _find_bash_posix() == "/bin/sh"


class TestFindPowershell:
    """_find_powershell() returns powershell.exe on Windows."""

    def test_non_windows_always_returns_powershell_dot_exe(self, monkeypatch):
        """The function doesn't check _IS_WINDOWS — it just calls shutil.which."""
        with mock.patch("shutil.which", return_value=r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"):
            assert _find_powershell() == r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    def test_windows_missing_still_returns_string(self, monkeypatch):
        """When not on PATH, fall back to the literal string 'powershell.exe'."""
        with mock.patch("shutil.which", return_value=None):
            assert _find_powershell() == "powershell.exe"


class TestFindPwsh:
    """_find_pwsh() multi-step detection on Windows."""

    def test_path_search_returns_pwsh(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        with mock.patch("shutil.which", side_effect=lambda x: r"C:\Program Files\PowerShell\7\pwsh.exe" if x in ("pwsh", "pwsh.exe") else None):
            assert _find_pwsh() == r"C:\Program Files\PowerShell\7\pwsh.exe"

    def test_program_files_location(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=True):
                with mock.patch.dict(os.environ, {"ProgramFiles": r"C:\Program Files"}, clear=True):
                    result = _find_pwsh()
                    assert result is not None
                    assert "pwsh.exe" in result

    def test_all_steps_fail_returns_none(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        with mock.patch("shutil.which", return_value=None):
            with mock.patch("os.path.isfile", return_value=False):
                with mock.patch.dict(os.environ, {}, clear=True):
                    assert _find_pwsh() is None

    # --- WindowsApps stub filtering (fix #20/N2) ---

    def test_windowsapps_path_skipped_in_strategy1(self, monkeypatch):
        """Strategy 1 skips WindowsApps paths and falls through."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        windowsapps_path = (
            r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\pwsh.exe"
        )
        real_path = r"C:\Program Files\PowerShell\7\pwsh.exe"

        # shutil.which returns the WindowsApps stub first
        with mock.patch("shutil.which", return_value=windowsapps_path):
            # Strategy 2 (Program Files) has the real binary
            with mock.patch(
                "os.path.isfile",
                side_effect=lambda p: p == real_path,
            ):
                with mock.patch.dict(
                    os.environ,
                    {"ProgramFiles": r"C:\Program Files"},
                    clear=True,
                ):
                    result = _find_pwsh()
                    # Must return the real binary, not the stub
                    assert result == real_path, f"expected {real_path}, got {result}"

    def test_windowsapps_path_is_last_resort_size_check(self, monkeypatch):
        """Strategy 4 validates file size > 10KB to skip stubs."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        windowsapps_path = (
            r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\pwsh.exe"
        )
        with mock.patch("shutil.which", return_value=None):
            # Only the WindowsApps path exists on "disk"
            with mock.patch(
                "os.path.isfile",
                side_effect=lambda p: p == windowsapps_path,
            ):
                with mock.patch(
                    "os.path.getsize", return_value=512
                ):  # stub-sized file
                    with mock.patch.dict(
                        os.environ,
                        {
                            "LOCALAPPDATA": (
                                r"C:\Users\test\AppData\Local"
                            )
                        },
                        clear=True,
                    ):
                        # Too small (< 10KB) -> skip
                        assert _find_pwsh() is None

    def test_windowsapps_path_accepted_when_large_enough(self, monkeypatch):
        """Strategy 4 accepts WindowsApps binaries larger than 10KB."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        windowsapps_path = (
            r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\pwsh.exe"
        )
        with mock.patch("shutil.which", return_value=None):
            # Only the WindowsApps path exists on "disk"
            with mock.patch(
                "os.path.isfile",
                side_effect=lambda p: p == windowsapps_path,
            ):
                with mock.patch(
                    "os.path.getsize", return_value=50000
                ):  # real binary size
                    with mock.patch.dict(
                        os.environ,
                        {
                            "LOCALAPPDATA": (
                                r"C:\Users\test\AppData\Local"
                            )
                        },
                        clear=True,
                    ):
                        result = _find_pwsh()
                        assert result == windowsapps_path


class TestResolveShell:
    """_resolve_shell() on Windows prefers pwsh, falls back to powershell.
    When HERMES_SHELL_TYPE=bash, resolves to pre-installed Git Bash
    (no auto-download); a missing/broken Git Bash falls back to the
    PowerShell chain (pwsh → powershell.exe).
    """

    def test_windows_auto_pwsh_available_returns_pwsh(self, monkeypatch):
        """auto + no git-bash + pwsh available → pwsh (git-bash probed first)."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "auto"}
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "tools.environments.local._find_bash", side_effect=lambda **kw: None
            ), mock.patch(
                "tools.environments.local._find_pwsh", return_value=pwsh_path
            ):
                assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_windows_auto_pwsh_unavailable_fallsback_to_powershell(self, monkeypatch):
        """auto + no git-bash + no pwsh → powershell 5.1."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "auto"}
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "tools.environments.local._find_bash", side_effect=lambda **kw: None
            ), mock.patch(
                "tools.environments.local._find_pwsh", return_value=None
            ), mock.patch(
                "tools.environments.local._find_powershell", return_value=ps_path
            ):
                assert _resolve_shell() == ("powershell", ps_path)

    def test_windows_auto_git_bash_available_returns_bash(self, monkeypatch):
        """auto prefers git-bash over pwsh when a working bash exists."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        bash_path = r"C:\Program Files\Git\bin\bash.exe"
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        env = {"HERMES_SHELL_TYPE": "auto"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "tools.environments.local._find_bash", side_effect=lambda **kw: bash_path
            ), mock.patch(
                "tools.environments.local._find_pwsh", return_value=pwsh_path
            ):
                # bash wins even though pwsh is available
                assert _resolve_shell() == ("bash", bash_path)

    def test_windows_auto_git_bash_missing_uses_pwsh(self, monkeypatch):
        """auto + no git-bash + pwsh available → pwsh."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        env = {"HERMES_SHELL_TYPE": "auto"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "tools.environments.local._find_bash", side_effect=lambda **kw: None
            ), mock.patch(
                "tools.environments.local._find_pwsh", return_value=pwsh_path
            ):
                assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_windows_explicit_powershell(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "powershell"}
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=None):
                with mock.patch("tools.environments.local._find_powershell", return_value=ps_path):
                    assert _resolve_shell() == ("powershell", ps_path)

    def test_windows_explicit_pwsh_returns_pwsh(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "pwsh"}
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=pwsh_path):
                assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_windows_explicit_pwsh_unavailable_fallsback(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "pwsh"}
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=None):
                with mock.patch("tools.environments.local._find_powershell", return_value=ps_path):
                    assert _resolve_shell() == ("powershell", ps_path)

    def test_windows_bash_found_returns_bash(self, monkeypatch):
        """HERMES_SHELL_TYPE=bash returns bash when a usable bash is found."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        bash_path = r"C:\Program Files\Git\bin\bash.exe"
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_bash", return_value=bash_path):
                with mock.patch("tools.environments.local._bash_starts", return_value=True):
                    assert _resolve_shell() == ("bash", bash_path)

    def test_windows_bash_missing_falls_back_to_pwsh(self, monkeypatch):
        """HERMES_SHELL_TYPE=bash with Git Bash missing falls back to pwsh."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch(
                "tools.environments.local._find_bash",
                side_effect=RuntimeError(
                    "Git Bash is not found on this system. "
                    "It was explicitly selected via HERMES_SHELL_TYPE=bash; "
                    "install Git for Windows or use PowerShell."
                ),
            ):
                with mock.patch("tools.environments.local._find_pwsh", return_value=pwsh_path):
                    assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_windows_bash_missing_falls_back_to_powershell(self, monkeypatch):
        """HERMES_SHELL_TYPE=bash with Git Bash missing falls back to powershell.exe."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_bash", return_value=None):
                with mock.patch("tools.environments.local._find_pwsh", return_value=None):
                    with mock.patch("tools.environments.local._find_powershell", return_value=ps_path):
                        assert _resolve_shell() == ("powershell", ps_path)

    def test_windows_bash_broken_falls_back_to_powershell(self, monkeypatch):
        """A probe-failed Git Bash is treated as unavailable and falls back."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        bash_path = r"C:\Program Files\Git\bin\bash.exe"
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_bash", return_value=bash_path):
                with mock.patch("tools.environments.local._bash_starts", return_value=False):
                    with mock.patch("tools.environments.local._find_pwsh", return_value=None):
                        with mock.patch("tools.environments.local._find_powershell", return_value=ps_path):
                            assert _resolve_shell() == ("powershell", ps_path)

    def test_windows_unknown_shell_type_raises(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "cmd"}
        with mock.patch.dict(os.environ, env, clear=True):
            with pytest.raises(RuntimeError, match="Unknown HERMES_SHELL_TYPE"):
                _resolve_shell()

    def test_windows_legacy_pwsh_maps_to_pwsh_when_available(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "pwsh"}
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=pwsh_path):
                assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_non_windows_always_bash(self, monkeypatch, tmp_path):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", False)
        bash_exe = tmp_path / "bash"
        bash_exe.write_text("")
        with mock.patch("shutil.which", return_value=str(bash_exe)):
            with mock.patch.dict(os.environ, {}, clear=True):
                assert _resolve_shell() == ("bash", str(bash_exe))


class TestBuildPowershellBackgroundScript:
    """_build_powershell_background_script produces a runnable PowerShell wrapper."""

    def test_includes_command_cwd_and_cwd_file(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo hello",
            cwd="D:\\test",
            shell_type="pwsh",
            cwd_file="D:/tmp/cwd.txt",
        )
        assert "Invoke-Expression 'echo hello'" in script
        assert "Set-Location -LiteralPath 'D:\\test'" in script
        assert "D:/tmp/cwd.txt" in script
        assert "exit $hermes_ec" in script

    def test_omits_cwd_file_when_not_provided(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo hello",
            cwd="D:\\test",
            shell_type="powershell",
        )
        assert "Out-File" not in script
        assert "exit $hermes_ec" in script

    def test_escapes_single_quotes(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo 'hello'",
            cwd="D:\\test",
            shell_type="pwsh",
        )
        assert "Invoke-Expression 'echo ''hello'''" in script

    # --- try/catch wrapping (fix N1) ---

    def test_try_catch_wraps_invoke_expression(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo hello",
            cwd="D:\test",
            shell_type="pwsh",
        )
        assert "try { Invoke-Expression" in script
        assert "catch {" in script

    # --- $PSNativeCommandArgumentPassing (fix #6) ---

    def test_native_command_argument_passing_set(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo hello",
            cwd="D:\test",
            shell_type="pwsh",
        )
        assert "$PSNativeCommandArgumentPassing = 'Windows'" in script

    # --- $Error.Count check (fix #11/F7) ---

    def test_error_count_check_present(self):
        from tools.environments.local import _build_powershell_background_script

        script = _build_powershell_background_script(
            command="echo hello",
            cwd="D:\test",
            shell_type="pwsh",
        )
        assert "$Error.Count" in script
        assert "$hermes_ec = $LASTEXITCODE" in script


class TestBuildBashBackgroundScript:
    """_build_bash_background_script produces a runnable bash wrapper for the
    git-bash default background path (mirrors the PowerShell wrapper)."""

    def test_includes_cd_command_and_cwd_file(self):
        from tools.environments.local import _build_bash_background_script

        script = _build_bash_background_script(
            command="echo hello",
            cwd=r"D:\test",
            cwd_file="D:/tmp/hermes-cwd.txt",
        )
        # cd is guarded (exit 126 when the directory cannot be entered); the
        # cwd path is MSYS-rewritten on Windows hosts (/d/test) and kept
        # verbatim on POSIX hosts - check the shape, not the exact form.
        assert script.startswith("builtin cd -- ") and " || exit 126" in script
        assert "eval 'echo hello'" in script
        assert "pwd > " in script and "hermes-cwd.txt" in script
        assert "__hermes_ec=$?" in script
        assert "exit $__hermes_ec" in script

    def test_omits_cwd_file_when_not_provided(self):
        from tools.environments.local import _build_bash_background_script

        script = _build_bash_background_script(
            command="echo hello",
            cwd=r"D:\test",
        )
        assert "pwd > " not in script
        assert "exit $__hermes_ec" in script

    def test_escapes_single_quotes(self):
        from tools.environments.local import _build_bash_background_script

        script = _build_bash_background_script(
            command="echo 'hello'",
            cwd=r"D:\test",
        )
        # bash single-quote escaping: ' -> '\'' (produced line:
        #   eval 'echo '\''hello'\'''
        esc = "'\\''"  # the bash escape sequence '\'' (value: quote, backslash, quote, quote)
        assert "eval 'echo " + esc + "hello" + esc + "'" in script

    def test_no_powershell_flags_in_wrapper(self):
        from tools.environments.local import _build_bash_background_script

        script = _build_bash_background_script(
            command="echo hello",
            cwd=r"D:\test",
        )
        assert "-NoProfile" not in script
        assert "Invoke-Expression" not in script


class TestIsWindowsAppsStub:
    """_is_windows_apps_stub() rejects Microsoft Store App Execution Aliases."""

    def test_windowsapps_path_is_stub(self):
        assert (
            _is_windows_apps_stub(
                r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\bash.exe"
            )
            is True
        )

    def test_git_bash_path_is_not_stub(self):
        assert _is_windows_apps_stub(r"C:\Program Files\Git\bin\bash.exe") is False

    def test_forward_slash_windowsapps_is_stub(self):
        assert (
            _is_windows_apps_stub(
                "C:/Users/test/AppData/Local/Microsoft/WindowsApps/bash.exe"
            )
            is True
        )

    def test_empty_path_is_not_stub(self):
        assert _is_windows_apps_stub("") is False


class TestIsWslBashLauncher:
    """_is_wsl_bash_launcher() must reject the Windows WSL bash launcher."""

    def test_system32_bash_is_wsl_launcher(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}, clear=True):
            # Path check alone is enough — no subprocess probe should run.
            with mock.patch(
                "subprocess.run",
                side_effect=AssertionError("path check should short-circuit"),
            ):
                assert _is_wsl_bash_launcher(r"C:\Windows\System32\bash.exe") is True

    def test_syswow64_bash_is_wsl_launcher(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}, clear=True):
            assert _is_wsl_bash_launcher(r"C:\Windows\SysWOW64\bash.exe") is True

    def test_git_bash_is_not_wsl_by_path_and_uname(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        git_bash = r"C:\Program Files\Git\bin\bash.exe"
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}, clear=True):
            with mock.patch(
                "subprocess.run",
                return_value=mock.Mock(stdout="MINGW64_NT-10.0-22631\n", returncode=0),
            ):
                assert _is_wsl_bash_launcher(git_bash) is False

    def test_uname_probe_detects_wsl_outside_system32(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        wsl_shim = r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\bash.exe"
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}, clear=True):
            with mock.patch(
                "subprocess.run",
                return_value=mock.Mock(
                    stdout="Linux 6.18.33.2-microsoft-standard-WSL2\n",
                    returncode=0,
                ),
            ):
                assert _is_wsl_bash_launcher(wsl_shim) is True

    def test_result_is_cached_per_path(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        wsl_bash = r"C:\Windows\System32\bash.exe"
        with mock.patch.dict(os.environ, {"WINDIR": r"C:\Windows"}, clear=True):
            assert _is_wsl_bash_launcher(wsl_bash) is True
            # Second call must hit the cache, not re-probe.
            with mock.patch(
                "subprocess.run",
                side_effect=AssertionError("cache should short-circuit"),
            ):
                assert _is_wsl_bash_launcher(wsl_bash) is True


class TestFindBashWindowsRejectsWsl:
    """_find_bash() on Windows must prefer Git Bash and never return WSL bash."""

    @contextlib.contextmanager
    def _stub_environment(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        monkeypatch.setattr("tools.environments.local._wsl_bash_launcher_cache", {})
        monkeypatch.setattr("tools.environments.local._where_git_executables", lambda: [])
        monkeypatch.setattr("tools.environments.local._bash_starts", lambda _p: True)
        with mock.patch.dict(
            os.environ,
            {
                "ProgramFiles": r"C:\Program Files",
                "ProgramFiles(x86)": r"C:\Program Files (x86)",
                "LOCALAPPDATA": r"C:\Users\test\AppData\Local",
                "HERMES_SHELL_TYPE": "auto",
            },
            clear=True,
        ):
            yield

    def test_wsl_launcher_on_path_is_skipped_for_git_bash(self, monkeypatch):
        with self._stub_environment(monkeypatch):
            wsl_bash = r"C:\Windows\System32\bash.exe"
            git_bash = r"C:\Program Files\Git\bin\bash.exe"

            with mock.patch("shutil.which", return_value=wsl_bash):
                with mock.patch(
                    "os.path.isfile",
                    side_effect=lambda p: os.path.normcase(p) == os.path.normcase(git_bash),
                ):
                    with mock.patch(
                        "tools.environments.local._is_wsl_bash_launcher",
                        side_effect=lambda p: p == wsl_bash,
                    ):
                        assert _find_bash(raise_if_missing=False) == git_bash

    def test_wsl_launcher_alone_falls_back_to_none(self, monkeypatch):
        with self._stub_environment(monkeypatch):
            wsl_bash = r"C:\Windows\System32\bash.exe"

            with mock.patch("shutil.which", return_value=wsl_bash):
                with mock.patch("os.path.isfile", return_value=False):
                    with mock.patch(
                        "tools.environments.local._is_wsl_bash_launcher",
                        return_value=True,
                    ):
                        # No Git Bash exists: WSL must not be selected; auto mode
                        # falls through to PowerShell (None from _find_bash).
                        assert _find_bash(raise_if_missing=False) is None

    def test_windowsapps_stub_on_path_is_skipped_for_git_bash(self, monkeypatch):
        """A WindowsApps App Execution Alias bash.exe is skipped even when the
        WSL check says no (it is a Store stub, not a usable bash)."""
        with self._stub_environment(monkeypatch):
            stub = r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\bash.exe"
            git_bash = r"C:\Program Files\Git\bin\bash.exe"

            with mock.patch("shutil.which", return_value=stub):
                with mock.patch(
                    "os.path.isfile",
                    side_effect=lambda p: os.path.normcase(str(p))
                    == os.path.normcase(git_bash),
                ):
                    with mock.patch(
                        "tools.environments.local._is_wsl_bash_launcher",
                        return_value=False,
                    ), mock.patch(
                        "tools.environments.local._is_windows_apps_stub",
                        side_effect=lambda p: p == stub,
                    ):
                        assert _find_bash(raise_if_missing=False) == git_bash

    def test_windowsapps_stub_alone_falls_back_to_none(self, monkeypatch):
        """A WindowsApps stub is not a bash: auto mode falls through to None
        (PowerShell) instead of selecting or probing the stub."""
        with self._stub_environment(monkeypatch):
            stub = r"C:\Users\test\AppData\Local\Microsoft\WindowsApps\bash.exe"

            with mock.patch("shutil.which", return_value=stub):
                with mock.patch("os.path.isfile", return_value=False):
                    with mock.patch(
                        "tools.environments.local._is_wsl_bash_launcher",
                        return_value=False,
                    ), mock.patch(
                        "tools.environments.local._is_windows_apps_stub",
                        side_effect=lambda p: p == stub,
                    ):
                        assert _find_bash(raise_if_missing=False) is None

    def test_git_exe_chain_beats_path_bash(self, monkeypatch):
        """Mirrors kimix ordering: a Git Bash derived from ``where.exe git``
        wins over a (working, non-WSL) plain ``bash`` found on PATH — PATH is
        the last-resort candidate source."""
        with self._stub_environment(monkeypatch):
            git_bash = r"C:\Users\test\scoop\apps\git\bin\bash.exe"
            path_bash = r"C:\msys64\usr\bin\bash.exe"

            with mock.patch("shutil.which", return_value=path_bash):
                with mock.patch(
                    "os.path.isfile",
                    side_effect=lambda p: os.path.normcase(str(p))
                    == os.path.normcase(git_bash),
                ):
                    with mock.patch(
                        "tools.environments.local._where_git_executables",
                        lambda: [r"C:\Users\test\scoop\apps\git\cmd\git.exe"],
                    ), mock.patch(
                        "tools.environments.local._git_exec_path", lambda git_path: None
                    ), mock.patch(
                        "tools.environments.local._is_wsl_bash_launcher",
                        return_value=False,
                    ), mock.patch(
                        "tools.environments.local._is_windows_apps_stub",
                        return_value=False,
                    ):
                        assert _find_bash(raise_if_missing=False) == git_bash
