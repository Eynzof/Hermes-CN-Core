"""Tests for the LLM-ergonomics surface of the terminal + process tools.

Covers the P-061 fork patch: blocking regex wait (wait_for_pattern), inactivity
early-return waits, new-output-only reads (since_chars / poll offset),
output_path export on background retrieval, foreground→background promotion
(promote_on_timeout), interactive mode, session continuation (process_id), and
kimi-style mode normalization.
"""

import json
import os
import subprocess
import sys
import threading
import time
from unittest.mock import patch

import pytest

from tools.process_registry import (
    ProcessRegistry,
    ProcessSession,
    _buffer_append,
    process_registry,
)
from tools.terminal_tool import (
    _continue_background_session,
    _interactive_shell_command,
    _normalize_terminal_mode,
)


@pytest.fixture()
def registry():
    """Create a fresh ProcessRegistry."""
    return ProcessRegistry()


def _make_session(
    sid="proc_test123",
    command="echo hello",
    task_id="t1",
    exited=False,
    exit_code=None,
    output="",
    started_at=None,
) -> ProcessSession:
    """Helper to create a ProcessSession for testing."""
    return ProcessSession(
        id=sid,
        command=command,
        task_id=task_id,
        started_at=started_at or time.time(),
        exited=exited,
        exit_code=exit_code,
        output_buffer=output,
    )


def _wait_until(predicate, timeout: float = 5.0, interval: float = 0.05) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


# =========================================================================
# wait() — pattern / inactivity / since_chars
# =========================================================================


class TestWaitPattern:
    def test_wait_matches_new_output_while_running(self, registry):
        """wait(pattern=...) returns status='matched' as soon as the regex hits
        NEW output, even while the process keeps running."""
        s = _make_session(sid="proc_pat")
        registry._running[s.id] = s

        def emit_later():
            time.sleep(0.1)
            _buffer_append(s, "line1\nApplication startup complete\n")

        t = threading.Thread(target=emit_later)
        t.start()
        try:
            result = registry.wait(
                s.id, timeout=5, pattern=r"Application startup complete"
            )
        finally:
            t.join(timeout=1)

        assert result["status"] == "matched", result
        assert result["wait_matched"] is True
        assert result["matched_pattern"] == "Application startup complete"
        assert result["pattern"] == r"Application startup complete"
        assert result["process_running"] is True
        assert "Application startup complete" in result["output"]
        assert "elapsed_seconds" in result

    def test_wait_pattern_exits_without_match(self, registry):
        """If the process exits before the pattern appears, report exited with
        wait_matched=False instead of a phantom match."""
        s = _make_session(sid="proc_pat_exit", exited=True, exit_code=0, output="done")
        registry._finished[s.id] = s
        result = registry.wait(s.id, timeout=5, pattern=r"NEVER")
        assert result["status"] == "exited"
        assert result["wait_matched"] is False
        assert result["pattern"] == r"NEVER"

    def test_wait_invalid_pattern_rejected(self, registry):
        s = _make_session(sid="proc_badpat")
        registry._running[s.id] = s
        result = registry.wait(s.id, timeout=5, pattern="[unclosed")
        assert result["status"] == "error"
        assert "regex" in result["error"].lower()


class TestWaitInactivity:
    def test_inactivity_timeout_returns_early_with_partial_output(self, registry):
        """A still-running but SILENT process must not block the whole window:
        inactivity_timeout returns partial output early."""
        s = _make_session(sid="proc_inact", output="started\n")
        registry._running[s.id] = s

        start = time.monotonic()
        result = registry.wait(s.id, timeout=10, inactivity_timeout=1)
        elapsed = time.monotonic() - start

        assert result["status"] == "timeout", result
        assert result["inactivity_timeout"] is True
        assert result["process_running"] is True
        assert "started" in result["output"]
        assert elapsed < 5, f"inactivity wait took {elapsed:.1f}s"

    def test_pattern_wait_defaults_to_inactivity_cap(self, registry):
        """With pattern set and no inactivity_timeout, silence is capped at the
        default (120s) — modeled here with an explicit tiny value to prove the
        wiring: the inactivity branch carries wait_matched=False."""
        s = _make_session(sid="proc_pat_silent")
        registry._running[s.id] = s
        result = registry.wait(
            s.id, timeout=10, pattern=r"READY", inactivity_timeout=1
        )
        assert result["status"] == "timeout"
        assert result["inactivity_timeout"] is True
        assert result["wait_matched"] is False


class TestWaitSinceChars:
    def test_since_chars_returns_new_output_only(self, registry):
        """since_chars is the continuation cursor: only output produced AFTER
        the cursor is returned (terminal process_id continuation)."""
        s = _make_session(sid="proc_since", output="old line\n")
        registry._running[s.id] = s
        baseline = len(s.output_buffer)

        def emit_later():
            time.sleep(0.1)
            _buffer_append(s, "new line\n")

        t = threading.Thread(target=emit_later)
        t.start()
        try:
            result = registry.wait(s.id, timeout=5, since_chars=baseline)
        finally:
            t.join(timeout=1)

        assert result["status"] == "output", result
        assert result["process_running"] is True
        assert "new line" in result["output"]
        assert "old line" not in result["output"]


