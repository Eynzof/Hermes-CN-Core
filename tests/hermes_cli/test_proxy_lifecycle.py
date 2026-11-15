"""The proxy command owns one real listener and releases it on shutdown."""

import asyncio
from types import SimpleNamespace

import aiohttp
import pytest

from hermes_cli.proxy import server
from hermes_constants import get_hermes_home
from tests._fixtures._port_lock_probe import child_can_claim_port


@pytest.mark.parametrize(("host", "origin_status"), [("127.0.0.1", 200), ("0.0.0.0", 403)])
@pytest.mark.platforms("linux", "macos", "windows")
def test_proxy_run_server_binds_serves_and_shuts_down(monkeypatch, unused_tcp_port, host, origin_status):
    async def exercise():
        started = asyncio.Event()
        original_start = server.web.TCPSite.start

        async def observe_start(site):
            await original_start(site)
            started.set()

        monkeypatch.setattr(server.web.TCPSite, "start", observe_start)
        adapter = SimpleNamespace(name="fixture", display_name="Fixture", is_authenticated=lambda: True)
        url = f"http://127.0.0.1:{unused_tcp_port}"
        for _ in range(2):
            started.clear()
            stop = asyncio.Event()
            task = asyncio.create_task(server.run_server(adapter, host=host, port=unused_tcp_port, shutdown_event=stop))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                async with aiohttp.ClientSession() as client:
                    async with client.get(f"{url}/health") as response:
                        assert response.status == 200
                        assert await response.json() == {"status": "ok", "upstream": "Fixture", "authenticated": True}
                    async with client.get(f"{url}/health", headers={"Origin": url}) as response:
                        assert response.status == origin_status
                assert not await asyncio.to_thread(child_can_claim_port, get_hermes_home(), unused_tcp_port)
            finally:
                stop.set()
                await asyncio.wait_for(task, timeout=5)
            with pytest.raises(OSError):
                await asyncio.open_connection("127.0.0.1", unused_tcp_port)
            assert await asyncio.to_thread(child_can_claim_port, get_hermes_home(), unused_tcp_port)
        rebound = await asyncio.start_server(lambda reader, writer: writer.close(), "127.0.0.1", unused_tcp_port)
        rebound.close()
        await rebound.wait_closed()

    asyncio.run(exercise())
