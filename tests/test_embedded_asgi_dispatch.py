from __future__ import annotations

import asyncio
import threading

import anyio

from hermes_embedded import asgi_dispatch


def test_embedded_runner_reuses_root_task_and_anyio_worker() -> None:
    async def snapshot():
        worker_thread = await anyio.to_thread.run_sync(threading.get_ident)
        return asyncio.get_running_loop(), asyncio.current_task(), worker_thread

    first = asgi_dispatch._run(snapshot())
    second = asgi_dispatch._run(snapshot())

    assert second[0] is first[0]
    assert second[1] is first[1]
    assert second[2] == first[2]
