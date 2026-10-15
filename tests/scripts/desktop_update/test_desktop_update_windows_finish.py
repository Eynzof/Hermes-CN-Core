"""C3 finish: real Windows hand-offs, isolated per-file budget."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import time

import pytest

from tests.scripts.desktop_update.windows_handoff_support import (
    _wait_for_log,
)

from tests.scripts.desktop_update.windows_commit_support import _handoff


@pytest.mark.platforms('windows')
def test_result_finished_at_is_stamped_after_the_r6_release_wait(tmp_path: Path) -> None:
    import msvcrt

    # The relaunched Desktop drops a non-manual result whose finished_at is 30 minutes old; the
    # R6 wait lasts up to 2 h. The result must carry the time the hand-off actually finished.
    release = None

    def release_after_wait(home: Path, fd: int) -> tuple[int, int]:
        try:
            _wait_for_log(home, 'keeping the update marker')
            before = json.loads((home / '.hermes-update-result.json').read_text(encoding='utf-8-sig'))
            # finished_at has whole-second precision: cross its boundary so
            # retaining the pre-wait timestamp cannot satisfy the assertion.
            time.sleep(2)
            return before['finished_at'], int(time.time())
        finally:
            os.close(fd)

    with ThreadPoolExecutor(max_workers=1) as executor:
        def hold_checkout(home: Path, install: Path) -> None:
            nonlocal release
            fd = os.open(install / '.hermes-update.lock', os.O_RDWR | os.O_CREAT | os.O_BINARY, 0o644)
            os.lseek(fd, 1 << 20, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            release = executor.submit(release_after_wait, home, fd)

        code, out, _, result, home = _handoff(tmp_path, '-NoGateway', prepare=hold_checkout)
        assert release is not None
        before, released_at = release.result()
    assert code == 0, out
    log = (home / 'logs/desktop-update-handoff.log').read_text(encoding='utf-8-sig')
    assert 'keeping the update marker' in log, log
    assert result['ok'] is True and result['finished_at'] >= released_at > before, result



@pytest.mark.platforms('windows')
def test_marker_with_a_live_delegate_is_handed_to_it_at_finish(tmp_path: Path) -> None:
    # A7 release rule: the owner deletes its claim unless a delegate still runs; then that
    # delegate becomes the owner (canonical body, started_at kept, no delegate line).
    pid_file = tmp_path / 'delegate.pid'
    try:
        code, out, _, _result, home = _handoff(tmp_path, HANDOFF_DELEGATE=str(pid_file))
        assert code == 0, out
        marker = (home / '.hermes-update-in-progress').read_bytes().decode().split('\n')
        assert marker[0] == pid_file.read_text(encoding='utf-8-sig'), marker
        assert marker[2].startswith('ct:') and marker[3:] == [''], marker
    finally:
        if pid_file.exists():
            subprocess.run(['taskkill', '/F', '/PID', pid_file.read_text(encoding='utf-8-sig')], capture_output=True)
