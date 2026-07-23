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

from tools.environments.local import _find_bash_posix, _find_powershell, _find_pwsh, _resolve_shell


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
    When HERMES_SHELL_TYPE=bash, resolves to pre-installed Git Bash (no auto-download).
    """

    def test_windows_auto_pwsh_available_returns_pwsh(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "auto"}
        pwsh_path = r"C:\Program Files\PowerShell\7\pwsh.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=pwsh_path):
                assert _resolve_shell() == ("pwsh", pwsh_path)

    def test_windows_auto_pwsh_unavailable_fallsback_to_powershell(self, monkeypatch):
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "auto"}
        ps_path = r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_pwsh", return_value=None):
                with mock.patch("tools.environments.local._find_powershell", return_value=ps_path):
                    assert _resolve_shell() == ("powershell", ps_path)

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
        """HERMES_SHELL_TYPE=bash returns bash when a pre-installed bash is found."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        bash_path = r"C:\Program Files\Git\bin\bash.exe"
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_bash", return_value=bash_path):
                assert _resolve_shell() == ("bash", bash_path)

    def test_windows_bash_not_found_raises_helpful_error(self, monkeypatch):
        """HERMES_SHELL_TYPE=bash raises a helpful error when bash is not installed."""
        monkeypatch.setattr("tools.environments.local._IS_WINDOWS", True)
        env = {"HERMES_SHELL_TYPE": "bash"}
        with mock.patch.dict(os.environ, env, clear=True):
            with mock.patch("tools.environments.local._find_bash", return_value=None):
                with pytest.raises(RuntimeError, match="Git Bash is not found"):
                    _resolve_shell()

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
