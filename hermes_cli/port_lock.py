"""Cross-process port coordination via advisory lock files.

When multiple Hermes instances (CLI dashboards, desktop-managed dashboards,
gateways, proxies) share a host, they use a lightweight lock file under
``$HERMES_HOME/.port-locks/<port>.lock`` to reserve ports before binding.
The lock is advisory and auto-released when the holding process exits.

The lock directory is created on first use. If locking cannot be performed
(for example on a read-only filesystem), the helpers fall back to a no-op
``PortLock`` so startup is not blocked.
"""

from __future__ import annotations

import os
import sys
import threading
from pathlib import Path
from typing import Optional


class PortLock:
    """Opaque handle representing a held port lock.

    Keeps the underlying file descriptor / handle alive for the lifetime of
    the object. Dropping the handle releases the lock.
    """

    def __init__(self, port: int, file_path: Path, handle):
        self.port = port
        self.file_path = file_path
        self._handle = handle
        self._closed = False

    def release(self) -> None:
        """Explicitly release the lock. Safe to call multiple times."""
        if self._closed:
            return
        self._closed = True
        try:
            _release_lock(self._handle)
        except Exception:
            pass

    def __enter__(self) -> "PortLock":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.release()

    def __del__(self):
        self.release()


def _lock_dir(hermes_home: str | Path | None = None) -> Path:
    """Return the directory where port lock files live."""
    if hermes_home is None:
        from hermes_constants import get_hermes_home

        hermes_home = get_hermes_home()
    return Path(hermes_home) / ".port-locks"


def _lock_file_path(port: int, hermes_home: str | Path | None = None) -> Path:
    """Return the lock file path for a given port."""
    return _lock_dir(hermes_home) / f"{port}.lock"


def _read_lock_owner(path: Path) -> Optional[int]:
    """Read the PID written into a lock file, if any."""
    try:
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return None
        first = text.splitlines()[0].strip()
        if not first:
            return None
        return int(first)
    except Exception:
        return None


def _write_lock_owner(path: Path, pid: int) -> None:
    """Write the current owner PID into the lock file."""
    try:
        path.write_text(f"{pid}\n", encoding="utf-8")
    except Exception:
        pass


def _pid_is_running(pid: int) -> bool:
    """Best-effort cross-platform PID liveness check."""
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            import ctypes

            kernel = ctypes.windll.kernel32
            # PROCESS_QUERY_LIMITED_INFORMATION (Vista+) is enough for
            # GetExitCodeProcess and least-privilege. Fall back to
            # PROCESS_QUERY_INFORMATION on older hosts.
            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            PROCESS_QUERY_INFORMATION = 0x0400
            STILL_ACTIVE = 259

            handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                handle = kernel.OpenProcess(PROCESS_QUERY_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                exit_code = ctypes.c_uint(0)
                if kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                    return exit_code.value == STILL_ACTIVE
                return True
            finally:
                kernel.CloseHandle(handle)
        except Exception:
            pass
        # Fallback: tasklist
        try:
            import subprocess

            output = subprocess.check_output(
                ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=5,
            )
            return str(pid) in output
        except Exception:
            return True  # be conservative: assume alive if we can't tell
    else:
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True


# ---------------------------------------------------------------------------
# Platform-specific locking primitives
# ---------------------------------------------------------------------------

if sys.platform == "win32":

    def _acquire_lock(handle) -> bool:
        """Try to acquire a Windows byte-range lock on the whole file."""
        try:
            import msvcrt

            # Lock the first 2**31-1 bytes non-blocking. msvcrt.locking
            # raises OSError on conflict.
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 0x7FFFFFFF)
            return True
        except OSError:
            return False

    def _release_lock(handle) -> None:
        try:
            import msvcrt

            msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 0x7FFFFFFF)
        except OSError:
            pass

else:

    def _acquire_lock(handle) -> bool:
        """Try to acquire a POSIX flock on the whole file."""
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except (OSError, IOError):
            return False

    def _release_lock(handle) -> None:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, IOError):
            pass


# Thread safety: two threads in the same process trying to claim the same port
# should coordinate in-memory too. A simple module-level mutex covers this rare
# case without adding per-process complexity.
_local_lock = threading.Lock()
_local_claims: set[int] = set()


def try_claim_port(
    port: int,
    hermes_home: str | Path | None = None,
    *,
    owner_pid: Optional[int] = None,
) -> Optional[PortLock]:
    """Try to claim a port via lock file.

    Returns a ``PortLock`` on success. The lock is released automatically when
    the object is garbage-collected or ``release()`` is called. Returns ``None``
    when the port is already locked by another live process.

    If the lock file cannot be created (read-only filesystem, permission
    denied), returns a no-op ``PortLock`` so Hermes can still start. The lock
    is best-effort coordination, not a security boundary.
    """
    with _local_lock:
        if port in _local_claims:
            # Same process already holds this port; return a lightweight handle.
            return PortLock(port, _lock_file_path(port, hermes_home), None)

        path = _lock_file_path(port, hermes_home)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            # Cannot create lock directory; fall back to no-op lock.
            return PortLock(port, path, None)

        handle = None
        try:
            # Open read-write so flock works on all platforms.
            handle = open(path, "a+", encoding="utf-8")
        except OSError:
            return PortLock(port, path, None)

        if _acquire_lock(handle):
            _write_lock_owner(path, owner_pid or os.getpid())
            _local_claims.add(port)
            return PortLock(port, path, handle)

        # Lock is held. Check whether the owner is still alive (stale lock).
        stale_owner = _read_lock_owner(path)
        if stale_owner is not None and not _pid_is_running(stale_owner):
            # Break stale lock by re-acquiring after closing the failed handle.
            try:
                handle.close()
            except Exception:
                pass
            try:
                # Reopen and claim. There is a small race here if another
                # process claims between close and reopen; in that case we
                # simply report the port as taken.
                handle = open(path, "a+", encoding="utf-8")
                if _acquire_lock(handle):
                    _write_lock_owner(path, owner_pid or os.getpid())
                    _local_claims.add(port)
                    return PortLock(port, path, handle)
            except OSError:
                pass

        # Port genuinely occupied (or race lost).
        try:
            handle.close()
        except Exception:
            pass
        return None


def release_port_lock(port: int) -> None:
    """Release the in-process claim for ``port`` if held.

    The underlying OS lock is already tied to the ``PortLock`` object; this
    helper only clears the local thread-safety bookkeeping.
    """
    with _local_lock:
        _local_claims.discard(port)


def claim_port_set(
    ports: list[int],
    hermes_home: str | Path | None = None,
) -> Optional[list[PortLock]]:
    """Atomically claim a set of ports, or none at all.

    Returns a list of ``PortLock`` handles on success. On failure (any port
    already locked), releases any locks already acquired and returns ``None``.
    """
    locks: list[PortLock] = []
    for port in ports:
        lock = try_claim_port(port, hermes_home)
        if lock is None:
            for acquired in locks:
                acquired.release()
            return None
        locks.append(lock)
    return locks
