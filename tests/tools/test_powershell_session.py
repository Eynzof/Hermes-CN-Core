"""Tests for the reusable PowerShell session (P-042).

Two layers:

* Pure-logic unit tests (``combine_commands``, ``PSResult``) run on any host.
* Live-session tests spawn a real interpreter and are skipped when PowerShell
  is unavailable (i.e. everywhere but Windows).
"""

from __future__ import annotations

import shutil
import sys
import time

import pytest

from tools.environments.powershell_session import (
    PSResult,
    PowerShellSession,
    combine_commands,
)

# --------------------------------------------------------------------------
# Pure-logic unit tests (cross-platform)
# --------------------------------------------------------------------------


class TestCombineCommands:
    def test_joins_with_semicolon(self):
        assert combine_commands(["a", "b", "c"]) == "a; b; c"

    def test_drops_blank_and_whitespace_entries(self):
        assert combine_commands(["a", "", "  ", "b"]) == "a; b"

    def test_custom_separator(self):
        assert combine_commands(["a", "b"], separator=" && ") == "a && b"

    def test_empty(self):
        assert combine_commands([]) == ""


class TestPSResult:
    def test_success_true(self):
        assert PSResult("out", 0).success is True

    def test_nonzero_is_failure(self):
        assert PSResult("out", 1).success is False

    def test_timed_out_is_failure(self):
        assert PSResult("", 124, timed_out=True).success is False

    def test_interrupted_is_failure(self):
        assert PSResult("", 130, interrupted=True).success is False

    def test_session_died_is_failure(self):
        assert PSResult("", 1, session_died=True).success is False

    # --- error_count (N3 fix) ---

    def test_error_count_defaults_to_zero(self):
        assert PSResult("out", 0).error_count == 0

    def test_has_non_terminating_errors_true(self):
        assert PSResult("out", 0, error_count=3).has_non_terminating_errors is True

    def test_has_non_terminating_errors_false_when_zero(self):
        assert PSResult("out", 0, error_count=0).has_non_terminating_errors is False

    def test_has_non_terminating_errors_with_exit_code_0(self):
        # A cmdlet can exit 0 but still have $Error.Count > 0
        r = PSResult("out", 0, error_count=2)
        assert r.success is True  # exit code 0 means success
        assert r.has_non_terminating_errors is True  # but errors accumulated


# --------------------------------------------------------------------------
# Live-session tests (Windows / PowerShell only)
# --------------------------------------------------------------------------

_PS_PATH = None
if sys.platform == "win32":
    _PS_PATH = shutil.which("pwsh.exe") or shutil.which("powershell.exe")

live = pytest.mark.skipif(
    not _PS_PATH, reason="requires Windows PowerShell / pwsh"
)


@pytest.fixture
def ps_session(tmp_path):
    session = PowerShellSession(shell_path=_PS_PATH, cwd=str(tmp_path))
    session.start()
    yield session
    session.close()


