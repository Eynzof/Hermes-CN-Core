"""RustBridgeTransport — the Python side of the embedded gateway transport.

Implements the same Transport Protocol as ``tui_gateway.transport.Transport``
(``write(obj) -> bool``, ``close()``) so ``tui_gateway.server.dispatch`` runs
against it with zero changes to Core — this is exactly the role the WebSocket
``WSTransport`` (``tui_gateway/ws.py``) plays for the dashboard's ``/api/ws``.
The only difference: instead of writing to a process pipe or a TCP WebSocket,
``write`` pushes event frames into the embedding Rust process through the
``_hermes_desktop_bridge`` module (a pyo3 function table Rust injects into
``sys.modules`` at interpreter start), and the Rust side fans them out to the
WebView as ``gateway-ws-message`` events.

Transport vs response routing
-----------------------------
Core answers RPC requests in two ways (see ``tui_gateway.server.dispatch``):

- inline handlers return the response dict from ``dispatch`` itself;
- long handlers (``_LONG_HANDLERS``: ``session.list``, ``session.resume``,
  ``model.options``, ``complete.*``, ``setup.status``, ...) are scheduled on
  the module thread pool and the pool worker writes the response with
  ``transport.write(response_frame)``.

The embedded FFI entry point (``api.handle_rpc``) must return *the result*
synchronously either way, so this transport splits its two frame kinds:

- frames carrying an ``id`` (RPC responses) resolve the pending per-request
  slot registered by ``expect_response`` — they never reach Rust (the Rust
  caller re-wraps the result with the WebView's own request id);
- every other frame is an event and is forwarded to the Rust bridge, tagged
  with this transport's ``connection_id`` so stale connections cannot receive
  a fresh connection's events.
"""

from __future__ import annotations

import json
import threading
from typing import Any, Callable

__all__ = [
    "RustBridgeTransport",
    "bind_connection",
    "unbind_connection",
    "transport_for",
    "connection_ids",
]

# Sink signature: callable(connection_id: str, frame_json: str) -> bool.
# api.py wires this to the Rust ``_hermes_desktop_bridge.publish_event``
# pyo3 function; tests can inject any callable.
Sink = Callable[[str, str], bool]

# Fallback sink used when Rust did not inject a bridge (e.g. unit tests that
# call handle_rpc directly). Events are dropped, which is the same semantics
# as a WebSocket transport whose peer went away.
def _null_sink(_connection_id: str, _frame_json: str) -> bool:
    return False


_active_sink: Sink = _null_sink

# connection_id -> RustBridgeTransport (one per WebView gateway connection).
_transports: dict[str, "RustBridgeTransport"] = {}
_transports_lock = threading.Lock()


def set_sink(sink: Sink | None) -> None:
    """Install the Rust bridge sink (called from ``api.init_runtime``)."""
    global _active_sink
    with _transports_lock:
        _active_sink = sink or _null_sink


class _ResponseSlot:
    """One-shot holder for a long-handler response frame."""

    __slots__ = ("_event", "frame")

    def __init__(self) -> None:
        self._event = threading.Event()
        self.frame: dict[str, Any] | None = None

    def resolve(self, frame: dict[str, Any]) -> None:
        self.frame = frame
        self._event.set()

    def wait(self, timeout: float) -> dict[str, Any] | None:
        return self.frame if self._event.wait(timeout) else None


class RustBridgeTransport:
    """Transport that pushes event frames into the embedding Rust process.

    Frames that carry an ``id`` are RPC responses for requests this transport
    itself issued (see ``expect_response``) and are matched to their pending
    slot instead of being forwarded. Everything else is an event frame in
    Core's ``_event_frame`` shape (``{"jsonrpc","method":"event","params":
    {type, session_id, payload}}``) and goes to the Rust bridge verbatim.
    """

    def __init__(self, connection_id: str, sink: Sink | None = None) -> None:
        self._connection_id = connection_id
        self._closed = False
        # Optional per-transport override; when unset write() resolves the
        # module-level active sink so a transport bound before the Rust bridge
        # is injected still forwards once the bridge exists.
        self._sink_override: Sink | None = sink
        self._pending: dict[str, _ResponseSlot] = {}
        self._pending_lock = threading.Lock()

    @property
    def connection_id(self) -> str:
        return self._connection_id

    # ── tui_gateway Transport protocol ───────────────────────────────────

    def write(self, obj: Any) -> bool:
        """Deliver one frame. Returns True when a consumer accepted it."""
        if self._closed:
            return False
        if isinstance(obj, (str, bytes)):
            try:
                obj = json.loads(obj)
            except (TypeError, ValueError):
                obj = {"type": "message", "payload": {"text": str(obj)}}
        if not isinstance(obj, dict):
            obj = {"type": "message", "payload": obj}

        rid = obj.get("id")
        if rid is not None:
            with self._pending_lock:
                slot = self._pending.pop(str(rid), None)
            if slot is not None:
                slot.resolve(obj)
                return True
            # A response for an unknown/stale request id: drop it rather than
            # leaking a response-shaped frame into the event stream.
            return False

        try:
            frame_json = json.dumps(obj, ensure_ascii=False, default=str)
        except (TypeError, ValueError):
            frame_json = json.dumps({"type": "message", "payload": repr(obj)})
        sink = self._sink_override if self._sink_override is not None else _active_sink
        return bool(sink(self._connection_id, frame_json))

    def close(self) -> None:
        self._closed = True
        with self._pending_lock:
            pending = list(self._pending.values())
            self._pending.clear()
        for slot in pending:
            slot.resolve({"jsonrpc": "2.0", "id": None,
                          "error": {"code": -32000, "message": "gateway connection closed"}})

    # ── Embedded request/response plumbing ───────────────────────────────

    def expect_response(self, rid: str) -> _ResponseSlot:
        """Register the slot that ``write`` resolves for request id ``rid``."""
        slot = _ResponseSlot()
        with self._pending_lock:
            self._pending[str(rid)] = slot
        return slot

    def abandon(self, rid: str) -> None:
        with self._pending_lock:
            self._pending.pop(str(rid), None)

    def __repr__(self) -> str:  # pragma: no cover - debug aid
        return (
            f"<RustBridgeTransport connection_id={self._connection_id!r} "
            f"closed={self._closed}>"
        )


def bind_connection(connection_id: str) -> "RustBridgeTransport":
    """Create (or reuse) the transport for one WebView gateway connection.

    The transport resolves the module-level sink at *write* time so frames
    flow even when the Rust bridge is injected after this transport was bound.
    """
    with _transports_lock:
        existing = _transports.get(connection_id)
        if existing is not None and not existing._closed:
            return existing
        transport = RustBridgeTransport(connection_id)
        _transports[connection_id] = transport
        return transport


def unbind_connection(connection_id: str) -> "RustBridgeTransport | None":
    """Close and drop the transport for a disconnected WebView connection."""
    with _transports_lock:
        transport = _transports.pop(connection_id, None)
    if transport is not None:
        transport.close()
    return transport


def transport_for(connection_id: str | None) -> "RustBridgeTransport | None":
    with _transports_lock:
        return _transports.get(connection_id or "")


def connection_ids() -> list[str]:
    with _transports_lock:
        return list(_transports.keys())
