"""Test the port lock module, including PID reuse detection (Bug 5) and
lock file format consistency (Bug 8)."""

import os
import sys
import time

import pytest

from hermes_cli.port_lock import (
    _lock_file_path,
    _read_lock_owner,
    _read_lock_owner_with_start_time,
    _write_lock_owner,
    _pid_is_running,
    _stale_lock_owner,
    _get_process_start_time,
    _get_my_start_time,
    claim_port_set,
    release_port_lock,
    try_claim_port,
)


# ---------------------------------------------------------------------------
# Lock file format (Bug 8)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "content, expected_pid, expected_start_time",
    [
        ("12345\n", 12345, 0),  # old format (no colon)
        ("12345:67890\n", 12345, 67890),  # new format
        ("  12345  :  67890  \n", 12345, 67890),  # whitespace tolerance
        ("12345\n99999\n", 12345, 0),  # multi-line, old format
        ("12345:67890\nmore\n", 12345, 67890),  # multi-line, new format
        ("", None, None),  # empty file
        ("\n", None, None),  # only newline
        ("abc", None, None),  # non-numeric
        ("12abc", None, None),  # partially numeric
        ("0\n", 0, 0),  # PID 0, old format
        ("0:0\n", 0, 0),  # PID 0, new format
    ],
)
def test_read_lock_owner_edge_cases(
    tmp_path, content, expected_pid, expected_start_time
):
    """Verify parsing of various lock file formats."""
    lock_path = tmp_path / "test.lock"
    lock_path.write_text(content, encoding="utf-8")

    result_pid = _read_lock_owner(lock_path)
    assert result_pid == expected_pid, (
        f"PID mismatch for {content!r}: {result_pid} != {expected_pid}"
    )

    result_full = _read_lock_owner_with_start_time(lock_path)
    if expected_pid is None:
        assert result_full is None, (
            f"Expected None for {content!r}, got {result_full}"
        )
    else:
        assert result_full is not None
        assert result_full[0] == expected_pid
        assert result_full[1] == expected_start_time


def test_write_lock_owner_writes_new_format(tmp_path):
    """Verify that _write_lock_owner writes the pid:start_time format."""
    lock_path = tmp_path / "test.lock"
    test_pid = 42
    _write_lock_owner(lock_path, test_pid)

    text = lock_path.read_text(encoding="utf-8").strip()
    assert ":" in text, f"Expected ':' in lock file content: {text!r}"
    parts = text.split(":")
    assert len(parts) == 2, f"Expected two colon-separated parts, got {parts}"
    assert int(parts[0]) == test_pid
    assert int(parts[1]) > 0, "Start time should be positive"


# ---------------------------------------------------------------------------
# PID liveness (Bug 5)
# ---------------------------------------------------------------------------

def test_pid_is_running_consistency_matrix():
    """Verify pid_is_running behaves correctly for various inputs."""
    assert _pid_is_running(0) is False  # PID 0
    assert _pid_is_running(os.getpid()) is True  # ourselves
    assert _pid_is_running(99999999) is False  # nonexistent (probably)


def test_pid_is_running_system_pid_does_not_crash():
    """SYSTEM PID (4 on Windows) should not crash, regardless of return value."""
    try:
        _result = _pid_is_running(4)
        # Accept either True or False — just don't crash
    except Exception as exc:
        pytest.fail(f"_pid_is_running(4) raised {exc}")


# ---------------------------------------------------------------------------
# Stale lock detection with PID reuse (Bug 5)
# ---------------------------------------------------------------------------