@live
class TestLiveSession:
    def test_basic_output_and_exit_code(self, ps_session):
        r = ps_session.run("Write-Output 'hello world'")
        assert r.output.strip() == "hello world"
        assert r.returncode == 0
        assert r.success

    def test_state_persists_across_commands(self, ps_session):
        ps_session.run("$myvar = 4242")
        r = ps_session.run('Write-Output "val=$myvar"')
        assert r.output.strip() == "val=4242"

    def test_external_exit_code_captured(self, ps_session):
        r = ps_session.run("cmd /c exit 7")
        assert r.returncode == 7

    def test_cmdlet_only_command_reports_zero(self, ps_session):
        # Spawn parity: after a failing external command, a subsequent
        # cmdlet-only command must NOT inherit the stale exit code.
        ps_session.run("cmd /c exit 9")
        r = ps_session.run("Write-Output ok")
        assert r.returncode == 0

    def test_bad_command_does_not_wedge_session(self, ps_session):
        ps_session.run("This-Is-Not-A-Real-Cmdlet-XYZ")
        # Session is still usable afterwards.
        r = ps_session.run("Write-Output recovered")
        assert r.output.strip() == "recovered"
        assert ps_session.is_alive()

    def test_unicode_roundtrip(self, ps_session):
        r = ps_session.run("Write-Output 'caf\u00e9 \u4f60\u597d'")
        assert "caf\u00e9" in r.output
        assert "\u4f60\u597d" in r.output

    def test_cwd_tracking(self, ps_session):
        ps_session.run("Set-Location C:\\Windows")
        assert ps_session.cwd.lower() == "c:\\windows"

    def test_timeout_recovers(self, ps_session):
        r = ps_session.run("Start-Sleep -Seconds 30", timeout=1.0)
        assert r.timed_out
        assert r.returncode == 124
        # Session was restarted and is usable again.
        r2 = ps_session.run("Write-Output alive")
        assert r2.output.strip() == "alive"
        assert ps_session.is_alive()

    def test_dead_session_respawns_on_next_call(self, ps_session):
        # `exit` kills the interpreter; the next call must transparently respawn.
        ps_session.run("exit")
        r = ps_session.run("Write-Output back")
        assert r.output.strip() == "back"
        assert ps_session.is_alive()

    def test_context_manager(self, tmp_path):
        with PowerShellSession(shell_path=_PS_PATH, cwd=str(tmp_path)) as s:
            assert s.run("Write-Output ctx").output.strip() == "ctx"
        assert not s.is_alive()

    def test_error_count_tracked_in_marker(self, ps_session):
        # A cmdlet that produces a non-terminating error should have error_count > 0
        r = ps_session.run("Get-ChildItem -Path 'C:/IDontExist_XYZ_123' -ErrorAction SilentlyContinue")
        # The command should succeed (exit 0) but accumulate an error
        assert r.returncode == 0
        # The marker should have tracked $Error.Count
        assert r.error_count > 0, f"expected >0, got {r.error_count}"


@live
class TestSessionReusePerformance:
    def test_powershell_session_reuse(self, ps_session):
        """Warm commands reuse the interpreter and are far cheaper than a spawn.

        Behavior contract (not a brittle snapshot): the average warm command is
        much faster than a single fresh ``powershell.exe`` spawn, and well under
        a generous absolute ceiling.
        """
        import subprocess

        # Warm up the session, then time a batch of warm commands.
        ps_session.run("Write-Output warmup")
        n = 10
        start = time.perf_counter()
        for _ in range(n):
            r = ps_session.run("Write-Output hi")
            assert r.returncode == 0
        warm_avg_ms = (time.perf_counter() - start) / n * 1000

        # Baseline: one real fresh spawn (what the old path paid EVERY call).
        spawn_start = time.perf_counter()
        subprocess.run(
            [_PS_PATH, "-NoProfile", "-NonInteractive", "-Command", "Write-Output hi"],
            capture_output=True,
            text=True,
            timeout=30,
        )
        spawn_ms = (time.perf_counter() - spawn_start) * 1000

        print(
            f"\n  warm avg: {warm_avg_ms:.2f}ms  |  fresh spawn: {spawn_ms:.1f}ms"
        )
        assert warm_avg_ms < spawn_ms, (
            f"warm reuse ({warm_avg_ms:.2f}ms) should beat a fresh spawn "
            f"({spawn_ms:.1f}ms)"
        )
        # Absolute sanity ceiling — generous to stay non-flaky on loaded CI.
        assert warm_avg_ms < 60, f"warm avg unexpectedly high: {warm_avg_ms:.2f}ms"


@live
class TestCommandBatching:
    def test_command_batching(self, ps_session):
        """Batched commands (sequential + combined) produce correct output."""
        # Sequential batch — each reuses the warm session.
        results = ps_session.run_batch(
            ["Write-Output A", "Write-Output B", "Write-Output C"]
        )
        assert [r.output.strip() for r in results] == ["A", "B", "C"]
        assert all(r.returncode == 0 for r in results)

        # Combined batch — one round-trip, output preserves order.
        combined = ps_session.run_combined(["Write-Output X", "Write-Output Y"])
        assert combined.output.split() == ["X", "Y"]
        assert combined.returncode == 0

    def test_batch_preserves_state_between_commands(self, ps_session):
        results = ps_session.run_batch(
            ["$counter = 10", "$counter += 5", 'Write-Output "c=$counter"']
        )
        assert results[-1].output.strip() == "c=15"
