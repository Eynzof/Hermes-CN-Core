"""C3 receipts: real Windows hand-offs, isolated per-file budget."""
from __future__ import annotations

from pathlib import Path

import pytest


from tests.scripts.desktop_update.windows_commit_support import _handoff


@pytest.mark.platforms('windows')
@pytest.mark.parametrize(('receipt', 'ok'), [('interrupted', True), ('partial', True), ('interrupted,another-run', False),
                                             ('failed', False)])
def test_nonzero_exit_after_the_commit_point_is_installed_with_a_followup(tmp_path: Path, receipt: str, ok: bool) -> None:
    # Contract C3: `hermes update` exits 130 / 1 AFTER its commit point (Ctrl-C after the code
    # moved, a parked autostash). Its own receipt (matched by the correlation id) says so: the
    # result must report the update installed with an owed follow-up, never "update failed".
    code, out, argv, result, _ = _handoff(tmp_path, HANDOFF_RECEIPT=receipt, HANDOFF_EXIT='130')
    if ok:
        assert code == 0, out
        assert (result['ok'], result['exit_code'], result['manual']) == (True, 0, True), result
        assert result['message'].startswith('Hermes was updated, but'), result
        assert [w.split(':')[0] for w in result['warnings']] == ['update'], result
        assert argv[-1] == ['gateway', 'start', '--all'], argv   # the post-commit steps still run
    else:
        assert code == 130, out
        assert (result['ok'], result['exit_code']) == (False, 130), result
