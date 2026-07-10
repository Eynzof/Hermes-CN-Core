"""Tests for LocalEnvironment's opt-in persistent PowerShell session (P-042).

The session fast path is Windows + PowerShell only and OFF by default.  These
cover the flag resolver (cross-platform, mocked) and the live execute() path
(Windows only), including output/exit-code/cwd parity with the spawn path and
the stdin fallback.
"""

from __future__ import annotations

import shutil
import sys

import pytest


# --------------------------------------------------------------------------
# Flag resolution (cross-platform via mocking)
# --------------------------------------------------------------------------


class TestSessionReuseFlag:
    def test_disabled_off_windows(self):
        import tools.environments.local as local

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", False)
            assert local._resolve_pwsh_session_reuse("powershell") is False

    def test_disabled_for_bash_shell(self):
        import tools.environments.local as local

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", True)
            assert local._resolve_pwsh_session_reuse("bash") is False

    def test_env_var_enables(self):
        import tools.environments.local as local

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", True)
            mp.setenv("HERMES_PWSH_SESSION_REUSE", "1")
            assert local._resolve_pwsh_session_reuse("powershell") is True
            assert local._resolve_pwsh_session_reuse("pwsh") is True

    def test_env_var_disables_over_config(self):
        import tools.environments.local as local
        import hermes_cli.config as cfg

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", True)
            mp.setenv("HERMES_PWSH_SESSION_REUSE", "0")
            # Even if config said true, the explicit env override wins (and
            # config is not even consulted).
            mp.setattr(
                cfg,
                "load_config",
                lambda: {"terminal": {"powershell_session_reuse": True}},
            )
            assert local._resolve_pwsh_session_reuse("powershell") is False

    def test_config_enables(self):
        import tools.environments.local as local
        import hermes_cli.config as cfg

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", True)
            mp.delenv("HERMES_PWSH_SESSION_REUSE", raising=False)
            mp.setattr(
                cfg,
                "load_config",
                lambda: {"terminal": {"powershell_session_reuse": True}},
            )
            assert local._resolve_pwsh_session_reuse("powershell") is True

    def test_default_off(self):
        import tools.environments.local as local
        import hermes_cli.config as cfg

        with pytest.MonkeyPatch.context() as mp:
            mp.setattr(local, "_IS_WINDOWS", True)
            mp.delenv("HERMES_PWSH_SESSION_REUSE", raising=False)
            mp.setattr(cfg, "load_config", lambda: {"terminal": {}})
            assert local._resolve_pwsh_session_reuse("powershell") is False


# --------------------------------------------------------------------------
# Live execute() via session (Windows / PowerShell only)
# --------------------------------------------------------------------------

_HAS_PS = sys.platform == "win32" and (
    shutil.which("pwsh.exe") or shutil.which("powershell.exe")
)
live = pytest.mark.skipif(not _HAS_PS, reason="requires Windows PowerShell / pwsh")


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n").strip()


@pytest.fixture
def reuse_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PWSH_SESSION_REUSE", "1")
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    assert env._pwsh_session_reuse is True
    yield env
    env.cleanup()


@pytest.fixture
def spawn_env(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_PWSH_SESSION_REUSE", "0")
    from tools.environments.local import LocalEnvironment

    env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
    assert env._pwsh_session_reuse is False
    yield env
    env.cleanup()


@live
class TestLocalSessionExecute:
    def test_basic_execute(self, reuse_env):
        r = reuse_env.execute("Write-Output 'via session'")
        assert r["returncode"] == 0
        assert "via session" in r["output"]

    def test_session_created_and_reused(self, reuse_env):
        reuse_env.execute("Write-Output one")
        assert reuse_env._pwsh_session is not None
        first = id(reuse_env._pwsh_session)
        reuse_env.execute("Write-Output two")
        assert id(reuse_env._pwsh_session) == first  # same interpreter reused

    def test_exit_code(self, reuse_env):
        assert reuse_env.execute("cmd /c exit 4")["returncode"] == 4

    def test_cwd_persists_across_calls(self, reuse_env):
        reuse_env.execute("Set-Location C:\\Windows")
        assert reuse_env.cwd.lower() == "c:\\windows"
        r = reuse_env.execute("(Get-Location).Path")
        assert "windows" in r["output"].lower()

    def test_stdin_falls_back_to_spawn(self, reuse_env):
        # A command with stdin_data can't use the shared session; it must still
        # run correctly via the spawn fallback.
        r = reuse_env.execute("findstr x", timeout=10, stdin_data="xyz\nabc\n")
        assert _norm(r["output"]) == "xyz"

    def test_unicode(self, reuse_env):
        r = reuse_env.execute("Write-Output 'caf\u00e9 \u4f60\u597d'")
        assert "caf\u00e9" in r["output"] and "\u4f60\u597d" in r["output"]

    def test_cleanup_closes_session(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HERMES_PWSH_SESSION_REUSE", "1")
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd=str(tmp_path), timeout=30)
        env.execute("Write-Output hi")
        session = env._pwsh_session
        assert session is not None and session.is_alive()
        env.cleanup()
        assert env._pwsh_session is None
        assert not session.is_alive()


@live
class TestSessionSpawnParity:
    @pytest.mark.parametrize(
        "cmd",
        [
            "Write-Output plain",
            "cmd /c exit 7",
            "$x = 21; Write-Output ($x * 2)",
            "1..3 | ForEach-Object { $_ }",
            "Write-Output 'caf\u00e9 \u4f60\u597d'",
        ],
    )
    def test_output_and_rc_match_spawn(self, cmd, reuse_env, spawn_env):
        r_session = reuse_env.execute(cmd)
        r_spawn = spawn_env.execute(cmd)
        assert r_session["returncode"] == r_spawn["returncode"]
        assert _norm(r_session["output"]) == _norm(r_spawn["output"])
