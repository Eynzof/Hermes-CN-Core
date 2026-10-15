"""C1 marker lifecycle: real Windows hand-off and process identities."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import time

import pytest

from tests.installation_launcher_fixture import publish_fixture_launcher
from tests.scripts.desktop_update.windows_handoff_support import (
    SCRIPT, MARKER, _creation_time, _dead_pid,
    sleeper as sleeper,
)

from tests.scripts.desktop_update.windows_marker_support import (
    CLI, _run,
)


@pytest.mark.platforms('windows')
def test_dead_owner_marker_is_reclaimed_and_released(tmp_path: Path) -> None:
    (tmp_path / MARKER).write_bytes(f'{_dead_pid()}\n{int(time.time())}\nct:5.000\n'.encode())
    pid, code, out = _run(tmp_path, '-SelfTestMarker')
    assert code == 0, out
    assert not (tmp_path / MARKER).exists()
    log = (tmp_path / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert f'claimed update marker (pid {pid})' in log
    assert 'removed update marker (owned)' in log



@pytest.mark.platforms('windows')
def test_desktop_that_never_exits_is_not_relaunched_over(
    tmp_path: Path, sleeper: subprocess.Popen, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The verdict (refuse with 4, never relaunch) is the same at any ceiling; the production
    # 150 s one alone pushed this file to the runner's 300 s per-file cap on a loaded runner.
    monkeypatch.setenv('HERMES_UPDATE_DESKTOP_EXIT_SECONDS', '5')
    install = tmp_path / 'checkout'
    publish_fixture_launcher(install, CLI)
    home = tmp_path / 'home'; home.mkdir()
    # The Desktop's bridge claim, which a -DesktopPid hand-off adopts (A4).
    (home / MARKER).write_bytes(f'{sleeper.pid}\n{int(time.time())}\nct:{_creation_time(sleeper.pid)}\n'.encode())
    relaunch = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32' / 'hostname.exe'
    _, code, out = _run(home, '-DesktopPid', str(sleeper.pid), '-RelaunchExe', str(relaunch),
                        install=install)
    assert code == 4, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'did not exit within 5s' in log, log   # the override reached the script's wait
    assert 'relaunching desktop' not in log, log



@pytest.mark.platforms('windows')
def test_ui_profile_sweep_keeps_dirs_of_live_handoffs(tmp_path: Path, sleeper: subprocess.Popen) -> None:
    live = tmp_path / f'hermes-update-ui-{sleeper.pid}'
    dead = tmp_path / f'hermes-update-ui-{_dead_pid()}'
    live.mkdir(); dead.mkdir()
    home = tmp_path / 'home'; home.mkdir()
    result = subprocess.run(
        ['powershell', '-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', str(SCRIPT), '-SelfTestUi', '-NoUi'],
        cwd=tmp_path, env={**os.environ, 'HERMES_HOME': str(home), 'TEMP': str(tmp_path),
                           'HERMES_SELFTEST_HOLD_SECONDS': '0'},
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert live.exists(), 'another live hand-off lost its browser profile'
    assert not dead.exists()
