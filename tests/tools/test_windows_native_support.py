"""Behavioral tests for Windows-specific compatibility fixes.

Complements ``tests/tools/test_windows_compat.py`` (which does source-level
pattern linting) with cross-platform-mocked tests that exercise the actual
code paths Hermes takes on native Windows.

Runs on Linux CI — every test mocks ``sys.platform``, ``subprocess.run``,
and ``os.kill`` as needed to simulate Windows behavior without requiring a
Windows runner.
"""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import sys
from pathlib import Path
from unittest import mock
from unittest.mock import MagicMock

import pytest


# ---------------------------------------------------------------------------
# configure_windows_stdio
# ---------------------------------------------------------------------------


class TestConfigureWindowsStdio:
    """``hermes_cli.stdio.configure_windows_stdio`` wiring.

    The function must:
    - be a no-op on non-Windows
    - only configure once per process (idempotent)
    - set PYTHONIOENCODING / PYTHONUTF8 without overriding explicit user settings
    - reconfigure sys.stdout/stderr/stdin to UTF-8 on Windows
    - flip the console code page to CP_UTF8 (65001) via ctypes
    - respect HERMES_DISABLE_WINDOWS_UTF8 opt-out
    """

    @pytest.fixture(autouse=True)
    def _reset_configured(self, monkeypatch):
        """Reload the module before each test so the _CONFIGURED flag resets."""
        # Remove from sys.modules so import triggers a fresh load
        sys.modules.pop("hermes_cli.stdio", None)
        # Fresh import now; tests import from hermes_cli.stdio themselves,
        # but this guarantees the module they get is a brand-new copy.
        import hermes_cli.stdio as _s
        _s._CONFIGURED = False
        yield
        sys.modules.pop("hermes_cli.stdio", None)

    @pytest.mark.skipif(sys.platform == "win32", reason="POSIX-only test — no-op on Windows")
    def test_no_op_on_posix(self, monkeypatch):
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: False)
        result = stdio.configure_windows_stdio()
        assert result is False

    def test_idempotent(self):
        from hermes_cli import stdio

        stdio.configure_windows_stdio()
        # Second call returns False because _CONFIGURED is set
        assert stdio.configure_windows_stdio() is False


    def test_reconfigure_stream_handles_missing_method(self, monkeypatch):
        """StringIO-like objects without .reconfigure() must not blow up."""
        from hermes_cli import stdio
        import io

        buf = io.StringIO()
        # Must not raise
        stdio._reconfigure_stream(buf)


# ---------------------------------------------------------------------------
# terminate_pid — the centralized kill primitive
# ---------------------------------------------------------------------------


@pytest.mark.windows_only
class TestTerminatePidRoutingOnWindows:
    """``gateway.status.terminate_pid`` must use taskkill /T /F on Windows.

    ``windows_only``: this used to patch the module-level ``_IS_WINDOWS``
    flag on Linux, which selected the taskkill branch on a host where
    ``taskkill`` does not exist and ``gateway/status`` cannot even import its
    ``msvcrt`` branch. On the Windows runner the flag is genuinely True, so
    only ``subprocess.run`` is mocked — the dependency, not the host.
    """

    def test_force_uses_taskkill_on_windows(self, monkeypatch):
        from gateway import status

        captured = {}

        def fake_run(args, **kwargs):
            captured["args"] = args
            result = MagicMock()
            result.returncode = 0
            result.stderr = ""
            result.stdout = ""
            return result

        monkeypatch.setattr(status.subprocess, "run", fake_run)
        status.terminate_pid(12345, force=True)

        assert captured["args"][0] == "taskkill"
        assert "/PID" in captured["args"]
        assert "12345" in captured["args"]
        assert "/T" in captured["args"]
        assert "/F" in captured["args"]

    def test_force_taskkill_failure_raises_oserror(self, monkeypatch):
        from gateway import status

        def fake_run(args, **kwargs):
            result = MagicMock()
            result.returncode = 128
            result.stderr = "ERROR: The process cannot be terminated."
            result.stdout = ""
            return result

        monkeypatch.setattr(status.subprocess, "run", fake_run)
        with pytest.raises(OSError, match="cannot be terminated"):
            status.terminate_pid(12345, force=True)

    def test_graceful_on_windows_uses_os_kill_sigterm(self, monkeypatch):
        """Non-force path calls os.kill with SIGTERM (Windows has no SIGKILL).

        ``terminate_pid(pid)`` with force=False bypasses the taskkill branch
        and uses ``os.kill`` directly — so platform doesn't actually matter
        for the signal choice.  Verifies the getattr fallback works.
        """
        from gateway import status

        captured = {}

        def fake_kill(pid, sig):
            captured["pid"] = pid
            captured["sig"] = sig

        monkeypatch.setattr(status.os, "kill", fake_kill)
        status.terminate_pid(99, force=False)

        assert captured["pid"] == 99
        assert captured["sig"] == signal.SIGTERM

    def test_taskkill_not_found_falls_back_to_os_kill(self, monkeypatch):
        """On Windows without taskkill (WinPE, containers), fall back gracefully."""
        from gateway import status

        captured = {}

        def fake_run(args, **kwargs):
            raise FileNotFoundError(2, "taskkill not found")

        def fake_kill(pid, sig):
            captured["pid"] = pid
            captured["sig"] = sig

        monkeypatch.setattr(status.subprocess, "run", fake_run)
        monkeypatch.setattr(status.os, "kill", fake_kill)
        status.terminate_pid(42, force=True)

        assert captured["pid"] == 42
        assert captured["sig"] == signal.SIGTERM


