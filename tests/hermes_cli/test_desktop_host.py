from __future__ import annotations

import argparse

import pytest

from hermes_cli import desktop_host


def test_smoke_check_loads_entrypoint_without_managed_environment() -> None:
    called = False

    def dashboard_entrypoint(_args: argparse.Namespace) -> None:
        nonlocal called
        called = True

    assert (
        desktop_host.run(
            ["--smoke-check"],
            environ={},
            dashboard_entrypoint=dashboard_entrypoint,
        )
        == 0
    )
    assert called is False


def test_managed_host_invokes_dashboard_with_desktop_contract() -> None:
    received: argparse.Namespace | None = None

    def dashboard_entrypoint(args: argparse.Namespace) -> None:
        nonlocal received
        received = args

    result = desktop_host.run(
        ["--host", "127.0.0.1", "--port", "9127", "--no-open"],
        environ={"HERMES_DESKTOP": "1", "HERMES_DESKTOP_MANAGED": "1"},
        dashboard_entrypoint=dashboard_entrypoint,
    )

    assert result == 0
    assert received is not None
    assert received.host == "127.0.0.1"
    assert received.port == 9127
    assert received.no_open is True
    assert received.isolated is True
    assert received.status is False
    assert received.stop is False


def test_host_rejects_direct_unmanaged_launch() -> None:
    with pytest.raises(RuntimeError, match="only be started by Hermes Desktop"):
        desktop_host.run([], environ={}, dashboard_entrypoint=lambda _args: None)
