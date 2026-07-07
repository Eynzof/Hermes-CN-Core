"""Reusable PowerShell session — spawn once, run many commands (P-042).

[CN-fork] Optimization follow-up to P-016/P-019 (PowerShell is the only Windows
shell) and P-020 (registry PATH refresh).  Spawning a fresh ``powershell.exe``
per terminal command pays the full Windows process-creation + DLL-load +
interpreter-init cost every call (~80-100ms warm, ~200-400ms cold — root-cause
analysis hotspot #6).  A *persistent* session pays that once: subsequent
commands are piped to the already-running interpreter over stdin and complete in
~1-5ms.

``powershell.exe -NoProfile -NonInteractive -Command -`` reads and executes
stdin **incrementally** (verified empirically), so we can feed one command,
read its output up to a unique completion marker, then feed the next — all
through the same process.  Each command is base64-wrapped into a single physical
stdin line and run through ``Invoke-Expression`` inside a ``try/catch``, so the
completion marker is emitted no matter how malformed the user command is (a
syntax error is caught, not left wedging the parser).

Robustness properties:

* **Thread-safe.**  A single re-entrant lock serialises ``run``/``run_script``
  so concurrent terminal calls on one session can't interleave on the pipe.
* **Self-healing.**  A dead interpreter (crash, someone ran ``exit``) is
  detected via ``poll()``/EOF and respawned lazily on the next call.
* **Timeout + interrupt recovery.**  A hung command can't be cancelled
  in-place in a shared interpreter, so on timeout/interrupt the whole session is
  killed and respawned; the caller gets the partial output plus a 124/130 code.
* **UTF-8.**  The session sets ``[Console]::OutputEncoding`` / ``$OutputEncoding``
  to UTF-8 on start so CJK output round-trips (parity with the spawn path's
  ``ps_with_utf8``).

This module is Windows-oriented but has **no** hard Windows import, so its pure
logic (marker parsing, command combining) is unit-testable on any host, and the
live-session tests ``skipif`` when PowerShell is absent.
"""

from __future__ import annotations

import base64
import logging
import os
from platform_utils import is_windows
import queue
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)

_IS_WINDOWS = is_windows()


def _hide_flags() -> int:
    """Windows no-console creation flags (0 elsewhere / on import failure)."""
    if not _IS_WINDOWS:
        return 0
    try:
        from hermes_cli._subprocess_compat import windows_hide_flags

        return windows_hide_flags()
    except Exception:
        return 0

# Sentinel the reader thread pushes when the interpreter's stdout hits EOF
# (the process exited) so a blocked reader in ``run`` unwinds promptly.
_EOF = object()

# PowerShell preamble run once per session: UTF-8 output, keep going on errors
# (parity with the spawn wrapper's ``$ErrorActionPreference='Continue'``), and
# silence progress bars that would otherwise pollute captured stdout.
_INIT_SCRIPT = (
    "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; "
    "$OutputEncoding=[System.Text.Encoding]::UTF8; "
    "$ErrorActionPreference='Continue'; "
    "$ProgressPreference='SilentlyContinue'"
)


@dataclass
class PSResult:
    """Outcome of a single command run through a :class:`PowerShellSession`."""

    output: str
    returncode: int
    cwd: str = ""
    timed_out: bool = False
    interrupted: bool = False
    session_died: bool = False

    @property
    def success(self) -> bool:
        return self.returncode == 0 and not (
            self.timed_out or self.interrupted or self.session_died
        )


def combine_commands(commands: list[str], separator: str = "; ") -> str:
    """Join *commands* into one script for a single-round-trip batch run.

    Blank/whitespace-only entries are dropped so a stray ``""`` doesn't inject
    an empty statement.  Kept a module-level pure function so batching logic is
    testable without spawning a shell.
    """
    return separator.join(c.strip() for c in commands if c and c.strip())


