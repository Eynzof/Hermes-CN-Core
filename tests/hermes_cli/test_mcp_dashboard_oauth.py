"""Dashboard HTTP contract for hosted MCP OAuth."""

from unittest.mock import patch

import pytest


def _client():
    from starlette.testclient import TestClient

    from hermes_cli.web_server import app, _SESSION_HEADER_NAME, _SESSION_TOKEN

    client = TestClient(app)
    client.headers[_SESSION_HEADER_NAME] = _SESSION_TOKEN
    return client


@pytest.fixture(autouse=True)
def _clear_flows():
    from hermes_cli import web_server

    web_server._mcp_oauth_flows.clear()
    web_server.app.state.auth_required = False
    yield
    web_server._mcp_oauth_flows.clear()
    web_server.app.state.auth_required = False


def test_hosted_auth_start_returns_public_authorization_url(monkeypatch):
    from hermes_cli import web_server

    client = _client()
    client.post(
        "/api/mcp/servers",
        json={"name": "reports", "url": "https://mcp.example/mcp", "auth": "oauth"},
    )

    def fake_worker(flow, cfg):
        import asyncio

        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=s1"))

    monkeypatch.setattr(web_server, "_run_dashboard_mcp_oauth", fake_worker)
    with patch(
        "hermes_cli.dashboard_auth.prefix.resolve_public_url",
        return_value="https://agent.example",
    ):
        response = client.post("/api/mcp/servers/reports/auth")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "authorization_required"
    assert body["authorization_url"] == "https://idp.example/authorize?state=s1"
    flow = web_server._mcp_oauth_flows[body["flow_id"]]
    assert flow.redirect_uri == "https://agent.example/api/mcp/oauth/callback/reports"


def test_hosted_callback_bypasses_gated_cookie_auth(monkeypatch):
    import asyncio

    from starlette.testclient import TestClient

    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-gated",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/reports",
    )
    asyncio.run(
        flow.publish_authorization_url(
            "https://idp.example/authorize?state=expected"
        )
    )
    web_server._mcp_oauth_flows[flow.flow_id] = flow
    monkeypatch.setattr(web_server.app.state, "auth_required", True, raising=False)

    response = TestClient(web_server.app).get(
        "/api/mcp/oauth/callback/reports?code=abc&state=expected"
    )

    assert response.status_code == 200
    assert flow._callback == ("abc", "expected")


def test_hosted_auth_allows_same_server_name_in_different_profiles(tmp_path, monkeypatch):
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    profile_home = tmp_path / "profiles" / "work"
    profile_home.mkdir(parents=True)
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: profile_home)

    existing = DashboardOAuthFlow(
        flow_id="existing-default",
        server_name="reports",
        profile=None,
        hermes_home=str(tmp_path / "default"),
        redirect_uri="https://agent.example/callback/existing",
    )
    web_server._mcp_oauth_flows[existing.flow_id] = existing

    def fake_worker(flow, cfg):
        import asyncio

        asyncio.run(flow.publish_authorization_url("https://idp.example/authorize?state=work"))

    with patch("hermes_cli.mcp_config._get_mcp_servers", return_value={"reports": {"url": "https://mcp.example"}}), \
         patch.object(web_server, "_run_dashboard_mcp_oauth", fake_worker):
        response = _client().post("/api/mcp/servers/reports/auth?profile=work")

    assert response.status_code != 409




def test_flow_status_does_not_expose_authorization_code():
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="flow-status",
        server_name="reports",
        profile=None,
        hermes_home="/tmp/hermes-test",
        redirect_uri="https://agent.example/api/mcp/oauth/callback/flow-status",
    )
    flow.authorization_url = "https://idp.example/authorize"
    flow.status = "approved"
    flow._callback = ("secret-code", "secret-state")
    web_server._mcp_oauth_flows[flow.flow_id] = flow

    response = _client().get("/api/mcp/oauth/flows/flow-status")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "approved"
    assert "secret-code" not in response.text
    assert "secret-state" not in response.text


def test_logout_removes_only_selected_profile_tokens(tmp_path, monkeypatch):
    import asyncio
    from hermes_cli import web_server
    from hermes_cli.config import load_config, save_config
    from hermes_constants import get_hermes_home
    from mcp.shared.auth import OAuthToken
    from tools.mcp_oauth import HermesTokenStorage

    current_home = get_hermes_home()
    work_home = tmp_path / "work"
    work_home.mkdir()
    monkeypatch.setattr(web_server, "_resolve_profile_dir", lambda _name: work_home)
    current = HermesTokenStorage("reports", hermes_home=current_home)
    work = HermesTokenStorage("reports", hermes_home=work_home)
    for storage in (current, work):
        asyncio.run(storage.set_tokens(OAuthToken(access_token="test-access", token_type="Bearer")))
    with web_server._profile_scope("work"):
        save_config({"mcp_servers": {"reports": {"url": "https://mcp.example/mcp", "auth": "oauth", "enabled": True}}})

    response = _client().delete("/api/mcp/servers/reports/auth?profile=work")

    assert response.status_code == 200
    assert asyncio.run(work.get_tokens()) is None
    assert asyncio.run(current.get_tokens()) is not None
    with web_server._profile_scope("work"):
        assert load_config()["mcp_servers"]["reports"]["enabled"] is False


def test_cancel_acknowledges_only_after_worker_releases_server():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from hermes_cli import web_server
    from tools.mcp_dashboard_oauth import DashboardOAuthFlow

    flow = DashboardOAuthFlow(
        flow_id="cancel-cleanup", server_name="reports", profile=None,
        hermes_home="/tmp/hermes-test", redirect_uri="https://agent.example/callback",
    )
    web_server._mcp_oauth_flows[flow.flow_id] = flow
    cancelled = threading.Event()
    mark_error = flow.mark_error

    def begin_cancel(error):
        mark_error(error)
        cancelled.set()

    with patch.object(flow, "mark_error", side_effect=begin_cancel), ThreadPoolExecutor() as pool:
        response = pool.submit(_client().delete, f"/api/mcp/oauth/flows/{flow.flow_id}")
        try:
            assert cancelled.wait(5)
            assert not response.done(), "Cancellation must retain the busy state during worker cleanup"
        finally:
            flow.mark_worker_done()
        assert response.result(timeout=5).status_code == 200
        assert flow.worker_done
