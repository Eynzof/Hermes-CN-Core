"""Tests for the ``/api/auth/me`` identity-probe endpoint.

The endpoint returns ``{"user": null}`` (200) when unauthenticated, or
``{"user": {user_id, email, ...}}`` when a valid session cookie is present.
"""
from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from hermes_cli import web_server
from hermes_cli.dashboard_auth.registry import clear_providers, register_provider
from tests.hermes_cli.conftest_dashboard_auth import StubAuthProvider
from tests.hermes_cli.test_dashboard_auth_middleware import _complete_stub_login


@pytest.fixture
def gated_app():
    """Configure web_server.app for gated mode + register the stub provider."""
    clear_providers()
    register_provider(StubAuthProvider())
    prev_host = getattr(web_server.app.state, "bound_host", None)
    prev_port = getattr(web_server.app.state, "bound_port", None)
    prev_required = getattr(web_server.app.state, "auth_required", None)
    web_server.app.state.bound_host = "fly-app.fly.dev"
    web_server.app.state.bound_port = 443
    web_server.app.state.auth_required = True
    client = TestClient(web_server.app, base_url="https://fly-app.fly.dev")
    yield client
    clear_providers()
    web_server.app.state.bound_host = prev_host
    web_server.app.state.bound_port = prev_port
    web_server.app.state.auth_required = prev_required


def test_auth_me_returns_user_null_when_unauthenticated(gated_app):
    """No cookies → 200 with ``{"user": null}``, not 401."""
    r = gated_app.get("/api/auth/me")
    assert r.status_code == 200, (
        f"Expected 200 with user: null, got {r.status_code}: {r.text}"
    )
    assert r.json() == {"user": None}


def test_auth_me_returns_session_when_authenticated(gated_app):
    """After completing the stub OAuth round trip, the session data is returned."""
    _complete_stub_login(gated_app)
    r = gated_app.get("/api/auth/me")
    assert r.status_code == 200
    body = r.json()
    user = body.get("user")
    assert user is not None, f"Expected user data, got {body}"
    assert user["user_id"] == "stub-user-1"
    assert user["email"] == "stub@example.test"
    assert user["display_name"] == "Stub User"
    assert user["provider"] == "stub"
    assert user["org_id"] == "stub-org-1"
    assert "expires_at" in user


def test_auth_me_is_public_under_loopback_middleware():
    """In loopback mode (auth_required=False), the endpoint still returns
    200 with ``{"user": null}`` when no token is present."""
    clear_providers()
    prev_required = getattr(web_server.app.state, "auth_required", None)
    prev_host = getattr(web_server.app.state, "bound_host", None)
    web_server.app.state.bound_host = "127.0.0.1"
    web_server.app.state.auth_required = False
    try:
        client = TestClient(web_server.app, base_url="http://127.0.0.1")
        r = client.get("/api/auth/me")
        assert r.status_code == 200, (
            f"Expected 200 in loopback mode, got {r.status_code}: {r.text}"
        )
        assert r.json() == {"user": None}
    finally:
        web_server.app.state.auth_required = prev_required
        web_server.app.state.bound_host = prev_host


def test_auth_me_is_not_blocked_by_legacy_auth_middleware():
    """In loopback mode (auth_required=False), the legacy ``auth_middleware``
    (which checks ``_PUBLIC_API_PATHS`` via path prefix) must let
    ``/api/auth/me`` through instead of returning 401."""
    # Loopback mode uses the legacy ``auth_middleware``. The path
    # ``/api/auth/me`` is in ``PUBLIC_API_PATHS``, which the middleware
    # checks via ``path in _PUBLIC_API_PATHS``. Without the entry, the
    # middleware's ``if path.startswith("/api/") ... return 401`` would fire.
    from hermes_cli.dashboard_auth.public_paths import PUBLIC_API_PATHS

    assert "/api/auth/me" in PUBLIC_API_PATHS, (
        "/api/auth/me must be in PUBLIC_API_PATHS to bypass the legacy "
        "auth_middleware in loopback mode"
    )
