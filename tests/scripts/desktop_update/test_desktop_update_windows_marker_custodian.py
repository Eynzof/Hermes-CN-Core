"""C1 marker custodian: real Windows hand-off and process identities."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest
import psutil

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.legacy_desktop_reader import legacy_read
from tests.scripts.desktop_update.windows_handoff_support import (
    SCRIPT, MARKER, _creation_time, _HeldLock,
)

from tests.scripts.desktop_update.windows_marker_support import (
    HOLD_CLI, _run, _marker_lines, _parent_pid,
)


@pytest.mark.platforms('windows')
def test_script_killed_right_after_spawning_the_update_leaves_a_live_marker(tmp_path: Path) -> None:
    """C1 rule 6, written by the script itself: windows.ps1 is killed (taskkill /F, no /T) while
    its `hermes update` child is only starting up and has not taken the update lock. The marker
    must still read LIVE through the child named on line 4 -- also to an old packaged Desktop,
    which judges line 1 alone, so line 1 names the hand-off's custodian before the update starts
    and the first old-reader read right after the kill (the marker lock held, so nothing can
    take over yet) already sees a live owner (review 5423056011) -- and be released once that
    child is gone."""
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, HOLD_CLI)
    home = tmp_path / 'home'; home.mkdir()
    hold = tmp_path / 'release-update'
    marker = home / MARKER
    script = subprocess.Popen(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT),
         '-InstallRoot', str(install), '-NoUi'],
        cwd=tmp_path, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env={**os.environ, 'HERMES_HOME': str(home), 'HERMES_RUNTIME_DIR': str(tmp_path / 'empty-store'),
             'HANDOFF_HOLD': str(hold)},
    )
    child_pid_file = Path(str(hold) + '.pid')
    try:
        deadline = time.monotonic() + 120
        while not child_pid_file.exists():
            assert time.monotonic() < deadline and script.poll() is None, 'update child never started'
            time.sleep(0.02)
        # Kill the script the moment the delegate line is there (at most 3 s after the spawn).
        settle = time.monotonic() + 3
        while time.monotonic() < settle and not any(line.startswith('delegate:') for line in _marker_lines(marker)):
            time.sleep(0.01)
        a7 = _HeldLock(home)   # no marker mutation (a takeover included) until we let go
        try:
            subprocess.run(['taskkill', '/F', '/PID', str(script.pid)], capture_output=True, check=True)
            script.wait(timeout=30)
            seen = legacy_read(home)
            assert seen['live'] is not None and seen['kept'], ('hand-off just died', seen)
        finally:
            a7.release()
        child = int(child_pid_file.read_text(encoding='utf-8-sig'))
        deadline = time.monotonic() + 60
        while (lines := _marker_lines(marker))[:1] in ([], [str(script.pid)]):   # the custodian takes over
            assert time.monotonic() < deadline, lines
            time.sleep(0.1)
        assert lines[3].startswith('delegate:'), lines
        delegate = int(lines[3].split()[0].split(':')[1])
        assert delegate in (child, _parent_pid(child), _parent_pid(_parent_pid(child)))  # update or its launcher
        assert lines[3] == f'delegate:{delegate} ct:{_creation_time(delegate)}'

        # A real reader (the script itself) sees an update in progress and refuses.
        _, code, out = _run(home, '-SelfTestMarker', '-NoMarkerCleanup', install=install)
        assert code == 2, out
        seen = legacy_read(home)
        assert seen['live'] == {'pid': int(lines[0]), 'ageMs': seen['live']['ageMs']} and seen['kept'], seen

        hold.touch()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and psutil.pid_exists(delegate):
            time.sleep(0.2)
        while marker.exists():   # the custodian releases once the delegate is gone
            assert time.monotonic() < deadline, _marker_lines(marker)
            time.sleep(0.2)
        assert legacy_read(home)['live'] is None
        _, code, out = _run(home, '-SelfTestMarker', '-NoMarkerCleanup', install=install)
        assert code == 0, out
    finally:
        hold.touch()
        if script.poll() is None:
            subprocess.run(['taskkill', '/T', '/F', '/PID', str(script.pid)], capture_output=True)
            script.wait()