class PowerShellSession:
    """A single long-lived PowerShell process reused across many commands."""

    def __init__(
        self,
        shell_path: str = "powershell.exe",
        *,
        cwd: str | None = None,
        env: dict | None = None,
        default_timeout: float = 120.0,
        start_timeout: float = 30.0,
    ):
        self._shell_path = shell_path
        self._start_cwd = cwd or os.getcwd()
        self._env = env
        self._default_timeout = default_timeout
        self._start_timeout = start_timeout

        self._proc: subprocess.Popen | None = None
        self._reader: threading.Thread | None = None
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.RLock()
        # Unique per-session marker prefix so a command echoing an old marker
        # string can never be mistaken for a genuine completion sentinel.
        self._marker_base = f"__HERMES_PSDONE_{uuid.uuid4().hex[:16]}_"
        self._seq = 0
        self._cwd = self._start_cwd

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def cwd(self) -> str:
        return self._cwd

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self) -> None:
        """Spawn the interpreter (idempotent — no-op if already alive)."""
        with self._lock:
            self._ensure_started()

    def _ensure_started(self) -> None:
        if self.is_alive():
            return
        self._spawn()

    def _spawn(self) -> None:
        args = [
            self._shell_path,
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            "-",
        ]
        popen_kwargs: dict = {}
        if _IS_WINDOWS:
            popen_kwargs["creationflags"] = _hide_flags()
        self._proc = subprocess.Popen(
            args,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
            cwd=self._start_cwd if os.path.isdir(self._start_cwd) else None,
            env=self._env,
            **popen_kwargs,
        )
        self._queue = queue.Queue()
        self._reader = threading.Thread(
            target=self._reader_loop,
            args=(self._proc, self._queue),
            daemon=True,
        )
        self._reader.start()

        # Prime encoding/error prefs, then a readiness round-trip so ``start()``
        # only returns once the interpreter is actually accepting commands.
        self._write_line(_INIT_SCRIPT)
        self._seq += 1
        marker = f"{self._marker_base}{self._seq}__"
        self._write_line(
            f"Write-Output ('{marker}0|'+(Get-Location).Path+'{marker}')"
        )
        self._read_until_marker(marker, self._start_timeout, check_interrupt=False)
        logger.info("PowerShell session started (pid=%s)", self._proc.pid)

    def _reader_loop(self, proc: subprocess.Popen, q: queue.Queue) -> None:
        try:
            for line in proc.stdout:  # blocking readline until EOF
                q.put(line.rstrip("\r\n"))
        except (OSError, ValueError):
            pass
        finally:
            q.put(_EOF)

    def _write_line(self, line: str) -> None:
        assert self._proc is not None and self._proc.stdin is not None
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _restart(self) -> None:
        """Kill the current interpreter and spawn a fresh one."""
        self._terminate_proc()
        self._spawn()

    def _terminate_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        try:
            if proc.stdin:
                try:
                    proc.stdin.close()
                except Exception:
                    pass
            if _IS_WINDOWS:
                # Tree-kill the interpreter and any children it spawned.  Run
                # taskkill in BYTES mode (no text=True): on a Chinese Windows it
                # prints GBK ("成功: ..."), which a utf-8 text-mode reader thread
                # would choke on with a noisy UnicodeDecodeError.  proc.kill()
                # is the fallback if taskkill isn't available.
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(proc.pid), "/F", "/T"],
                        capture_output=True,
                        timeout=5,
                        creationflags=_hide_flags(),
                    )
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
            else:
                proc.kill()
            try:
                proc.wait(timeout=2.0)
            except Exception:
                pass
        except Exception:
            pass
        finally:
            self._proc = None

    def close(self) -> None:
        with self._lock:
            self._terminate_proc()

    def __enter__(self) -> "PowerShellSession":
        self.start()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def __del__(self):
        try:
            self._terminate_proc()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    def run_script(
        self,
        script: str,
        timeout: float | None = None,
        *,
        check_interrupt: bool = True,
        activity_cb: Callable[[], None] | None = None,
    ) -> PSResult:
        """Run *script* verbatim in the session; return its :class:`PSResult`.

        *script* may be multi-line — it is base64-wrapped into a single stdin
        line and executed via ``Invoke-Expression`` inside a ``try/catch``, so
        the completion marker always fires.  ``$LASTEXITCODE`` (falling back to
        ``$?``) is captured as the return code, and the final ``Get-Location``
        as the cwd.

        *activity_cb* (if given) is invoked on each read-loop tick (<=0.1s) so a
        long-running command can report liveness to the gateway's inactivity
        watchdog; it must rate-limit itself.
        """
        timeout = self._default_timeout if timeout is None else timeout
        with self._lock:
            self._ensure_started()
            self._seq += 1
            marker = f"{self._marker_base}{self._seq}__"
            payload = base64.b64encode(script.encode("utf-8")).decode("ascii")
            # Reset $LASTEXITCODE to $null before the command so a persistent
            # session gives the SAME exit code a fresh spawn would: a
            # cmdlet-only command (which never sets $LASTEXITCODE) reports 0
            # instead of inheriting the previous external command's code, exactly
            # matching the spawn path's ``$hermes_ec = $LASTEXITCODE`` /
            # ``exit $null -> 0`` semantics.
            line = (
                f"$__hs=[Text.Encoding]::UTF8.GetString("
                f"[Convert]::FromBase64String('{payload}'));"
                f"$global:LASTEXITCODE=$null;"
                f"try{{Invoke-Expression $__hs}}"
                f"catch{{$_|Out-String -Width 4096|Write-Output}};"
                f"$__hec=$LASTEXITCODE;"
                f"if($null -eq $__hec){{$__hec=0}};"
                f"Write-Output '';"
                f"Write-Output ('{marker}'+[string]$__hec+'|'"
                f"+(Get-Location).Path+'{marker}')"
            )
            try:
                self._write_line(line)
            except (OSError, ValueError, AssertionError):
                # Interpreter died between calls — respawn and retry once.
                self._restart()
                self._write_line(line)
            return self._read_until_marker(
                marker, timeout, check_interrupt, activity_cb
            )

    def run(
        self,
        command: str,
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        check_interrupt: bool = True,
    ) -> PSResult:
        """Run a single user *command* (optionally in *cwd*).

        Formats identically to the spawn path: the command is run through
        ``Invoke-Expression ... | Out-String -Width 4096`` so object output is
        rendered the same way.
        """
        parts: list[str] = []
        if cwd:
            parts.append(
                f"Set-Location -LiteralPath '{cwd.replace(chr(39), chr(39) * 2)}'"
            )
        escaped = command.replace("'", "''")
        parts.append(
            f"Invoke-Expression '{escaped}' | Out-String -Width 4096 | Write-Output"
        )
        return self.run_script(
            "\n".join(parts), timeout=timeout, check_interrupt=check_interrupt
        )

    def run_batch(
        self, commands: list[str], *, timeout: float | None = None
    ) -> list[PSResult]:
        """Run *commands* sequentially, each reusing the warm session."""
        return [self.run(c, timeout=timeout) for c in commands]

    def run_combined(
        self,
        commands: list[str],
        *,
        separator: str = "; ",
        timeout: float | None = None,
    ) -> PSResult:
        """Run *commands* joined into one round-trip (single marker wait)."""
        return self.run(combine_commands(commands, separator), timeout=timeout)

    def interrupt_current(self) -> None:
        """Abort whatever is running by killing + respawning the interpreter.

        Used as the ``cancel_fn`` when the session backs a
        :class:`ProcessHandle` so the shared ``_wait_for_process`` interrupt
        path can stop a wedged command.
        """
        with self._lock:
            self._restart()

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def _read_until_marker(
        self,
        marker: str,
        timeout: float,
        check_interrupt: bool,
        activity_cb: Callable[[], None] | None = None,
    ) -> PSResult:
        out_lines: list[str] = []
        deadline = time.monotonic() + timeout
        interrupt_fn = _resolve_interrupt_fn() if check_interrupt else None

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._restart()
                return PSResult(
                    "\n".join(out_lines), 124, self._cwd, timed_out=True
                )
            if interrupt_fn is not None and interrupt_fn():
                self._restart()
                return PSResult(
                    "\n".join(out_lines), 130, self._cwd, interrupted=True
                )
            if activity_cb is not None:
                try:
                    activity_cb()
                except Exception:
                    pass
            try:
                line = self._queue.get(timeout=min(remaining, 0.1))
            except queue.Empty:
                continue
            if line is _EOF:
                # Interpreter exited without emitting our marker.
                self._proc = None
                return PSResult(
                    "\n".join(out_lines), 1, self._cwd, session_died=True
                )

            idx = line.find(marker)
            if idx != -1:
                last = line.rfind(marker)
                middle = line[idx + len(marker) : last]
                ec_str, _, cwd = middle.partition("|")
                try:
                    ec = int(ec_str)
                except ValueError:
                    ec = 0
                if cwd:
                    self._cwd = cwd
                # Drop the single blank line we inject before the marker.
                if out_lines and out_lines[-1] == "":
                    out_lines.pop()
                return PSResult("\n".join(out_lines), ec, self._cwd)

            out_lines.append(line)


def _resolve_interrupt_fn():
    """Return ``tools.interrupt.is_interrupted`` or ``None`` if unavailable."""
    try:
        from tools.interrupt import is_interrupted

        return is_interrupted
    except Exception:
        return None
