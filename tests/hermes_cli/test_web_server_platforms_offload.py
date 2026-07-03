"""[CN-fork] P-040 regression: /api/messaging/platforms must not wedge the loop.

``_messaging_platform_catalog()`` triggers platform-plugin discovery, which
imports every bundled IM adapter module on first call (discord.py alone can
take 10+ seconds on a cold install). Before P-040 that import ran inline in
the async handler — ON the event loop — so the desktop's very first boot
request queue (status/sessions/model info/...) sat behind it and the UI showed
a blank screen until the import finished.

These tests pin the two halves of the fix:

1. the handler runs the catalog build in an executor (a deliberately slow,
   blocking catalog must not delay a concurrent request on the same loop);
2. the lifespan warm helper resolves the plugin registry and is
   exception-isolated.
"""
import asyncio
import time

import pytest


@pytest.mark.asyncio
async def test_platforms_endpoint_does_not_block_concurrent_requests(
    monkeypatch, _isolate_hermes_home
):
    import hermes_cli.web_server as ws

    def slow_catalog():
        # Deliberately BLOCKING sleep: if the handler ran this on the event
        # loop, the concurrent /api/status request below could not complete
        # until it finished. In an executor it only occupies a worker thread.
        time.sleep(0.8)
        return ()

    monkeypatch.setattr(ws, "_messaging_platform_catalog", slow_catalog)

    import httpx

    transport = httpx.ASGITransport(app=ws.app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://testserver",
        headers={ws._SESSION_HEADER_NAME: ws._SESSION_TOKEN},
    ) as client:
        # Warm the probe endpoint first so the timed call below measures ONLY
        # event-loop availability, not the probe's own cold imports.
        warm = await client.get("/api/mcp-servers")
        assert warm.status_code == 200

        started = asyncio.get_running_loop().time()
        platforms_task = asyncio.create_task(
            client.get("/api/messaging/platforms")
        )
        # Give the platforms handler a chance to enter its slow section.
        await asyncio.sleep(0.05)
        probe_resp = await client.get("/api/mcp-servers")
        probe_elapsed = asyncio.get_running_loop().time() - started
        platforms_resp = await platforms_task

    assert probe_resp.status_code == 200
    assert platforms_resp.status_code == 200
    assert platforms_resp.json()["platforms"] == []
    # The wedge symptom was: every other request waits for the full catalog
    # build. With the executor offload the probe must return well before the
    # 0.8s sleep elapses.
    assert probe_elapsed < 0.75, (
        f"probe request took {probe_elapsed:.2f}s — it queued behind the "
        "platforms catalog build, so the offload regressed"
    )


def test_warm_platform_registry_resolves_plugins_and_swallows_errors(monkeypatch):
    import hermes_cli.web_server as ws

    calls = {"n": 0}

    class _Registry:
        def plugin_entries(self):
            calls["n"] += 1
            return []

    import gateway.platform_registry as reg_mod

    monkeypatch.setattr(reg_mod, "platform_registry", _Registry())
    ws._warm_platform_registry()
    assert calls["n"] == 1

    class _Boom:
        def plugin_entries(self):
            raise RuntimeError("cold import exploded")

    monkeypatch.setattr(reg_mod, "platform_registry", _Boom())
    # Must never raise — the warm is fire-and-forget at lifespan startup.
    ws._warm_platform_registry()