# ---------------------------------------------------------------------------
# SIGKILL fallback pattern
# ---------------------------------------------------------------------------


class TestSigkillFallback:
    """Modules that want SIGKILL must fall back to SIGTERM when absent."""

    def test_getattr_fallback_works_when_sigkill_missing(self, monkeypatch):
        """The `getattr(signal, "SIGKILL", signal.SIGTERM)` pattern."""
        # Build a stand-in signal module with no SIGKILL attribute
        fake_signal = MagicMock()
        del fake_signal.SIGKILL  # ensure it's absent
        fake_signal.SIGTERM = 15

        result = getattr(fake_signal, "SIGKILL", fake_signal.SIGTERM)
        assert result == 15

    @pytest.mark.skipif(sys.platform == "win32", reason="SIGKILL not available on Windows")
    def test_getattr_fallback_prefers_sigkill_when_present(self):
        """On POSIX the fallback is a no-op: real SIGKILL wins."""
        result = getattr(signal, "SIGKILL", signal.SIGTERM)
        assert result == signal.SIGKILL


# ---------------------------------------------------------------------------
# OSError widening on liveness probes
#
# Post-#21561, ``ProcessRegistry._is_host_pid_alive`` delegates to
# ``gateway.status._pid_exists``, which is the cross-platform liveness
# primitive (psutil-first, ctypes/os.kill fallback). The tests below assert
# (a) the delegation is correct and (b) ``_pid_exists`` correctly widens
# Windows' ``OSError(WinError 87)`` / ``PermissionError`` behavior on the
# POSIX fallback branch.
# ---------------------------------------------------------------------------


class TestProcessRegistryOSErrorWidening:
    """_is_host_pid_alive delegates to gateway.status._pid_exists."""

    def test_oserror_treated_as_not_alive(self, monkeypatch):
        """_pid_exists → False propagates as _is_host_pid_alive → False."""
        from tools.process_registry import ProcessRegistry

        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: False)
        assert ProcessRegistry._is_host_pid_alive(12345) is False

    def test_permission_error_treated_as_alive(self, monkeypatch):
        """PermissionError is encoded by _pid_exists as alive=True; propagates as-is.

        This is a meaningful semantic change from the pre-#21561 version of
        this test (which asserted PermissionError → not-alive). The old
        ``os.kill(pid, 0)``-based probe couldn't distinguish "gone" from
        "owned by another user" on some platforms, so it conservatively
        returned False. The new psutil-based probe CAN distinguish them via
        ``OpenProcess + ERROR_ACCESS_DENIED`` on Windows / ``except
        PermissionError`` on POSIX, so alive=True is correct.
        """
        from tools.process_registry import ProcessRegistry

        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: True)
        assert ProcessRegistry._is_host_pid_alive(12345) is True


    def test_alive_pid_returns_true(self, monkeypatch):
        from tools.process_registry import ProcessRegistry

        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: True)
        assert ProcessRegistry._is_host_pid_alive(os.getpid()) is True


