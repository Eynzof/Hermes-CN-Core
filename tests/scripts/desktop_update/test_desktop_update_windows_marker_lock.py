"""A7 on the real Windows hand-off script: every marker mutation happens inside one
hold of the kernel lock on ``<marker>.lock`` and the update child is the marker's
delegate before it runs a single instruction.
"""
from __future__ import annotations

from contextlib import suppress
from pathlib import Path
import subprocess
import sys
import time

import pytest
import psutil

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.windows_handoff_support import (
    MARKER,
    HOLD_CLI,
    _creation_time,
    _alive,
    _dead_pid,
    _script,
    _finish,
    _op,
    _HeldLock,
    _custodian,
    _log,
)


@pytest.fixture
def sleeper():
    proc = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(600)'])
    yield proc
    proc.kill()
    proc.wait()


# -- A7 ---------------------------------------------------------------------

@pytest.mark.platforms('windows')
def test_a7_claim_is_decided_inside_the_marker_lock(tmp_path: Path, sleeper: subprocess.Popen) -> None:
    """While another process holds <marker>.lock the claimant must not touch a dead marker;
    once the holder (which replaced it with a live claim meanwhile) lets go, it judges THAT."""
    dead = f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode()
    marker = tmp_path / MARKER
    marker.write_bytes(dead)
    lock = _HeldLock(tmp_path)
    try:
        claimant = _script(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup')
        time.sleep(5)   # PowerShell start-up plus a few lock retries
        assert claimant.poll() is None, 'claimant did not wait for the marker lock'
        assert marker.read_bytes() == dead, 'a dead marker was mutated outside the marker lock'
        live = f'{sleeper.pid}\n{int(time.time())}\nct:{_creation_time(sleeper.pid)}\n'.encode()
        marker.write_bytes(live)   # the lock holder's own decision: a new live claim
    finally:
        lock.release()
    code, out = _finish(claimant)
    assert code == 2, out
    assert marker.read_bytes() == live


@pytest.mark.platforms('windows')
def test_a7_concurrent_claimants_over_a_dead_marker_yield_one_owner(tmp_path: Path) -> None:
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, HOLD_CLI)
    home = tmp_path / 'home'; home.mkdir()
    marker = home / MARKER
    marker.write_bytes(f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode())
    hold = tmp_path / 'release-update'
    lock = _HeldLock(home)   # line every claimant up behind the lock, then let them race
    claimants = [_script(home, install=install, HANDOFF_HOLD=str(hold)) for _ in range(4)]
    try:
        time.sleep(6)
        lock.release()
        deadline = time.monotonic() + 120
        while not Path(str(hold) + '.pid').exists():
            assert time.monotonic() < deadline, 'no claimant reached the update'
            time.sleep(0.05)
        while sum(c.poll() is not None for c in claimants) < 3:
            assert time.monotonic() < deadline, [c.poll() for c in claimants]
            time.sleep(0.1)
        refused = [c for c in claimants if c.poll() == 2]
        running = [c for c in claimants if c.poll() is None]
        assert len(running) == 1 and len(refused) == 3, [c.returncode for c in claimants]
        assert marker.read_bytes().decode().split('\n')[0] == _custodian(home, running[0].pid)
    finally:
        hold.touch()
        for c in claimants:
            if c.poll() is None:
                try:
                    c.wait(timeout=120)
                except subprocess.TimeoutExpired:
                    subprocess.run(['taskkill', '/T', '/F', '/PID', str(c.pid)], capture_output=True)
    assert running[0].returncode == 0
    assert not marker.exists()


# -- the update child is the delegate before it runs anything ------------------

@pytest.mark.platforms('windows')
def test_script_killed_before_publishing_the_delegate_runs_no_update(tmp_path: Path) -> None:
    """Kill cell at the pre-publication boundary: windows.ps1 has created its `hermes update`
    child but has not published it as the marker delegate (the marker lock is held elsewhere).
    Killed there, no update instruction may have run, and the custodian line 1 names releases
    the marker (nothing holds the checkout)."""
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, HOLD_CLI)
    home = tmp_path / 'home'; home.mkdir()
    hold = tmp_path / 'release-update'
    ran = Path(str(hold) + '.pid')
    marker = home / MARKER
    help_ready = tmp_path / 'help-ready'
    help_release = tmp_path / 'help-release'
    script = _script(home, install=install, HANDOFF_HOLD=str(hold),
                     HANDOFF_HELP_READY=str(help_ready), HANDOFF_HELP_RELEASE=str(help_release))
    lock = None
    try:
        deadline = time.monotonic() + 60
        # Hold the real CLI's final probe until the marker lock is ours. Merely
        # seeing the claim races the script's subsequent delegate publication.
        while not help_ready.exists():
            assert time.monotonic() < deadline and script.poll() is None, _log(home)
            time.sleep(0.02)
        assert marker.read_bytes().split(b'\n')[0] == _custodian(home, script.pid).encode()
        lock = _HeldLock(home)   # after the claim, before the delegate publication
        help_release.touch()
        parent = psutil.Process(script.pid)
        child_pid = None
        deadline = time.monotonic() + 60
        while child_pid is None:
            # Native process enumeration avoids an unbounded WMI call on each
            # poll. The help probe may disappear between enumeration and read.
            for child in parent.children():
                with suppress(psutil.NoSuchProcess):
                    if '--yes' in child.cmdline():
                        child_pid = child.pid
                        break
            assert time.monotonic() < deadline and script.poll() is None, _log(home)
            if child_pid is None:
                time.sleep(0.05)
        subprocess.run(['taskkill', '/F', '/PID', str(script.pid)], capture_output=True, check=True)
        script.wait(timeout=30)
        time.sleep(3)
        assert not ran.exists(), 'the update child ran before it was published as the delegate'
        assert not _alive(child_pid), 'the never-resumed update child outlived its script'
        lines = marker.read_bytes().decode().split('\n')
        assert lines[0] == _custodian(home, script.pid), lines
        assert not any(line.startswith('delegate:') for line in lines), lines
    finally:
        help_release.touch()
        if lock:
            lock.release()
        hold.touch()
        if script.poll() is None:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(script.pid)], capture_output=True)
            script.wait()
    deadline = time.monotonic() + 60   # the custodian releases the marker once the lock is free
    while (verdict := _op(home, '-MarkerOp', 'reclaim')[1]).startswith('live '):
        assert time.monotonic() < deadline
        time.sleep(0.2)
    assert verdict in ('absent\n', 'reclaimed\n')