def test_stale_lock_when_pid_not_running(tmp_path):
    """Lock with a dead PID is detected as stale and can be broken."""
    lock_path = _lock_file_path(50010, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a lock with a PID that (almost certainly) doesn't exist
    lock_path.write_text("99999999:0\n", encoding="utf-8")

    assert _stale_lock_owner(lock_path) is True, (
        "Lock with dead PID should be stale"
    )

    # The lock should be breakable
    lock = try_claim_port(50010, tmp_path)
    assert lock is not None, "Should claim port after breaking stale lock"
    lock.release()


def test_stale_lock_not_broken_when_owner_alive(tmp_path):
    """Lock with a running PID should NOT be detected as stale."""
    lock_path = _lock_file_path(50011, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write a lock with our own PID and our actual creation time.
    # _write_lock_owner uses _get_my_start_time() which should match
    # _get_process_start_time(our_pid) used in _stale_lock_owner.
    _write_lock_owner(lock_path, os.getpid())

    # Without an actual OS lock, try_claim_port should succeed
    # _stale_lock_owner should return False because we're alive and the
    # creation time matches.
    stale = _stale_lock_owner(lock_path)
    assert stale is False, (
        f"Lock with alive PID should not be stale, got {stale}"
    )


def test_stale_lock_with_reused_pid_detected(tmp_path):
    """Simulate PID reuse via mismatched creation time."""
    lock_path = _lock_file_path(50012, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Get our actual creation time
    my_start = _get_my_start_time()
    if my_start is None:
        pytest.skip("Cannot determine process creation time on this platform")

    # Write a lock with our PID but a deliberately different start time
    fake_start_time = my_start - 3600_000  # 1 hour before we were created
    lock_path.write_text(f"{os.getpid()}:{fake_start_time}\n", encoding="utf-8")

    # _stale_lock_owner should detect the mismatch and return stale
    stale = _stale_lock_owner(lock_path)
    assert stale is True, "PID reuse via mismatched start time should be detected as stale"


def test_pid_reuse_lock_can_be_broken(tmp_path):
    """End-to-end test: PID reuse stale lock can be broken and claimed.

    In a real PID-reuse scenario, the original process has exited, so its
    OS-level lock handle is gone.  The stale lock file (with dead PID and
    mismatched start_time) should be detected and replaced.
    """
    lock_path = _lock_file_path(50013, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    my_start = _get_my_start_time()
    if my_start is None:
        pytest.skip("Cannot determine process creation time")

    # Write a lock file claiming our PID but with a deliberately different
    # start time (simulating PID reuse).  No actual OS lock is held — the
    # original process exited, which is why its PID was recycled.
    fake_start_time = my_start - 3600_000
    lock_path.write_text(f"{os.getpid()}:{fake_start_time}\n", encoding="utf-8")

    # try_claim_port should detect the stale lock (PID is alive but start
    # time doesn't match — PID was reused), break it, and acquire the lock.
    lock = try_claim_port(50013, tmp_path)
    assert lock is not None, (
        "Should claim port even with PID reuse (stale detected)"
    )
    lock.release()


# ---------------------------------------------------------------------------
# Process creation time
# ---------------------------------------------------------------------------

def test_get_my_start_time_matches_own_pid():
    """Our own creation time should match when looked up by PID."""
    my_start = _get_my_start_time()
    our_start = _get_process_start_time(os.getpid())
    if my_start is not None and our_start is not None:
        assert abs(my_start - our_start) < 1000, (
            f"Creation time mismatch: _get_my_start_time={my_start}, "
            f"_get_process_start_time(our_pid)={our_start}"
        )


# ---------------------------------------------------------------------------
# Basic lock operations
# ---------------------------------------------------------------------------

def test_can_claim_and_release_port(tmp_path):
    """Basic claim + release cycle works."""
    lock = try_claim_port(50020, tmp_path)
    assert lock is not None, "Should claim free port"
    assert lock.port == 50020
    lock.release()


def test_double_claim_in_same_process_succeeds(tmp_path):
    """Claiming the same port twice in one process returns a no-op handle."""
    first = try_claim_port(50021, tmp_path)
    assert first is not None

    second = try_claim_port(50021, tmp_path)
    assert second is not None, "Second claim in same process should succeed (no-op)"

    second.release()
    first.release()


def test_claim_set_works_for_disjoint_ports(tmp_path):
    """Atomic claim set succeeds for non-overlapping ports."""
    result = claim_port_set([50031, 50032], tmp_path)
    assert result is not None, "Should claim disjoint port set"
    assert len(result) == 2
    for lock in result:
        lock.release()


def test_claim_and_release_cycle(tmp_path):
    """Full claim → release → reclaim cycle works."""
    lock = try_claim_port(50040, tmp_path)
    assert lock is not None
    lock.release()

    # After release, can claim again
    lock2 = try_claim_port(50040, tmp_path)
    assert lock2 is not None, "Should be able to reclaim after release"
    lock2.release()


# ---------------------------------------------------------------------------
# Round-trip: Python writes, Python reads
# ---------------------------------------------------------------------------

def test_lock_file_roundtrip(tmp_path):
    """Verify that written lock files can be read back correctly."""
    lock_path = _lock_file_path(50050, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    test_pid = 12345
    _write_lock_owner(lock_path, test_pid)

    # Read back with _read_lock_owner
    read_pid = _read_lock_owner(lock_path)
    assert read_pid == test_pid

    # Read back with _read_lock_owner_with_start_time
    full = _read_lock_owner_with_start_time(lock_path)
    assert full is not None
    assert full[0] == test_pid
    assert full[1] > 0, "Start time should be present"


def test_old_format_backward_compatible(tmp_path):
    """Old format (pid only, no colon) is still readable."""
    lock_path = _lock_file_path(50060, tmp_path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    # Write old format
    lock_path.write_text("9999\n", encoding="utf-8")

    assert _read_lock_owner(lock_path) == 9999
    full = _read_lock_owner_with_start_time(lock_path)
    assert full is not None
    assert full[0] == 9999
    assert full[1] == 0  # old format → start_time = 0
