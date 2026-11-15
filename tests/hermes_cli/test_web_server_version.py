"""CN Desktop checks the backend version before it has session credentials."""

import asyncio
import json

import httpx
import pytest

import hermes_cli
from hermes_cli import web_server


@pytest.mark.parametrize("auth_required", [False, True], ids=["session-token", "auth-gate"])
def test_version_is_public_and_matches_runtime_identity(tmp_path, monkeypatch, auth_required):
    monkeypatch.setenv("HERMES_INSTALL_ROOT", str(tmp_path))
    monkeypatch.setattr(web_server.app.state, "bound_host", "127.0.0.1", raising=False)
    monkeypatch.setattr(web_server.app.state, "auth_required", auth_required, raising=False)

    async def exercise():
        transport = httpx.ASGITransport(app=web_server.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as client:
            # The real middleware still protects private routes without credentials.
            assert (await client.get("/api/env")).status_code == 401
            response = await client.get("/api/version")
            assert response.status_code == 200
            assert response.json() == {"version": hermes_cli.__version__, "name": "hermes-agent"}

            stamp = {"commit": "c" * 40, "baseVersion": "9.8.7", "source": "ci",
                     "updateMechanism": "external"}
            (tmp_path / "install-stamp.json").write_text(json.dumps(stamp), encoding="utf-8")
            response = await client.get("/api/version")
            assert response.status_code == 200
            assert response.json() == {"version": stamp["baseVersion"], "name": "hermes-agent"}
            assert response.json()["version"] == hermes_cli.__version__

    asyncio.run(exercise())
