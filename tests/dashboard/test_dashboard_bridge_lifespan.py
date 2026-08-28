"""Lifespan wiring tests for the DLR Dashboard bridge listener.

``hermes_cli.web_server._lifespan`` starts the typed Named Pipe listener only
when ``HERMES_DASHBOARD_BRIDGE=1`` and always stops it on exit. These tests
drive the real async lifespan with a fake listener class so no real Windows
Named Pipe is created; the heavy/networked lifespan work is stubbed out so
only the bridge wiring is exercised.
"""

from __future__ import annotations

import asyncio
import types

import pytest

from hermes_cli import web_server


class _FakeBridgeListener:
    """Stand-in for hermes_cli.dashboard_bridge.DashboardBridgeListener."""

    def __init__(self, backend_factory, **kwargs):
        self.backend_factory = backend_factory
        self.started = False
        self.stopped = False
        self.pipe_name = r"\\.\pipe\hermes-dashboard-bridge-fake"

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True


class _FakePtyRegistry:
    async def close_all(self):
        return None


async def _never_ending(*args, **kwargs):
    try:
        await asyncio.Event().wait()
    except asyncio.CancelledError:
        pass


def _stub_lifespan_dependencies(monkeypatch):
    """Neutralise heavy/networked lifespan work; the wiring is what's tested."""
    monkeypatch.setattr(web_server, "_warm_gateway_module", lambda: None)
    monkeypatch.setattr(web_server, "_warm_platform_registry", lambda *a, **k: None)
    try:
        import agent.models_dev as models_dev

        monkeypatch.setattr(models_dev, "prewarm_models_dev_async", lambda: None)
    except Exception:
        pass
    monkeypatch.setattr(web_server, "_dashboard_selftest_loop", _never_ending)
    monkeypatch.setattr(web_server, "_auto_archive_ticker_loop", _never_ending)
    monkeypatch.setattr(web_server, "run_reaper", _never_ending)
    monkeypatch.setattr(web_server, "PTY_REGISTRY", _FakePtyRegistry())
    monkeypatch.setattr(web_server, "_terminate_desktop_managed_gateway", lambda: None)


def _make_app():
    app = types.SimpleNamespace()
    app.state = types.SimpleNamespace()
    return app


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("env_value", "expected_started"),
    [
        ("1", True),  # exactly "1" opts in
        ("true", False),  # not "1" -> stays off
        ("0", False),  # explicit off
        ("", False),  # empty -> off
        (None, False),  # unset -> off (the default)
    ],
)
async def test_lifespan_bridge_listener_wiring(
    monkeypatch, env_value, expected_started
):
    _stub_lifespan_dependencies(monkeypatch)
    if env_value is None:
        monkeypatch.delenv("HERMES_DASHBOARD_BRIDGE", raising=False)
    else:
        monkeypatch.setenv("HERMES_DASHBOARD_BRIDGE", env_value)
    monkeypatch.setattr(
        "hermes_cli.dashboard_bridge.DashboardBridgeListener",
        _FakeBridgeListener,
    )

    app = _make_app()
    seen: dict = {}

    async with web_server._lifespan(app):  # type: ignore[arg-type]
        seen["listener"] = getattr(app.state, "bridge_listener", None)
        if seen["listener"] is not None:
            assert seen["listener"].started is True

    listener = seen["listener"]
    if expected_started:
        assert listener is not None
        assert listener.started is True
        # start() happened inside lifespan; stop() must happen in its finally.
        assert listener.stopped is True
        # The wiring lambda must be a factory for the backend (callable).
        assert callable(listener.backend_factory)
    else:
        assert listener is None
        assert not hasattr(app.state, "bridge_listener")


@pytest.mark.asyncio
async def test_lifespan_bridge_listener_start_failure_is_nonfatal(monkeypatch):
    """A failing listener start must not take down the whole backend."""
    _stub_lifespan_dependencies(monkeypatch)
    monkeypatch.setenv("HERMES_DASHBOARD_BRIDGE", "1")

    class _ExplodingListener(_FakeBridgeListener):
        def start(self):
            raise RuntimeError("pipe unavailable")

    monkeypatch.setattr(
        "hermes_cli.dashboard_bridge.DashboardBridgeListener",
        _ExplodingListener,
    )

    app = _make_app()

    async with web_server._lifespan(app):  # type: ignore[arg-type]
        assert getattr(app.state, "bridge_listener", None) is None
