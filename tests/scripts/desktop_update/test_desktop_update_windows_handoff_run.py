"""Protocol 2 only adopts the live Desktop bridge for this update run."""
from __future__ import annotations

from pathlib import Path
import subprocess
import time

import pytest

from tests.scripts.desktop_update.windows_handoff_support import (
    MARKER, _creation_time, _script, _finish, sleeper as sleeper,
)


@pytest.mark.platforms('windows')
@pytest.mark.parametrize('bridge', ['ok', 'other-run', 'stale-ct', 'v1'])
def test_handoff_run_adopts_only_the_desktop_bridge_for_that_run(
    tmp_path: Path, sleeper: subprocess.Popen, bridge: str,
) -> None:
    started = int(time.time()) - 20
    ct = _creation_time(sleeper.pid)
    if bridge == 'stale-ct':
        ct = f'{float(ct) - 5:.3f}'
    run = 'other.run' if bridge == 'other-run' else 'desk-1-ab-12cd'
    body = f'{sleeper.pid}\n{started}\n' + ('' if bridge == 'v1' else f'ct:{ct}\n') + f'run:{run}\n'
    (tmp_path / MARKER).write_bytes(body.encode())
    code, out = _finish(_script(tmp_path, '-SelfTestMarker', '-NoMarkerCleanup',
                                '-DesktopPid', str(sleeper.pid), '-HandoffRun', 'desk-1-ab-12cd'))
    if bridge != 'ok':
        assert code == 2, out
        assert (tmp_path / MARKER).read_bytes() == body.encode()
        return
    assert code == 0, out
    lines = (tmp_path / MARKER).read_bytes().decode().split('\n')
    pid = out.split(' pid=')[1].split()[0]
    assert lines[0] == pid and lines[1] == str(started) and lines[2].startswith('ct:'), lines
    assert lines[3:] == ['run:desk-1-ab-12cd', ''], lines