# =========================================================================
# read_log — output_path export + truncation metadata
# =========================================================================


class TestReadLogExport:
    def test_read_log_exports_full_buffer(self, registry, tmp_path):
        lines = "\n".join([f"line {i}" for i in range(100)])
        s = _make_session(output=lines)
        s._buffer_overflowed = True  # simulate the 200KB window wrapping
        registry._running[s.id] = s

        out = tmp_path / "proc_output.txt"
        result = registry.read_log(s.id, output_path=str(out))

        assert result["full_output_path"] == str(out)
        assert result["output_total_chars"] == len(lines)
        assert result["output_truncated"] is True
        assert out.read_text(encoding="utf-8") == lines

    def test_read_log_auto_path(self, registry):
        lines = "hello\nworld\n"
        s = _make_session(output=lines)
        registry._running[s.id] = s
        result = registry.read_log(s.id, output_path="auto")
        assert result["full_output_path"]
        assert os.path.isfile(result["full_output_path"])
        with open(result["full_output_path"], encoding="utf-8") as fh:
            assert fh.read() == lines


# =========================================================================
# poll — offset new-output reads + exit-code metadata
# =========================================================================


class TestPollErgonomics:
    def test_poll_offset_new_output_read(self, registry):
        s = _make_session(output="a\nb\nc\nd\n")
        registry._running[s.id] = s
        result = registry.poll(s.id, offset=2)
        assert result["output"] == "c\nd"
        assert result["total_lines"] == 4
        assert result["offset"] == 2

    def test_poll_reports_exit_code_meaning(self, registry):
        s = _make_session(
            exited=True, exit_code=1, output="no matches", command="grep foo bar"
        )
        registry._finished[s.id] = s
        result = registry.poll(s.id)
        assert result["exit_code_meaning"] == "No matches found (not an error)"

    def test_poll_reports_elapsed_and_truncation(self, registry):
        s = _make_session(output="x")
        s._buffer_overflowed = True
        registry._running[s.id] = s
        result = registry.poll(s.id)
        assert result["elapsed_seconds"] >= 0
        assert result["output_truncated"] is True
        assert result["output_total_chars"] == 1


# =========================================================================
# Foreground → background adoption (promote_on_timeout)
# =========================================================================


class _TestableEnv:
    """Minimal BaseEnvironment stand-in exposing _wait_for_process."""

    def __init__(self):
        from tools.environments.base import BaseEnvironment

        class _Env(BaseEnvironment):
            def _run_bash(self, cmd_string, *, login=False, timeout=120, stdin_data=None):
                raise NotImplementedError("Use real Popen in tests")

            def cleanup(self):
                pass

        self.env = _Env(cwd=os.getcwd(), timeout=30)


def _slow_process(seconds: float = 30) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-u", "-c", f"import time; time.sleep({seconds})"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )


class TestAdopt:
    def test_wait_for_process_promotes_on_timeout_keeps_running(self, registry):
        """Foreground timeout with promote_callback keeps the process alive and
        returns a registry session_id instead of killing (exit 124)."""
        env = _TestableEnv().env
        proc = _slow_process()
        session = registry.prepare_adopt_local("sleep", cwd=os.getcwd())
        try:
            with patch.object(registry, "_write_checkpoint"):
                result = env._wait_for_process(
                    proc,
                    timeout=1,
                    bounded_capture=True,
                    promote_callback=lambda p: registry.adopt_local(session, p),
                )

            assert result["promoted"] is True, result
            assert result["session_id"] == session.id
            assert result["timed_out"] is True
            assert result["returncode"] is None
            assert result["elapsed_seconds"] is not None
            # The process is STILL RUNNING — not killed.
            assert proc.poll() is None

            # The registry can poll / kill it like any background process.
            poll_result = registry.poll(session.id)
            assert poll_result["status"] == "running"
            killed = registry.kill_process(session.id)
            assert killed["status"] == "killed"
            assert _wait_until(lambda: registry.poll(session.id)["status"] == "exited")
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    def test_wait_for_process_pattern_match_promotes(self, registry):
        """wait_for_pattern returns as soon as the regex hits output; the live
        process is adopted as a background session."""
        env = _TestableEnv().env
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c",
             "import time; print('READY', flush=True); time.sleep(30)"],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        session = registry.prepare_adopt_local("readiness", cwd=os.getcwd())
        try:
            with patch.object(registry, "_write_checkpoint"):
                result = env._wait_for_process(
                    proc,
                    timeout=30,
                    bounded_capture=True,
                    wait_for_pattern=r"READY",
                    promote_callback=lambda p: registry.adopt_local(session, p),
                )

            assert result["pattern_matched"] is True, result
            assert result["matched_pattern"] == "READY"
            assert result["promoted"] is True
            assert result["session_id"] == session.id
            assert proc.poll() is None  # still running under the registry
        finally:
            if proc.poll() is None:
                try:
                    registry.kill_process(session.id)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

    def test_wait_for_process_timeout_without_promote_still_kills(self, registry):
        """Default behavior is unchanged: without promote_callback, timeout
        still kills (exit 124) — no regression for existing callers."""
        env = _TestableEnv().env
        proc = _slow_process()
        try:
            result = env._wait_for_process(proc, timeout=1, bounded_capture=True)
            assert result["returncode"] == 124
            assert "promoted" not in result
            # Process was killed — allow a beat for Windows handle signaling.
            assert _wait_until(lambda: proc.poll() is not None, timeout=5.0)
        finally:
            if proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass


