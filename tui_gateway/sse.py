"""SSE+POST transport for the tui_gateway JSON-RPC server (P-009).

Mirrors :mod:`tui_gateway.ws` but split across two HTTP routes:

* ``GET /api/v2/events`` — one-way Server-Sent Events stream the client
  subscribes to once at start-up. The server emits a ``client_id`` frame
  before anything else; the client carries that id on every subsequent
  RPC POST so the server can route the response back to the right
  stream.
* ``POST /api/v2/rpc`` — one-shot JSON-RPC request. The route handler
  looks up the client's :class:`SSETransport` by id, calls
  :func:`tui_gateway.server.dispatch` with it bound, and either returns
  the inline response or — for long handlers — returns a sentinel and
  lets the pool worker push the real response via the SSE stream.

Why SSE+POST instead of WS
--------------------------
Tauri webviews can't open ``ws://`` to localhost when the page is
served via ``tauri://``; CORS rejects the upgrade. SSE works because
EventSource is plain GET, and the desktop's Rust SSE proxy can
forward the stream from the privileged side. The trade-off is that
EventSource can't set custom headers, so the session token rides the
query string and the route handler authenticates it directly (the
path is on :data:`hermes_cli.web_server._PUBLIC_API_PATHS` so the
middleware doesn't 401 it first).
"""

from __future__ import annotations

import asyncio
import orjson
import logging
import uuid
from typing import AsyncIterator, Dict, Optional

_log = logging.getLogger(__name__)

# Max seconds an off-loop write will block waiting for the event loop
# to enqueue the frame. Same role as ``_WS_WRITE_TIMEOUT_S`` in
# tui_gateway.ws — protects pool workers from a wedged client.
_SSE_WRITE_TIMEOUT_S = 10.0

# Idle ping interval (seconds). Proxies (and the browser itself) close
# idle EventSource connections after ~30-60s, so we emit a SSE comment
# frame every interval.
_SSE_PING_INTERVAL_S = 15.0

# Bound the per-client queue to a sensible cap so a slow consumer can't
# OOM the process. A misbehaving client gets a dropped frame and the
# transport closes itself; the client will reconnect.
_SSE_QUEUE_MAX = 1024


class SSETransport:
    """Per-SSE-connection transport.

    Implements the :class:`tui_gateway.transport.Transport` protocol so
    :func:`tui_gateway.server.dispatch` can write back through it the
    same way it writes through :class:`tui_gateway.ws.WSTransport`.

    ``write`` is callable from any thread; it marshals frames onto the
    owning event loop via :func:`asyncio.run_coroutine_threadsafe` so
    the SSE generator can pull them out in order.
    """

    __slots__ = ("client_id", "_loop", "_queue", "_closed")

    def __init__(self, client_id: str, loop: asyncio.AbstractEventLoop) -> None:
        self.client_id = client_id
        self._loop = loop
        self._queue: asyncio.Queue[Optional[str]] = asyncio.Queue(maxsize=_SSE_QUEUE_MAX)
        self._closed = False

    def write(self, obj: dict) -> bool:
        if self._closed:
            return False

        # Serialize outside the loop coroutine so dict-encoding errors
        # surface to the caller (a programming bug we want in the crash
        # log, not a silent disconnect — see StdioTransport.write).
        line = orjson.dumps(obj).decode('utf-8')

        try:
            on_loop = asyncio.get_running_loop() is self._loop
        except RuntimeError:
            on_loop = False

        if on_loop:
            try:
                self._queue.put_nowait(line)
                return True
            except asyncio.QueueFull:
                self._closed = True
                _log.debug("sse queue full on loop; closing client %s", self.client_id)
                return False

        # Off-loop: marshal onto the loop. We use put() (not put_nowait)
        # so back-pressure is honoured; if the loop is dead the
        # run_coroutine_threadsafe future will time out and we close.
        try:
            fut = asyncio.run_coroutine_threadsafe(self._queue.put(line), self._loop)
            fut.result(timeout=_SSE_WRITE_TIMEOUT_S)
            return not self._closed
        except Exception as exc:
            self._closed = True
            _log.debug("sse write failed: %s", exc)
            return False

    async def stream(self) -> AsyncIterator[str]:
        """Yield SSE-framed text chunks until the transport closes.

        The very first chunk is the ``client_id`` frame the client
        needs to reach this transport via :func:`POST /api/v2/rpc`. The
        gateway then yields whatever the dispatcher writes, plus a
        ``: ping`` comment every :data:`_SSE_PING_INTERVAL_S` so idle
        proxies don't drop the connection.
        """
        yield f"event: client_id\ndata: {orjson.dumps({'client_id': self.client_id}).decode('utf-8')}\n\n"

        while not self._closed:
            try:
                line = await asyncio.wait_for(
                    self._queue.get(),
                    timeout=_SSE_PING_INTERVAL_S,
                )
            except asyncio.TimeoutError:
                yield ": ping\n\n"
                continue
            if line is None:
                break
            yield f"data: {line}\n\n"

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        # Drop a sentinel so the stream coroutine wakes and exits even
        # if no frames are pending. Best-effort: if the loop is gone,
        # nothing to wake.
        try:
            asyncio.run_coroutine_threadsafe(self._queue.put(None), self._loop)
        except Exception:
            pass


# Registry of live SSE connections, keyed by client_id. Populated when
# /api/v2/events opens a stream and removed on disconnect. POST /api/v2/rpc
# looks up the transport here before calling dispatch().
SSE_CLIENTS: Dict[str, SSETransport] = {}


def new_client_id() -> str:
    """Generate a fresh, opaque client id."""
    return uuid.uuid4().hex
