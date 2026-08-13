"""Tests for the non-raising ``_find_bash(raise_if_missing=False)`` probe.

The default/auto Windows shell resolution (git-bash → pwsh → powershell)
probes for git-bash without raising, so a missing/broken bash falls through
to PowerShell instead of failing the resolver.  Explicit callers
(``HERMES_SHELL_TYPE=bash``) keep the legacy helpful ``RuntimeError`` via
the default ``raise_if_missing=True``.

These tests mock ``shutil.which`` / the smoke test / ASLR state so they run
identically on any host.
"""

import os
from unittest import mock

import pytest

from tools.environments.local import _find_bash

LOCAL = "tools.environments.local"


def _env_without_bash(program_files: str = r"C:\nonexistent-pf"):
    """Environment with no discoverable bash on any standard path."""
    return {
        "ProgramFiles": program_files,
        "ProgramFiles(x86)": r"C:\nonexistent-pf-x86",
        "LOCALAPPDATA": r"C:\nonexistent-lappdata",
    }


class TestFindBashOptionalProbe:
    def test_optional_returns_none_when_no_candidates(self, monkeypatch):
        """No candidate at all → optional probe returns None (no raise)."""
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        with mock.patch.dict(os.environ, _env_without_bash(), clear=True), \
             mock.patch("shutil.which", return_value=None):
            assert _find_bash(raise_if_missing=False) is None

    def test_default_still_raises_when_no_candidates(self, monkeypatch):
        """Legacy contract: default call raises the helpful error."""
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        with mock.patch.dict(os.environ, _env_without_bash(), clear=True), \
             mock.patch("shutil.which", return_value=None):
            with pytest.raises(RuntimeError, match="Git Bash is not found"):
                _find_bash()

    def test_optional_returns_none_on_aslr_failure(self, monkeypatch, tmp_path):
        """Candidates exist but fail the smoke test under ASLR → None (no raise)."""
        fake_bash = tmp_path / "bash.exe"
        fake_bash.write_text("")
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        monkeypatch.setattr(f"{LOCAL}._bash_starts", lambda bash: False)
        monkeypatch.setattr(f"{LOCAL}._mandatory_aslr_enabled", lambda: True)
        env = {**os.environ, "HERMES_GIT_BASH_PATH": str(fake_bash)}
        with mock.patch.dict(os.environ, env, clear=False):
            assert _find_bash(raise_if_missing=False) is None

    def test_default_raises_aslr_help_on_aslr_failure(self, monkeypatch, tmp_path):
        """Default call surfaces the targeted ASLR remediation error."""
        fake_bash = tmp_path / "bash.exe"
        fake_bash.write_text("")
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        monkeypatch.setattr(f"{LOCAL}._bash_starts", lambda bash: False)
        monkeypatch.setattr(f"{LOCAL}._mandatory_aslr_enabled", lambda: True)
        env = {**os.environ, "HERMES_GIT_BASH_PATH": str(fake_bash)}
        with mock.patch.dict(os.environ, env, clear=False):
            with pytest.raises(RuntimeError, match="ForceRelocateImages"):
                _find_bash()

    def test_optional_returns_none_when_smoke_fails_outside_aslr(self, monkeypatch, tmp_path):
        """Smoke failure outside the ASLR class: default keeps the legacy
        first-candidate return, the optional probe must NOT select a broken
        bash and returns None so the resolver falls back to PowerShell."""
        fake_bash = tmp_path / "bash.exe"
        fake_bash.write_text("")
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        monkeypatch.setattr(f"{LOCAL}._bash_starts", lambda bash: False)
        monkeypatch.setattr(f"{LOCAL}._mandatory_aslr_enabled", lambda: False)
        monkeypatch.setattr(f"{LOCAL}._looks_like_msys_spawn_failure", lambda details: False)
        env = {**os.environ, "HERMES_GIT_BASH_PATH": str(fake_bash)}
        with mock.patch.dict(os.environ, env, clear=False):
            # legacy: underlying-launch-error is preserved by returning the candidate
            assert _find_bash() == str(fake_bash)
            # optional probe: broken bash is never selected
            assert _find_bash(raise_if_missing=False) is None

    def test_optional_returns_path_when_working(self, monkeypatch, tmp_path):
        """A candidate that passes the smoke test is returned in both modes."""
        fake_bash = tmp_path / "bash.exe"
        fake_bash.write_text("")
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", True)
        monkeypatch.setattr(f"{LOCAL}._bash_starts", lambda bash: True)
        env = {**os.environ, "HERMES_GIT_BASH_PATH": str(fake_bash)}
        with mock.patch.dict(os.environ, env, clear=False):
            assert _find_bash(raise_if_missing=False) == str(fake_bash)
            assert _find_bash() == str(fake_bash)

    def test_optional_non_windows_unchanged(self, monkeypatch, tmp_path):
        """Off Windows the probe is a no-op: both modes return _find_bash_posix()."""
        bash_exe = tmp_path / "bash"
        bash_exe.write_text("")
        monkeypatch.setattr(f"{LOCAL}._IS_WINDOWS", False)
        with mock.patch("shutil.which", return_value=str(bash_exe)):
            assert _find_bash(raise_if_missing=False) == str(bash_exe)
            assert _find_bash() == str(bash_exe)