# =========================================================================
# terminal_tool — mode normalization / interactive / continuation
# =========================================================================


class TestModeNormalization:
    def test_mode_aliases(self):
        assert _normalize_terminal_mode("run", False, False) == (False, False)
        assert _normalize_terminal_mode("execute", False, False) == (False, False)
        assert _normalize_terminal_mode("foreground", False, True) == (False, False)
        assert _normalize_terminal_mode("fg", False, True) == (False, False)
        assert _normalize_terminal_mode("background", False, False) == (True, False)
        assert _normalize_terminal_mode("bg", False, False) == (True, False)
        assert _normalize_terminal_mode("async", False, False) == (True, False)
        assert _normalize_terminal_mode("interactive", False, False) == (True, True)
        assert _normalize_terminal_mode("repl", False, False) == (True, True)
        assert _normalize_terminal_mode("shell", False, False) == (True, True)

    def test_unknown_mode_falls_back_to_flags(self):
        assert _normalize_terminal_mode("weird", True, True) == (True, True)
        assert _normalize_terminal_mode("weird", False, False) == (False, False)
        assert _normalize_terminal_mode(None, True, False) == (False, True)

    def test_interactive_shell_command_is_string(self):
        assert isinstance(_interactive_shell_command(), str)
        assert _interactive_shell_command().strip()


class TestSessionContinuation:
    def test_continue_background_session_end_to_end(self):
        """terminal(command=..., process_id=...) sends the command to a running
        process's stdin and returns ONLY the new output."""
        proc = subprocess.Popen(
            [sys.executable, "-u", "-c",
             "import sys; print('READY', flush=True);"
             " [print(line, end='', flush=True) for line in sys.stdin]"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        session = ProcessSession(
            id="proc_cont", command="cat", process=proc, pid=proc.pid,
            started_at=time.time(),
        )

        def _reader():
            try:
                raw_read = getattr(proc.stdout, "buffer", None)
                read = getattr(raw_read, "read1", None) or proc.stdout.read
                while True:
                    chunk = read(4096)
                    if not chunk:
                        break
                    text = chunk if isinstance(chunk, str) else chunk.decode("utf-8", errors="replace")
                    _buffer_append(session, text)
            except Exception:
                pass

        threading.Thread(target=_reader, daemon=True).start()
        process_registry._running[session.id] = session
        try:
            assert _wait_until(
                lambda: "READY" in (session.output_buffer or ""), timeout=5.0
            ), "child never printed READY"
            result = json.loads(
                _continue_background_session(session.id, "hello", timeout=10)
            )
            assert result["status"] == "output", result
            assert "hello" in result["output"]
            assert "READY" not in result["output"]  # only NEW output returned
        finally:
            process_registry._running.pop(session.id, None)
            try:
                proc.kill()
            except Exception:
                pass

    def test_continue_missing_session_returns_helpful_error(self):
        result = json.loads(_continue_background_session("proc_nope", "ls", timeout=5))
        assert result.get("error")
        assert "No background process" in result["error"]

    def test_continue_exited_session_returns_helpful_error(self, registry):
        s = _make_session(sid="proc_done", exited=True, exit_code=0)
        registry._finished[s.id] = s
        with patch(
            "tools.process_registry.process_registry.get",
            return_value=s,
        ):
            result = json.loads(
                _continue_background_session("proc_done", "ls", timeout=5)
            )
        assert result.get("error")
        assert "already exited" in result["error"]


# =========================================================================
# Schemas advertise the new surface
# =========================================================================


class TestSchemaSurface:
    def test_process_schema_has_new_params(self):
        from tools.process_registry import PROCESS_SCHEMA

        props = PROCESS_SCHEMA["parameters"]["properties"]
        for name in ("pattern", "inactivity_timeout", "block", "output_path"):
            assert name in props, f"process schema missing {name}"

    def test_terminal_schema_has_new_params(self):
        from tools.terminal_tool import TERMINAL_SCHEMA

        props = TERMINAL_SCHEMA["parameters"]["properties"]
        for name in (
            "mode",
            "interactive",
            "process_id",
            "promote_on_timeout",
            "wait_for_pattern",
            "inactivity_timeout",
        ):
            assert name in props, f"terminal schema missing {name}"

    def test_process_schema_wait_action_mentions_pattern(self):
        from tools.process_registry import PROCESS_SCHEMA

        desc = PROCESS_SCHEMA["description"]
        assert "pattern" in desc
