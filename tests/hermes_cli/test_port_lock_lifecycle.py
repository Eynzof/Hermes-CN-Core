"""Releasing and reclaiming a port must preserve native cross-process exclusion."""

import pytest

from hermes_cli.port_lock import try_claim_port
from tests._fixtures._port_lock_probe import child_can_claim_port


@pytest.mark.platforms("linux", "macos", "windows")
def test_reclaimed_port_still_excludes_other_processes(tmp_path, unused_tcp_port):
    first = try_claim_port(unused_tcp_port, tmp_path)
    assert first is not None
    with first:
        reentrant = try_claim_port(unused_tcp_port, tmp_path)
        assert reentrant is not None
        reentrant.release()
        assert not child_can_claim_port(tmp_path, unused_tcp_port)
        late_reentrant = try_claim_port(unused_tcp_port, tmp_path)
        assert late_reentrant is not None
    assert child_can_claim_port(tmp_path, unused_tcp_port)

    second = try_claim_port(unused_tcp_port, tmp_path)
    assert second is not None
    with second:
        first.release()
        late_reentrant.release()
        assert not child_can_claim_port(tmp_path, unused_tcp_port)
    assert child_can_claim_port(tmp_path, unused_tcp_port)