@pytest.mark.linux_only
class TestPidExistsOSErrorWidening:
    """gateway.status._pid_exists itself must widen Windows errors correctly.

    The POSIX fallback branch (reached when psutil isn't importable) is the
    only path where Python raises ``OSError(WinError 87)`` on Windows for a
    gone PID instead of ``ProcessLookupError``. The function must catch the
    wider ``OSError`` to match POSIX semantics.

    ``linux_only``: the subject is the POSIX fallback branch and its
    ``os.kill`` error handling, exercised with the errno values Windows
    produces. Gating to Linux is what makes ``_IS_WINDOWS`` genuinely False
    here instead of forced false by a patch.
    """

    def test_oserror_gone_pid_returns_false(self, monkeypatch):
        """Simulate Windows' OSError(WinError 87) for a gone PID via the POSIX fallback."""
        from gateway import status

        # Force the psutil-first branch to miss so we exercise the fallback.
        monkeypatch.setitem(
            __import__("sys").modules, "psutil",
            type("P", (), {"pid_exists": staticmethod(lambda pid: (_ for _ in ()).throw(ImportError()))})()
        )

        def fake_kill(pid, sig):
            raise OSError(22, "Invalid argument")

        monkeypatch.setattr(status.os, "kill", fake_kill)
        assert status._pid_exists(12345) is False

    def test_permission_error_returns_true(self, monkeypatch):
        """POSIX fallback: PermissionError means alive (owned by another user)."""
        from gateway import status

        monkeypatch.setitem(
            __import__("sys").modules, "psutil",
            type("P", (), {"pid_exists": staticmethod(lambda pid: (_ for _ in ()).throw(ImportError()))})()
        )

        def fake_kill(pid, sig):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(status.os, "kill", fake_kill)
        assert status._pid_exists(12345) is True


# ---------------------------------------------------------------------------
# tzdata dependency
# ---------------------------------------------------------------------------


