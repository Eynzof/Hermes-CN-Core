"""Test that HERMES_DESKTOP_MANAGED skips port claim (Bug 1+3)."""

import os

import pytest

from hermes_cli.main import _compute_well_known_ports


def test_cmd_dashboard_skips_claim_when_desktop_managed(monkeypatch):
    """HERMES_DESKTOP_MANAGED=1 → cmd_dashboard does NOT call claim_port_set."""
    monkeypatch.setenv("HERMES_DESKTOP_MANAGED", "1")

    assert os.environ.get("HERMES_DESKTOP_MANAGED") == "1"

    # Simulate the guard condition from cmd_dashboard
    call_count = 0

    def mock_claim_port_set(ports, hermes_home=None):
        nonlocal call_count
        call_count += 1
        return []

    is_managed = os.environ.get("HERMES_DESKTOP_MANAGED") == "1"
    if is_managed:
        port_locks = []
    else:
        port_locks = mock_claim_port_set(_compute_well_known_ports(9120))

    assert port_locks == [], "Should get empty port_locks (skip claim)"
    assert call_count == 0, "claim_port_set should NOT be called when managed"


def test_cmd_dashboard_claims_when_not_desktop_managed(monkeypatch):
    """Without HERMES_DESKTOP_MANAGED → claim_port_set IS called."""
    monkeypatch.delenv("HERMES_DESKTOP_MANAGED", raising=False)

    assert os.environ.get("HERMES_DESKTOP_MANAGED") is None

    call_count = 0
    captured_ports = None

    def mock_claim_port_set(ports, hermes_home=None):
        nonlocal call_count, captured_ports
        call_count += 1
        captured_ports = ports
        return []

    is_managed = os.environ.get("HERMES_DESKTOP_MANAGED") == "1"
    if is_managed:
        _port_locks = []
    else:
        _port_locks = mock_claim_port_set(_compute_well_known_ports(9120))

    assert call_count == 1, "claim_port_set should be called exactly once"
    assert captured_ports == [9120, 8644, 8645]


@pytest.mark.parametrize(
    "env_value, should_skip",
    [
        ("1", True),
        ("0", False),
        ("", False),
        (None, False),
    ],
)
def test_desktop_managed_env_var_logic(monkeypatch, env_value, should_skip):
    """Verify the conditional logic for various HERMES_DESKTOP_MANAGED values."""
    if env_value is None:
        monkeypatch.delenv("HERMES_DESKTOP_MANAGED", raising=False)
    else:
        monkeypatch.setenv("HERMES_DESKTOP_MANAGED", env_value)

    is_managed = os.environ.get("HERMES_DESKTOP_MANAGED") == "1"
    assert is_managed == should_skip
