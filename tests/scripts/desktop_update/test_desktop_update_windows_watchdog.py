"""C3 watchdog: real Windows hand-offs, isolated per-file budget."""
from __future__ import annotations

from pathlib import Path

import pytest


from tests.scripts.desktop_update.windows_commit_support import _handoff


@pytest.mark.platforms('windows')
def test_watchdog_remap_after_banner_reports_interrupted_followups(tmp_path: Path) -> None:
    code, out, _argv, result, _ = _handoff(
        tmp_path, '-NoGateway', HANDOFF_HANG='Update complete! (v1.0.0)',
        HERMES_UPDATE_STEP_IDLE_SECONDS='3',
    )
    assert code == 0, out
    assert (result['ok'], result['manual']) == (True, True), result
    assert any('interrupted' in w for w in result['warnings']), result



@pytest.mark.platforms('windows')
def test_cpu_busy_pipe_silent_update_is_not_killed_by_the_idle_watchdog(tmp_path: Path) -> None:
    code, out, _argv, result, _ = _handoff(
        tmp_path, '-NoGateway', HANDOFF_BUSY_SECONDS='15', HERMES_UPDATE_STEP_IDLE_SECONDS='4',
    )
    assert code == 0, out
    assert (result['ok'], result['warnings']) == (True, []), result



@pytest.mark.platforms('windows')
def test_hanging_update_help_probe_is_bounded(tmp_path: Path) -> None:
    code, out, argv, _result, _ = _handoff(
        tmp_path, '-NoGateway', '-ProbeTimeoutSeconds', '10', timeout=120, HANDOFF_HELP_HANG='1',
    )
    assert code == 0, out
    assert argv[0][:2] == ['update', '--yes'] and '--keep-stash' not in argv[0], argv
