"""Behavioral tests for Windows-specific compatibility fixes.

Complements ``tests/tools/test_windows_compat.py`` (which does source-level
pattern linting) with cross-platform-mocked tests that exercise the actual
code paths Hermes takes on native Windows.

Runs on Linux CI — every test mocks ``sys.platform``, ``subprocess.run``,
and ``os.kill`` as needed to simulate Windows behavior without requiring a
Windows runner.
"""

from __future__ import annotations

import os
import signal
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
    def test_no_op_on_posix(self):
        from hermes_cli import stdio

        assert stdio.is_windows() is False
        result = stdio.configure_windows_stdio()
        assert result is False

    def test_idempotent(self):
        from hermes_cli import stdio

        stdio.configure_windows_stdio()
        # Second call returns False because _CONFIGURED is set
        assert stdio.configure_windows_stdio() is False

    def test_windows_path_sets_env_and_reconfigures_streams(self, monkeypatch):
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: True)
        # Pretend the user has no prior setting
        monkeypatch.delenv("PYTHONIOENCODING", raising=False)
        monkeypatch.delenv("PYTHONUTF8", raising=False)
        monkeypatch.delenv("HERMES_DISABLE_WINDOWS_UTF8", raising=False)
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.delenv("VISUAL", raising=False)

        reconfigure_calls = []

        def fake_reconfigure(stream, *, encoding="utf-8", errors="replace"):
            reconfigure_calls.append((stream, encoding, errors))

        cp_calls = []

        def fake_flip():
            cp_calls.append(True)

        monkeypatch.setattr(stdio, "_reconfigure_stream", fake_reconfigure)
        monkeypatch.setattr(stdio, "_flip_console_code_page_to_utf8", fake_flip)
        # Pretend notepad.exe is on PATH (it always is on real Windows hosts,
        # but not on the Linux CI runner — mock it so the editor default
        # survives).
        monkeypatch.setattr(stdio, "_default_windows_editor", lambda: "notepad")

        result = stdio.configure_windows_stdio()
        assert result is True
        assert os.environ.get("PYTHONIOENCODING") == "utf-8"
        assert os.environ.get("PYTHONUTF8") == "1"
        # EDITOR must be set so prompt_toolkit's open_in_editor finds
        # a working program on Windows (it defaults to /usr/bin/nano).
        assert os.environ.get("EDITOR") == "notepad"
        assert len(cp_calls) == 1  # SetConsoleOutputCP path hit
        assert len(reconfigure_calls) == 3  # stdout, stderr, stdin

    def test_respects_existing_editor_var(self, monkeypatch):
        """User's explicit EDITOR wins over our default."""
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: True)
        monkeypatch.setenv("EDITOR", "code --wait")
        monkeypatch.setattr(stdio, "_reconfigure_stream", lambda *a, **kw: None)
        monkeypatch.setattr(stdio, "_flip_console_code_page_to_utf8", lambda: None)
        monkeypatch.setattr(stdio, "_default_windows_editor", lambda: "notepad")

        stdio.configure_windows_stdio()
        assert os.environ["EDITOR"] == "code --wait"

    def test_respects_existing_visual_var(self, monkeypatch):
        """VISUAL takes precedence over our EDITOR default too."""
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: True)
        monkeypatch.delenv("EDITOR", raising=False)
        monkeypatch.setenv("VISUAL", "nvim")
        monkeypatch.setattr(stdio, "_reconfigure_stream", lambda *a, **kw: None)
        monkeypatch.setattr(stdio, "_flip_console_code_page_to_utf8", lambda: None)
        monkeypatch.setattr(stdio, "_default_windows_editor", lambda: "notepad")

        stdio.configure_windows_stdio()
        # EDITOR should NOT be set when VISUAL already is (prompt_toolkit
        # checks VISUAL first anyway, but we also shouldn't override it).
        assert os.environ.get("EDITOR", "") != "notepad"
        assert os.environ["VISUAL"] == "nvim"

    def test_respects_existing_env_var(self, monkeypatch):
        """User's explicit PYTHONIOENCODING wins over our default."""
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: True)
        monkeypatch.setenv("PYTHONIOENCODING", "latin-1")
        monkeypatch.setattr(stdio, "_reconfigure_stream", lambda *a, **kw: None)
        monkeypatch.setattr(stdio, "_flip_console_code_page_to_utf8", lambda: None)

        stdio.configure_windows_stdio()
        assert os.environ["PYTHONIOENCODING"] == "latin-1"

    @pytest.mark.parametrize("optout", ["1", "true", "True", "yes"])
    def test_disable_flag_short_circuits(self, monkeypatch, optout):
        from hermes_cli import stdio

        monkeypatch.setattr(stdio, "is_windows", lambda: True)
        monkeypatch.setenv("HERMES_DISABLE_WINDOWS_UTF8", optout)

        reconfigure_hit = []
        monkeypatch.setattr(
            stdio,
            "_reconfigure_stream",
            lambda *a, **kw: reconfigure_hit.append(True),
        )

        result = stdio.configure_windows_stdio()
        assert result is False
        assert reconfigure_hit == [], "opt-out must skip stream reconfiguration"

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