class TestTzdataDependencyDeclared:
    """Windows installs must pull tzdata for zoneinfo to work."""

    def test_pyproject_declares_tzdata_for_win32(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
        # The dependency line should be conditional on sys_platform == 'win32'
        # and should NOT be in the core dependencies for Linux/macOS. We do
        # not care about the exact pinned version (which is bumped over time)
        # — only that tzdata is declared with a win32 marker. This is an
        # invariant check, not a snapshot test.
        from agent.re_compat import re
        # Match `"tzdata` … `; sys_platform == 'win32'"` allowing any version
        # specifier in between (==X.Y.Z, >=X.Y.Z,<W, etc.) and either quote
        # style on the marker.
        pattern = re.compile(
            r'"tzdata[^"]*;\s*sys_platform\s*==\s*[\'"]win32[\'"]\s*"'
        )
        assert pattern.search(source), (
            "tzdata must be a Windows-only dep in pyproject.toml dependencies "
            "(declared with a `; sys_platform == 'win32'` marker)"
        )


# ---------------------------------------------------------------------------
# README / docs consistency
# ---------------------------------------------------------------------------


class TestReadmeNoLongerSaysWindowsUnsupported:
    """The README shouldn't claim native Windows isn't supported."""

    def test_readme_does_not_say_not_supported(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "README.md").read_text(encoding="utf-8", errors="replace")
        # Previous string (removed in this PR): "Native Windows is not supported"
        assert "Native Windows is not supported" not in source, (
            "README.md still says native Windows is not supported — update the "
            "install copy to reflect the PowerShell installer."
        )

    def test_readme_mentions_powershell_installer(self):
        root = Path(__file__).resolve().parents[2]
        source = (root / "README.md").read_text(encoding="utf-8", errors="replace")
        assert "install.ps1" in source, (
            "README.md must point at scripts/install.ps1 for Windows users"
        )


# ---------------------------------------------------------------------------
# pty_bridge graceful import on Windows
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _subprocess_compat shared helpers
# ---------------------------------------------------------------------------


class TestSubprocessCompatHelpers:
    """hermes_cli/_subprocess_compat.py POSIX + Windows behaviour."""

    def test_is_windows_matches_sys_platform(self):
        from hermes_cli import _subprocess_compat as sc
        assert sc.IS_WINDOWS == (sys.platform == "win32")

    def test_resolve_node_command_returns_absolute_on_posix(self):
        """On Linux, resolve_node_command('sh', ['-c','echo hi']) picks up /bin/sh."""
        from hermes_cli._subprocess_compat import resolve_node_command
        # We can't assert "npm is on PATH" portably; use `sh` which is
        # guaranteed on POSIX.  On Windows the test only confirms the
        # no-crash fallback path.
        argv = resolve_node_command("sh", ["-c", "echo hi"])
        assert argv[1:] == ["-c", "echo hi"]
        # First element is either an absolute path (sh found) or the bare
        # name (fallback) — both are acceptable behaviours.


    @pytest.mark.windows_only
    def test_windows_detach_flags_exclude_detached_process(self):
        """DETACHED_PROCESS must stay OUT of every detach bundle.

        ``windows_only`` (with ``IS_WINDOWS`` no longer patched): the helpers
        return 0 off Windows, so on Linux the old flag patch was the only
        thing making the bit assertions reachable at all.
        """
        from hermes_cli import _subprocess_compat as sc
        assert not sc.windows_detach_flags() & 0x00000008, (
            "DETACHED_PROCESS must not be in windows_detach_flags(): it makes "
            "CREATE_NO_WINDOW a no-op and re-creates the per-descendant "
            "console flash (#54220/#56747)."
        )
        assert not sc.windows_detach_flags_without_breakaway() & 0x00000008, (
            "DETACHED_PROCESS must not be in the no-breakaway fallback either."
        )

    @pytest.mark.windows_only
    def test_windows_detach_flags_includes_breakaway_from_job(self):
        """CREATE_BREAKAWAY_FROM_JOB is load-bearing for the GUI-driven update path.

        Without it, the gateway-respawn watcher spawned by ``hermes update``
        (which runs under hermes-setup.exe, itself a grandchild of the
        Electron Desktop app) gets reaped when Electron exits and its
        Win32 job object is torn down by the OS.  Result: gateway dies
        during update and never comes back.

        Regression guard against accidentally dropping the breakaway bit
        from the default detach bundle.  This was fixed in
        ``fix/windows-gateway-reliability`` (PR #40909) and the bit must
        stay in the default bundle going forward.
        """
        from hermes_cli import _subprocess_compat as sc
        assert sc.windows_detach_flags() & 0x01000000, (
            "CREATE_BREAKAWAY_FROM_JOB (0x01000000) must remain in the "
            "default detach flag bundle so the Desktop GUI update flow "
            "can respawn the gateway after Electron exits."
        )

    @pytest.mark.windows_only
    def test_windows_detach_flags_without_breakaway_drops_only_that_bit(self):
        """Fallback retry payload for restrictive job objects.

        Some Windows Terminal / container / kiosk configurations refuse
        CREATE_BREAKAWAY_FROM_JOB with ERROR_ACCESS_DENIED.  Callers
        catch ``OSError`` and retry with this payload (see
        ``gateway_windows._spawn_detached`` for the canonical pattern).
        It must drop ONLY the breakaway bit — DETACHED_PROCESS et al.
        are still required for the child to survive the parent's exit.
        """
        from hermes_cli import _subprocess_compat as sc
        full = sc.windows_detach_flags()
        fallback = sc.windows_detach_flags_without_breakaway()
        # Fallback equals full minus the breakaway bit, nothing else changed.
        assert fallback == full & ~0x01000000
        # And the detach bits we still need are present (hidden console, own
        # process group — NOT console-less DETACHED_PROCESS, see
        # test_windows_detach_flags_exclude_detached_process).
        assert fallback & 0x00000200, "fallback missing CREATE_NEW_PROCESS_GROUP"
        assert fallback & 0x08000000, "fallback missing CREATE_NO_WINDOW"


# ---------------------------------------------------------------------------
# tui_gateway/entry.py signal installation survives absent POSIX signals
# ---------------------------------------------------------------------------


class TestTuiGatewayEntrySignalGuards:
    """Importing tui_gateway.entry must not crash when SIGPIPE/SIGHUP absent.

    Linux has both signals, so this is mostly a source-level invariant check
    (no bare ``signal.SIGPIPE`` at module level without a ``hasattr`` guard).
    On Windows the import would have raised AttributeError before this fix.
    """

    def test_module_imports_cleanly(self):
        """Importing the module must not raise — verifies the guards work."""
        # Drop any cached import so the module re-initialises
        for mod in list(sys.modules):
            if mod.startswith("tui_gateway"):
                del sys.modules[mod]
        import tui_gateway.entry  # noqa: F401  # must not raise


# ---------------------------------------------------------------------------
# hermes_cli/kanban_db.py waitpid guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# tools/environments/local.py Windows temp dir & PATH injection
# ---------------------------------------------------------------------------


class TestLocalEnvironmentWindowsTempDir:
    """LocalEnvironment.get_temp_dir must return a native Windows path on
    Windows, NOT the POSIX ``/tmp`` literal (which Python can't open)."""

    def test_posix_path_preserved_on_linux(self):
        """Linux/macOS behaviour MUST be unchanged — return / tmp or
        tempfile.gettempdir()-derived POSIX path.  This is the 'do no harm'
        test — regressions here break every Unix user's terminal tool."""
        from tools.environments.local import LocalEnvironment

        env = LocalEnvironment(cwd="/tmp", timeout=10, env={})
        tmp_dir = env.get_temp_dir()
        if sys.platform != "win32":
            assert tmp_dir.startswith("/"), (
                f"POSIX temp dir must start with '/'; got {tmp_dir!r}"
            )

class TestLocalEnvironmentPathInjectionGated:
    """Sane PATH completion must stay POSIX-only."""

    @pytest.mark.windows_only
    def test_windows_path_is_left_unchanged(self):
        """``windows_only``: the assertion is that a real Windows ``PATH``
        (``;``-separated, drive-lettered) comes back untouched. On Linux the
        old ``_IS_WINDOWS`` patch made the function return early without ever
        meeting a genuine Windows PATH."""
        from tools.environments.local import _append_missing_sane_path_entries

        path = r"C:\Windows\System32;C:\Program Files\Git\bin"
        assert _append_missing_sane_path_entries(path) == path


# ---------------------------------------------------------------------------
# cli.py git path normalization
# ---------------------------------------------------------------------------


class TestMsysPathNormalization:
    """_normalize_msys_path should turn /c/Users/... into C:\\Users\\...
    on Windows and leave paths unchanged on POSIX."""

    def test_posix_noop(self):
        """Must NOT mutate paths on Linux/macOS."""
        from cli import _normalize_msys_path
        if sys.platform != "win32":
            assert _normalize_msys_path("/home/teknium/foo") == "/home/teknium/foo"
            assert _normalize_msys_path("/c/Users/foo") == "/c/Users/foo"
            assert _normalize_msys_path("C:/Users/foo") == "C:/Users/foo"
            assert _normalize_msys_path(None) is None

    def test_empty_string_preserved(self):
        from cli import _normalize_msys_path
        assert _normalize_msys_path("") == ""

    @pytest.mark.windows_only
    def test_windows_translation(self, monkeypatch):
        """On native Windows, /c/Users/... becomes C:\\Users\\...

        ``windows_only``: the function's whole job is producing native
        Windows paths, which is only meaningful where ``os.sep`` is ``\\``.
        """
        import cli as cli_mod
        monkeypatch.setattr(cli_mod.sys, "platform", "win32")
        assert cli_mod._normalize_msys_path("/c/Users/foo") == r"C:\Users\foo"
        assert cli_mod._normalize_msys_path("/C/Users/foo") == r"C:\Users\foo"
        assert cli_mod._normalize_msys_path("/cygdrive/d/data") == r"D:\data"
        assert cli_mod._normalize_msys_path("/mnt/c/Users") == r"C:\Users"
        # Already-native path is preserved
        assert cli_mod._normalize_msys_path(r"C:\Users\foo") == r"C:\Users\foo"
        # Forward-slash Windows path is preserved (git on Windows often
        # returns this form; it's valid for both PowerShell and Python, so
        # we don't need to translate).
        assert cli_mod._normalize_msys_path("C:/Users/foo") == "C:/Users/foo"


class TestWindowlessGatewayRestartSpec:
    """gateway_windows.windowless_gateway_restart_spec — supplies the
    hidden-console respawn spec (normalized interpreter + stable cwd + env
    overlay)."""

    def test_noop_on_non_windows(self):
        import hermes_cli.gateway_windows as gw

        argv = ["/path/venv/bin/python", "-m", "hermes_cli.main", "gateway", "run"]
        with mock.patch.object(gw.sys, "platform", "linux"):
            new_argv, cwd, env = gw.windowless_gateway_restart_spec(list(argv))
        assert new_argv == argv
        assert cwd == ""
        assert env == {}

    def test_empty_argv_is_safe(self):
        import hermes_cli.gateway_windows as gw

        new_argv, cwd, env = gw.windowless_gateway_restart_spec([])
        assert new_argv == []
        assert cwd == ""
        assert env == {}

    @pytest.mark.windows_only
    def test_windows_keeps_console_python_and_preserves_tail(self):
        """On Windows the console interpreter is kept (hidden-console launch,
        NOT a pythonw swap — #54220/#56747) while every subsequent argument
        is preserved verbatim.

        ``windows_only``: faking this on Linux needed two more fakes to hold
        it up — a pre-import so the lazy ``hermes_cli.gateway`` import didn't
        re-run ``gateway/status``'s ``import msvcrt`` branch, and a mock of
        ``get_hermes_home`` because the real one's ``Path.resolve()`` consults
        sysconfig and blew up under the platform patch. Both workarounds were
        symptoms of testing Windows on a host that isn't Windows; on the
        Windows runner neither is needed.
        """
        import hermes_cli.gateway_windows as gw

        argv = [
            "C:/venv/Scripts/python.exe",
            "-m",
            "hermes_cli.main",
            "--profile",
            "work",
            "gateway",
            "run",
            "--replace",
        ]

        # Only the environment-dependent lookups are stubbed — the host is
        # genuinely Windows here.
        with mock.patch.object(
            gw, "_stable_gateway_working_dir", return_value="C:/hermes"
        ), mock.patch(
            "hermes_cli.config.get_hermes_home", return_value="C:/hermes"
        ):
            new_argv, cwd, env = gw.windowless_gateway_restart_spec(list(argv))

        # Interpreter is kept as the console python — hidden-console launch,
        # no pythonw swap.
        assert new_argv[0] == "C:/venv/Scripts/python.exe"
        # Everything after the interpreter is byte-for-byte preserved.
        assert new_argv[1:] == argv[1:]
        assert cwd == "C:/hermes"
        assert env["VIRTUAL_ENV"] == str(Path("C:/venv"))
        assert "PYTHONPATH" in env


# ---------------------------------------------------------------------------
# gateway/run.py :: GatewayRunner._launch_detached_restart_command
# outer watcher Popen breakaway-denied fallback (PR #42993)
# ---------------------------------------------------------------------------


@pytest.mark.windows_only
class TestGatewayRunRestartWatcherOuterPopenFallback:
    """The Windows ``/restart`` watcher in ``gateway.run`` spawns an outer
    detached ``python -c <watcher>`` process with
    ``windows_detach_popen_kwargs()`` (which carries
    ``CREATE_BREAKAWAY_FROM_JOB``).  A restrictive parent job object rejects
    the breakaway bit with ``ERROR_ACCESS_DENIED`` (surfaced as ``OSError``);
    the launcher must retry once without breakaway, preserving argv and the
    scrubbed environment, and only warn — never crash, never leak secrets —
    if the retry also fails.

    Behavioral: drives the real coroutine with a mocked ``subprocess.Popen``
    rather than asserting on source text.

    ``windows_only``: this used to run on Linux behind a ``sys.platform``
    patch, and the breakaway-bit assertions had to be skipped there anyway
    (``_subprocess_compat`` caches ``IS_WINDOWS`` at import, so the flags
    were all 0) — i.e. the most important assertions in the class never
    executed. On the Windows runner they do.
    """

    @staticmethod
    def _fake_self():
        from types import SimpleNamespace

        return SimpleNamespace(
            _detached_restart_helper_started=False,
            _restart_drain_timeout=0.0,
        )

    @classmethod
    def _drive(cls, gr):
        asyncio.run(
            gr.GatewayRunner._launch_detached_restart_command(cls._fake_self())
        )

    def test_outer_watcher_retries_without_breakaway_on_oserror(self, monkeypatch):
        import gateway.run as gr
        from hermes_cli._subprocess_compat import (
            windows_detach_flags_without_breakaway,
            windows_detach_popen_kwargs,
        )

        monkeypatch.setattr(gr, "_resolve_hermes_bin", lambda: ["hermes"])

        calls = []

        def fake_popen(argv, **kwargs):
            calls.append((argv, kwargs))
            if len(calls) == 1:
                raise OSError(5, "Access is denied")  # ERROR_ACCESS_DENIED
            return MagicMock()

        monkeypatch.setattr("subprocess.Popen", fake_popen)

        self._drive(gr)

        assert len(calls) == 2, "outer watcher must retry exactly once on OSError"
        (argv1, kw1), (argv2, kw2) = calls

        # argv is identical across primary and fallback, and every current
        # watcher parameter survives:
        #   [watcher_python, "-c", <script>, str(pid), str(restart_after_s), *cmd_argv]
        assert argv1 == argv2
        assert argv1[1] == "-c"
        assert argv1[3] == str(os.getpid())
        assert float(argv1[4]) >= 5.0  # restart deadline preserved
        assert argv1[-2:] == ["gateway", "restart"]

        # Scrubbed env preserved and identical on both calls.
        assert kw1["env"] is kw2["env"]
        assert "_HERMES_GATEWAY" not in kw1["env"]

        # Stable, non-flag spawn configuration preserved across both attempts.
        assert kw1["stdout"] is subprocess.DEVNULL
        assert kw1["stderr"] is subprocess.DEVNULL
        assert kw2["stdout"] is subprocess.DEVNULL
        assert kw2["stderr"] is subprocess.DEVNULL

        # Primary spreads the full detach helper.  Assert every returned helper
        # kwarg is present on the call.  The fallback uses the explicit
        # no-breakaway creationflags.
        expected_primary = windows_detach_popen_kwargs()
        for key, value in expected_primary.items():
            assert kw1[key] == value
        assert kw2["creationflags"] == windows_detach_flags_without_breakaway()
        assert "start_new_session" not in kw2

        # The point of the whole fallback: primary asks for breakaway, the
        # retry drops exactly that bit. Reachable now that the flags are real.
        _BREAKAWAY = 0x01000000
        assert kw1["creationflags"] & _BREAKAWAY, (
            "primary spawn must request CREATE_BREAKAWAY_FROM_JOB"
        )
        assert not (kw2["creationflags"] & _BREAKAWAY), (
            "fallback spawn must drop CREATE_BREAKAWAY_FROM_JOB"
        )


    def test_outer_watcher_happy_path_spawns_once(self, monkeypatch):
        import gateway.run as gr

        monkeypatch.setattr(gr, "_resolve_hermes_bin", lambda: ["hermes"])

        calls = []
        monkeypatch.setattr(
            "subprocess.Popen",
            lambda argv, **kwargs: calls.append((argv, kwargs)) or MagicMock(),
        )
        warn = MagicMock()
        monkeypatch.setattr(gr.logger, "warning", warn)

        self._drive(gr)

        assert len(calls) == 1, "no retry when the primary spawn succeeds"
        warn.assert_not_called()

    def test_outer_watcher_dual_failure_warns_without_leaking_secrets(
        self, monkeypatch
    ):
        import gateway.run as gr

        monkeypatch.setattr(gr, "_resolve_hermes_bin", lambda: ["hermes"])

        calls = []

        def always_fail(argv, **kwargs):
            calls.append((argv, kwargs))
            raise OSError(5, "Access is denied")

        monkeypatch.setattr("subprocess.Popen", always_fail)
        warn = MagicMock()
        monkeypatch.setattr(gr.logger, "warning", warn)

        # Deterministic sentinel in the environment the watcher inherits
        # (watcher_env = os.environ.copy()); the warning must never echo it.
        secret = "maxwell-do-not-log-this-secret-42993"
        monkeypatch.setenv("HERMES_TEST_SECRET", secret)

        # Dual failure must NOT propagate — the user's CLI still exits cleanly.
        self._drive(gr)

        assert len(calls) == 2, "both primary and fallback attempted"
        warn.assert_called_once()

        # Secret-safe logging: only (interpreter basename, error field, error
        # code) are logged — never the exception object (str(exc) can carry a
        # path), the argv (watcher source + interpreter path), or env contents.
        argv_used, kwargs_used = calls[0]
        fmt, *log_args = warn.call_args.args
        assert len(log_args) == 3, "warning should log only (basename, field, code)"
        basename_arg, error_field, error_code = log_args
        assert basename_arg == os.path.basename(argv_used[0])
        assert error_field in ("winerror", "errno")
        assert isinstance(error_code, int)
        for arg in log_args:
            assert not isinstance(arg, (OSError, list, dict))

        # The watcher's env carried the sentinel; the rendered warning must not.
        assert secret in (kwargs_used.get("env") or {}).get("HERMES_TEST_SECRET", "")
        rendered = fmt % tuple(log_args)
        assert secret not in rendered
        assert argv_used[2] not in rendered  # watcher script body
        assert "argv" not in fmt.lower()
        assert "env=" not in fmt.lower()
