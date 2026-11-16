"""Webhook profile isolation and route authentication."""

import asyncio
from unittest.mock import MagicMock, patch
import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from gateway.platforms.webhook import WebhookAdapter
from tests.gateway.test_webhook_adapter import _create_app, _make_adapter, _github_signature


class TestMultiplexProfileWebhookAuthentication:
    @staticmethod
    def _configure_profiles(adapter, tmp_path, monkeypatch):
        runner = MagicMock()
        runner.config.multiplex_profiles = True
        runner._profile_name_for_source.return_value = None
        adapter.gateway_runner = runner
        monkeypatch.setattr(
            "hermes_cli.profiles.profiles_to_serve",
            lambda multiplex: [
                ("default", tmp_path),
                ("worker", tmp_path / "profiles" / "worker"),
                ("other", tmp_path / "profiles" / "other"),
            ],
        )

    @staticmethod
    def _app(adapter):
        app = _create_app(adapter)
        app.router.add_post(
            "/p/{profile}/webhooks/{route_name}",
            adapter._handle_webhook,
        )
        return app

    @staticmethod
    def _headers(body: bytes, secret: str):
        return {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _github_signature(body, secret),
            # Stop after successful authentication without dispatching an
            # agent run; the route accepts only pull_request events.
            "X-GitHub-Event": "push",
        }

    @pytest.mark.asyncio
    async def test_route_secret_is_bound_to_named_profile(
        self, tmp_path, monkeypatch
    ):
        route_secret = "worker-route-secret-abc123"
        adapter = _make_adapter(
            routes={
                "gh": {
                    "profile": "worker",
                    "secret": route_secret,
                    "events": ["pull_request"],
                    "prompt": "PR: {action}",
                }
            },
            host="127.0.0.1",
        )
        self._configure_profiles(adapter, tmp_path, monkeypatch)
        body = b'{"action":"opened"}'
        headers = self._headers(body, route_secret)

        async with TestClient(TestServer(self._app(adapter))) as cli:
            accepted = await cli.post(
                "/p/worker/webhooks/gh",
                data=body,
                headers=headers,
            )
            assert accepted.status == 200
            assert (await accepted.json())["status"] == "ignored"

            wrong_profile = await cli.post(
                "/p/other/webhooks/gh",
                data=body,
                headers=headers,
            )
            assert wrong_profile.status == 404

            default_profile = await cli.post(
                "/webhooks/gh",
                data=body,
                headers=headers,
            )
            assert default_profile.status == 404

    @pytest.mark.asyncio
    async def test_routed_profile_skills_resolve_under_that_profile(
        self, tmp_path, monkeypatch
    ):
        """A /p/<profile>/ route's ``skills:`` must load from that profile's
        skills/ dir (#67277). Before the fix the lookup ran with no profile
        scope, so it scanned the launch profile and logged "Skill not found".
        """
        import agent.skill_commands as sc_mod

        worker = tmp_path / "profiles" / "worker"
        skill_dir = worker / "skills" / "worker-only"
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            "---\nname: worker-only\ndescription: w\n---\n\nBody of worker-only.\n"
        )
        (worker / "config.yaml").write_text("{}\n")
        (worker / ".env").write_text("")
        monkeypatch.setattr(
            "hermes_cli.profiles.get_profile_dir", lambda name: tmp_path / "profiles" / name
        )
        route_secret = "worker-route-secret-abc123"
        adapter = _make_adapter(
            routes={
                "gh": {
                    "profile": "worker",
                    "secret": route_secret,
                    "prompt": "PR: {action}",
                    "skills": ["worker-only"],
                }
            },
            host="127.0.0.1",
        )
        self._configure_profiles(adapter, tmp_path, monkeypatch)
        seen = []

        async def _capture(request, route_config, route_name, profile, payload, prompt,
                           event_type, delivery_id, now):
            seen.append((profile, prompt))
            return web.Response(status=202)

        # Rendering-only boundary; the authority correctly refuses an unserved
        # secondary home (covered independently by the native profile tests).
        adapter._dispatch_agent_run = _capture
        body = b'{"action":"opened"}'
        headers = {
            "Content-Type": "application/json",
            "X-Hub-Signature-256": _github_signature(body, route_secret),
        }
        with (
            patch.object(sc_mod, "_skill_commands_by_key", {}),
        ):
            async with TestClient(TestServer(self._app(adapter))) as cli:
                resp = await cli.post("/p/worker/webhooks/gh", data=body, headers=headers)
                assert resp.status == 202
                await asyncio.sleep(0.05)
        assert len(seen) == 1
        assert seen[0][0] == "worker"
        assert "Body of worker-only." in seen[0][1]


def test_route_profile_validation_fails_closed():
    assert WebhookAdapter._route_allows_profile({}, None) is True
    assert WebhookAdapter._route_allows_profile(
        {"profile": "worker"}, "worker"
    ) is True
    assert WebhookAdapter._route_allows_profile(
        {"profile": "worker"}, "other"
    ) is False
    for malformed in (None, "", "   ", 123, ["worker"]):
        assert WebhookAdapter._route_allows_profile(
            {"profile": malformed}, "worker"
        ) is False