class TestTerminatePidRoutingOnWindows:
    """``gateway.status.terminate_pid`` must use taskkill /T /F on Windows.

    On Linux we can't reload gateway/status with sys.platform=win32 because
    the module unconditionally imports ``msvcrt`` in that branch.  Instead
    we patch the module-level ``_IS_WINDOWS`` flag and ``subprocess.run``
    on the already-loaded module, which exercises the same branching code.
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

        monkeypatch.setattr(status, "_IS_WINDOWS", True)
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

        monkeypatch.setattr(status, "_IS_WINDOWS", True)
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

        monkeypatch.setattr(status, "_IS_WINDOWS", True)
        monkeypatch.setattr(status.subprocess, "run", fake_run)
        monkeypatch.setattr(status.os, "kill", fake_kill)
        status.terminate_pid(42, force=True)

        assert captured["pid"] == 42
        assert captured["sig"] == signal.SIGTERM

# ---------------------------------------------------------------------------
# SIGKILL fallback pattern
# ---------------------------------------------------------------------------

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

    def test_zero_or_none_pid_returns_false_without_probing(self, monkeypatch):
        """No wasted syscall on falsy pids."""
        from tools.process_registry import ProcessRegistry

        probes = []
        monkeypatch.setattr(
            "gateway.status._pid_exists",
            lambda pid: probes.append(pid) or True,
        )
        assert ProcessRegistry._is_host_pid_alive(None) is False
        assert ProcessRegistry._is_host_pid_alive(0) is False
        assert probes == []

    def test_alive_pid_returns_true(self, monkeypatch):
        from tools.process_registry import ProcessRegistry

        monkeypatch.setattr("gateway.status._pid_exists", lambda pid: True)
        assert ProcessRegistry._is_host_pid_alive(os.getpid()) is True

class TestPidExistsOSErrorWidening:
    """gateway.status._pid_exists itself must widen Windows errors correctly.

    The POSIX fallback branch (reached when psutil isn't importable) is the
    only path where Python raises ``OSError(WinError 87)`` on Windows for a
    gone PID instead of ``ProcessLookupError``. The function must catch the
    wider ``OSError`` to match POSIX semantics.
    """

    def test_oserror_gone_pid_returns_false(self, monkeypatch):
        """Simulate Windows' OSError(WinError 87) for a gone PID via the POSIX fallback."""
        from gateway import status

        # Force the psutil-first branch to miss so we exercise the fallback.
        monkeypatch.setitem(
            __import__("sys").modules, "psutil",
            type("P", (), {"pid_exists": staticmethod(lambda pid: (_ for _ in ()).throw(ImportError()))})()
        )
        monkeypatch.setattr(status, "_IS_WINDOWS", False)

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
        monkeypatch.setattr(status, "_IS_WINDOWS", False)

        def fake_kill(pid, sig):
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(status.os, "kill", fake_kill)
        assert status._pid_exists(12345) is True

# ---------------------------------------------------------------------------
# tzdata dependency
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# README / docs consistency
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# pty_bridge graceful import on Windows
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Entry points wire configure_windows_stdio
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

    def test_resolve_node_command_fallback_when_absent(self):
        from hermes_cli._subprocess_compat import resolve_node_command
        argv = resolve_node_command(
            "zzz-definitely-not-on-path-xyzzy", ["--help"]
        )
        # Must fall back to the bare name — NOT return None, NOT crash.
        assert argv[0] == "zzz-definitely-not-on-path-xyzzy"
        assert argv[1:] == ["--help"]

    def test_windows_flags_zero_on_posix(self):
        from hermes_cli._subprocess_compat import (
            windows_detach_flags,
            windows_detach_flags_without_breakaway,
            windows_hide_flags,
        )
        if sys.platform != "win32":
            assert windows_detach_flags() == 0
            assert windows_detach_flags_without_breakaway() == 0
            assert windows_hide_flags() == 0

    def test_windows_detach_popen_kwargs_is_posix_equivalent_on_posix(self):
        from hermes_cli._subprocess_compat import windows_detach_popen_kwargs
        kwargs = windows_detach_popen_kwargs()
        if sys.platform != "win32":
            # POSIX path MUST produce start_new_session=True, which maps to
            # os.setsid() in the child — identical to the unchanged main
            # branch behaviour.  Do NOT break Linux/macOS here.
            assert kwargs == {"start_new_session": True}
        else:
            # Windows path must include creationflags with all 4 bits set
            # (including CREATE_BREAKAWAY_FROM_JOB — see the dedicated
            # breakaway test below for the rationale).
            assert "creationflags" in kwargs
            assert kwargs["creationflags"] != 0
            # No start_new_session on Windows (silently no-op there).
            assert "start_new_session" not in kwargs

    def test_windows_detach_flags_has_expected_win32_bits(self, monkeypatch):
        """Simulate Windows to verify flag bundle."""
        from hermes_cli import _subprocess_compat as sc
        monkeypatch.setattr(sc, "IS_WINDOWS", True)
        flags = sc.windows_detach_flags()
        # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS | CREATE_NO_WINDOW |
        # CREATE_BREAKAWAY_FROM_JOB
        assert flags & 0x00000200, "missing CREATE_NEW_PROCESS_GROUP"
        assert flags & 0x00000008, "missing DETACHED_PROCESS"
        assert flags & 0x08000000, "missing CREATE_NO_WINDOW"
        assert flags & 0x01000000, "missing CREATE_BREAKAWAY_FROM_JOB"

    def test_windows_detach_flags_includes_breakaway_from_job(self, monkeypatch):
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
        monkeypatch.setattr(sc, "IS_WINDOWS", True)
        assert sc.windows_detach_flags() & 0x01000000, (
            "CREATE_BREAKAWAY_FROM_JOB (0x01000000) must remain in the "
            "default detach flag bundle so the Desktop GUI update flow "
            "can respawn the gateway after Electron exits."
        )

    def test_windows_detach_flags_without_breakaway_drops_only_that_bit(
        self, monkeypatch
    ):
        """Fallback retry payload for restrictive job objects.

        Some Windows Terminal / container / kiosk configurations refuse
        CREATE_BREAKAWAY_FROM_JOB with ERROR_ACCESS_DENIED.  Callers
        catch ``OSError`` and retry with this payload (see
        ``gateway_windows._spawn_detached`` for the canonical pattern).
        It must drop ONLY the breakaway bit — DETACHED_PROCESS et al.
        are still required for the child to survive the parent's exit.
        """
        from hermes_cli import _subprocess_compat as sc
        monkeypatch.setattr(sc, "IS_WINDOWS", True)
        full = sc.windows_detach_flags()
        fallback = sc.windows_detach_flags_without_breakaway()
        # Fallback equals full minus the breakaway bit, nothing else changed.
        assert fallback == full & ~0x01000000
        # And the three "detach" bits we still need are present.
        assert fallback & 0x00000200, "fallback missing CREATE_NEW_PROCESS_GROUP"
        assert fallback & 0x00000008, "fallback missing DETACHED_PROCESS"
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
# code_execution_tool TCP loopback on Windows
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# cron/scheduler.py /bin/bash dynamic resolution
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Node-ecosystem launcher resolution (npm / npx / node)
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

    def test_windows_path_is_left_unchanged(self, monkeypatch):
        from tools.environments import local as local_mod
        from tools.environments.local import _append_missing_sane_path_entries

        monkeypatch.setattr(local_mod, "_IS_WINDOWS", True)
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

    def test_windows_translation(self, monkeypatch):
        """Simulate Windows and verify /c/Users/... becomes C:\\Users\\..."""
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

# ---------------------------------------------------------------------------
# Gateway detached watcher — Windows creationflags
# ---------------------------------------------------------------------------

class TestWindowlessGatewayRestartSpec:
    """gateway_windows.windowless_gateway_restart_spec — the helper that
    converts a console-python gateway argv into a windowless pythonw one."""

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

    def test_windows_rewrites_to_pythonw_and_preserves_tail(self):
        """On Windows the interpreter is swapped for its windowless sibling
        while every subsequent argument is preserved verbatim."""
        import hermes_cli.gateway_windows as gw

        # Pre-import on the (Linux) host so the function's lazy
        # ``from hermes_cli.gateway import PROJECT_ROOT`` resolves from
        # sys.modules instead of re-importing under the win32 platform
        # patch below — a fresh import would run gateway/status.py's
        # ``if sys.platform == "win32": import msvcrt`` branch and crash on
        # Linux CI with ModuleNotFoundError.
        import hermes_cli.config  # noqa: F401
        import hermes_cli.gateway  # noqa: F401

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

        def fake_resolve(python_exe):
            return ("C:/base/pythonw.exe", Path("C:/venv"), ["C:/venv/Lib/site-packages"])

        # Mock get_hermes_home too: the real one calls Path.resolve(), which
        # consults sysconfig and raises ModuleNotFoundError under the win32
        # platform patch on a Linux host.
        with mock.patch.object(gw.sys, "platform", "win32"), mock.patch.object(
            gw, "_resolve_detached_python", side_effect=fake_resolve
        ), mock.patch.object(
            gw, "_stable_gateway_working_dir", return_value="C:/hermes"
        ), mock.patch(
            "hermes_cli.config.get_hermes_home", return_value="C:/hermes"
        ):
            new_argv, cwd, env = gw.windowless_gateway_restart_spec(list(argv))

        assert new_argv[0] == "C:/base/pythonw.exe"
        # Everything after the interpreter is byte-for-byte preserved.
        assert new_argv[1:] == argv[1:]
        assert cwd == "C:/hermes"
        assert env["VIRTUAL_ENV"] == str(Path("C:/venv"))
        assert "PYTHONPATH" in env
        assert "site-packages" in env["PYTHONPATH"]
