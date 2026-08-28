"""Complete REAL FFI surface for the embedded runtime.

This package lives inside Hermes-CN-Core (the ``hermes_backend`` checkout) and
is the production replacement for the desktop repo's self-contained reference
package: instead of mirroring response shapes with canned logic, every entry
point delegates to the REAL Core implementation — with zero HTTP between the
embedded CPython interpreter and the Rust desktop process.

Two delegation paths
--------------------
REST (``/api/...``)
    The desktop's REST FFI registry (``ffi.rs``) maps each route prefix to a
    ``handle_*`` router here. Each router drives the real
    ``hermes_cli.web_server`` FastAPI application — including every mounted
    ``web_routers.*`` APIRouter — through an in-process ASGI call
    (``asgi_dispatch``). No socket is bound and no HTTP client exists; the
    loopback hop the subprocess dashboard needed is gone, the handlers are
    byte-for-byte the real ones.

Gateway (JSON-RPC over ``/api/ws``)
    Every method the frontend sends is dispatched through the real
    ``tui_gateway.server`` via ``server.dispatch(req, transport)`` — the exact
    entry the dashboard WebSocket (``tui_gateway/ws.py``) uses. The transport
    is a per-connection :class:`rust_transport.RustBridgeTransport` whose sink
    is a pyo3 function table Rust injects into ``sys.modules``
    (``_hermes_desktop_bridge``); agent turn events (``message.start`` /
    ``message.delta`` / ``message.complete`` / approvals / ...) stream to the
    WebView through it, and long-handler responses resolve the pending FFI
    call. ``prompt.submit`` therefore returns the real
    ``{"status": "streaming"}`` and the desktop no longer synthesizes stub
    turns.

Contract with the Rust bridge (src/embedded/)
- ``ffi_surface_version`` equals the Rust ``FFI_SURFACE_VERSION`` ("0.2.0").
- REST routes map to ``handle_<router>(params: dict, ctx: dict)``.
- Gateway JSON-RPC methods map to ``handle_rpc(method, params, ctx)``.
- Connection lifecycle: ``handle_gateway_connect`` / ``handle_gateway_disconnect``
  mirror ``tui_gateway.ws.handle_ws`` setup/teardown per WebView connection.
"""

from __future__ import annotations

import base64
import itertools
import json
import threading
import uuid
from pathlib import Path
from typing import Any

from . import asgi_dispatch, rust_transport

# Rust src/embedded/mod.rs::FFI_SURFACE_VERSION must match this exactly.
FFI_SURFACE_VERSION = "0.2.0"

# Module-level attribute the Rust bridge reads at startup
# (`api.getattr("ffi_surface_version")` — must be a str, not a function).
ffi_surface_version = FFI_SURFACE_VERSION

# How long a long-handler gateway RPC (session.list, model.options,
# complete.*, ...) may take before the embedded FFI call gives up. Matches the
# desktop's 120s RPC timeout so the webview never outlives the backend call.
GATEWAY_RPC_TIMEOUT_S = 120.0

_init_lock = threading.Lock()
_runtime_ready = False
_rid_counter = itertools.count(1)

_gateways_lock = threading.Lock()
_gateway_server: Any = None


class GatewayRpcError(ValueError):
    """A JSON-RPC error frame returned by the real gateway dispatcher.

    Raised (instead of returned in-band) so the Rust bridge answers the
    WebView with a JSON-RPC *error* frame — the same wire shape Core's
    ``_err(rid, ...)`` produces over the WebSocket — keeping the frontend's
    error and "session busy" retry paths identical to the HTTP-era behavior.
    """


# ── Runtime bootstrap ───────────────────────────────────────────────────


def _ensure_runtime(ctx: dict[str, Any]) -> None:
    """Pin the Core environment and wire the Rust event bridge exactly once."""
    global _runtime_ready
    if _runtime_ready:
        # Keep HERMES_HOME / HERMES_PROFILE in sync (profile switches re-enter
        # with a new ctx); Core resolves these dynamically per call.
        asgi_dispatch.ensure_core_environment(
            ctx.get("hermesHome") or ctx.get("hermes_home"),
            None,
            ctx.get("profile"),
        )
        return
    with _init_lock:
        if _runtime_ready:
            return
        try:  # injected by Rust at interpreter start (feature embedded-python)
            import _hermes_desktop_bridge as bridge  # type: ignore[import-not-found]

            rust_transport.set_sink(bridge.publish_event)
        except ImportError:
            # No Rust bridge (unit tests, selftest): events drop like they do
            # for a closed WebSocket transport.
            rust_transport.set_sink(None)
        asgi_dispatch.ensure_core_environment(
            ctx.get("hermesHome") or ctx.get("hermes_home"),
            ctx.get("sessionToken"),
            ctx.get("profile"),
        )
        _runtime_ready = True


def _gateway() -> Any:
    """The real ``tui_gateway.server`` module (lazily, thread-safe)."""
    global _gateway_server
    if _gateway_server is not None:
        return _gateway_server
    with _gateways_lock:
        if _gateway_server is None:
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from tui_gateway import server as _server

            _gateway_server = _server
    return _gateway_server


# ── Version / gateway metadata ──────────────────────────────────────────


def get_version() -> str:
    """GET /api/version — the REAL backend version (``hermes_cli.__version__``).

    The desktop version gate reads this via FFI instead of HTTP. Note: dev
    launches through run.py set ``VITE_HERMES_SKIP_VERSION_CHECK=1`` because
    the desktop bundle's baked ``EXPECTED_BACKEND_VERSION`` can legitimately
    lag the Core checkout it embeds.
    """
    try:
        from hermes_cli import __version__  # noqa: PLC0415 - lazy on purpose

        return str(__version__)
    except Exception:  # noqa: BLE001 - never crash the version gate
        return "unknown"


def get_gateway_config() -> dict[str, Any]:
    """GET /api/gateway — gateway runtime config (embedded in-process form)."""
    return {
        "version": get_version(),
        "transport": "embedded-ffi",
        "http": False,
        "ws": False,
    }


# Embedded-mode overrides applied on top of the REAL /api/status body: the
# gateway runs inside this process, so subprocess liveness fields (PID file,
# health URL) do not apply and would make the UI show "backend stopped".
_EMBEDDED_STATUS_OVERRIDES: dict[str, Any] = {
    "gateway_running": True,
    "gateway_pid": None,
    "gateway_health_url": None,
    "gateway_state": "running",
    "gateway_exit_reason": None,
    "gateway_busy": False,
    "gateway_drainable": False,
    "can_update_hermes": False,
    "auth_required": False,
    "mode": "embedded",
    "runtime": "in-process",
    "ffiSurfaceVersion": FFI_SURFACE_VERSION,
}


def get_status(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """GET /api/status — REAL status + embedded in-process gateway overrides."""
    _ensure_runtime(ctx)
    status: dict[str, Any]
    try:
        status = asgi_dispatch.dispatch_rest(
            "GET",
            "/api/status",
            query={"profile": ctx["profile"]} if ctx.get("profile") else None,
            hermes_home=ctx.get("hermesHome") or ctx.get("hermes_home"),
            session_token=ctx.get("sessionToken"),
            profile=ctx.get("profile"),
        )
    except Exception:  # noqa: BLE001 - status must never hard-fail the UI
        status = {}
    if not isinstance(status, dict):
        status = {}
    home_raw = ctx.get("hermesHome") or ctx.get("hermes_home")
    status.setdefault("version", get_version())
    status.setdefault("release_date", "embedded")
    if home_raw:
        home = Path(str(home_raw))
        status.setdefault("hermes_home", str(home))
        status.setdefault("config_path", str(home / "config.yaml"))
        status.setdefault("env_path", str(home / ".env"))
    status.update(_EMBEDDED_STATUS_OVERRIDES)
    return status


def get_config(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """GET /api/config — the REAL config view."""
    _ensure_runtime(ctx)
    result = asgi_dispatch.dispatch_rest(
        "GET",
        "/api/config",
        hermes_home=ctx.get("hermesHome") or ctx.get("hermes_home"),
        session_token=ctx.get("sessionToken"),
        profile=ctx.get("profile"),
    )
    return result if isinstance(result, dict) else {}


# ── Gateway connection lifecycle (mirror of tui_gateway.ws.handle_ws) ───


def _live_register(server: Any, transport: rust_transport.RustBridgeTransport) -> None:
    if not getattr(transport, "_live_registered", False):
        server.register_live_transport(transport)
        transport._live_registered = True  # noqa: SLF001 - internal flag


def handle_gateway_connect(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Rust ``open_embedded_gateway`` — the embedded ``ws.accept`` equivalent.

    Binds the per-connection transport, registers it for global broadcasts and
    emits the same ``gateway.ready`` frame the dashboard WebSocket sends on
    accept (the frontend's connect handshake depends on it).
    """
    _ensure_runtime(ctx)
    connection_id = str(
        (params or {}).get("connectionId")
        or ctx.get("connectionId")
        or "embedded"
    )
    transport = rust_transport.bind_connection(connection_id)
    server = _gateway()
    _live_register(server, transport)
    try:
        server._ensure_skin_watcher()  # noqa: SLF001 - same call ws.py makes
    except Exception:  # noqa: BLE001 - cosmetic, never block connect
        pass
    skin: Any = None
    try:
        skin = server.resolve_skin()
    except Exception:  # noqa: BLE001
        skin = None
    transport.write({
        "jsonrpc": "2.0",
        "method": "event",
        "params": {
            "type": "gateway.ready",
            "payload": {"skin": skin, "change_events": True},
        },
    })
    return {"ok": True, "connectionId": connection_id}


def handle_gateway_disconnect(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """Rust ``close_embedded_gateway`` — the ``handle_ws`` teardown path.

    Unregisters the live transport, releases wake-word state and reaps (or
    detaches) the sessions this connection owned — identical to the
    ``finally`` block of ``tui_gateway.ws.handle_ws``.
    """
    _ensure_runtime(ctx)
    connection_id = str(
        (params or {}).get("connectionId")
        or ctx.get("connectionId")
        or "embedded"
    )
    transport = rust_transport.unbind_connection(connection_id)
    if transport is not None:
        server = _gateway()
        try:
            server.unregister_live_transport(transport)
        except Exception:  # noqa: BLE001
            pass
        try:
            server._release_wake_for_transport(transport)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        try:
            server._close_sessions_for_transport(  # noqa: SLF001
                transport, end_reason="embedded_disconnect"
            )
        except Exception:  # noqa: BLE001
            pass
    return {"ok": True, "connectionId": connection_id}


# ── Gateway JSON-RPC delegation ─────────────────────────────────────────


def _gateway_rpc(method: str, params: Any, ctx: dict[str, Any]) -> Any:
    """Dispatch one gateway JSON-RPC method through the REAL server.

    Inline handlers return their response from ``dispatch``; long handlers
    (``session.list``/``resume``, ``model.options``, ``complete.*``,
    ``setup.status``, ...) schedule on Core's own pool and write the response
    through the transport — the pending slot below captures it either way so
    the FFI call can return the result synchronously, exactly like the
    WebSocket read loop does.
    """
    _ensure_runtime(ctx)
    server = _gateway()
    connection_id = str(ctx.get("connectionId") or "embedded")
    transport = rust_transport.transport_for(connection_id)
    if transport is None:
        # Self-heal: an FFI caller (Rust bootstrap, tests) dispatching outside
        # a WebView connection still gets a live-registered transport.
        transport = rust_transport.bind_connection(connection_id)
        _live_register(server, transport)
    else:
        _live_register(server, transport)

    rid = f"emb-{next(_rid_counter)}"
    slot = transport.expect_response(rid)
    request = {
        "jsonrpc": "2.0",
        "id": rid,
        "method": method,
        "params": params if isinstance(params, dict) else {},
    }
    try:
        response = server.dispatch(request, transport)
        if response is None:
            response = slot.wait(GATEWAY_RPC_TIMEOUT_S)
        if response is None:
            raise TimeoutError(
                f"gateway RPC {method} timed out after {GATEWAY_RPC_TIMEOUT_S:.0f}s"
            )
        if isinstance(response, dict) and response.get("error"):
            error = response["error"]
            message = str(
                error.get("message") if isinstance(error, dict) else error
                or "gateway error"
            )
            raise GatewayRpcError(message)
        return response.get("result") if isinstance(response, dict) else response
    finally:
        transport.abandon(rid)


# ── REST delegation helpers ─────────────────────────────────────────────

_META_KEYS = ("path", "method", "query", "body")


def _request_parts(params: dict[str, Any]) -> tuple[str, str, dict[str, Any], Any]:
    path = str(params.get("path") or "")
    method = str(params.get("method") or "GET").upper()
    query = params.get("query") if isinstance(params.get("query"), dict) else {}
    if isinstance(params.get("body"), tuple):
        body: Any = params["body"]  # pre-encoded (content_type, bytes)
    elif "body" in params:
        body = params["body"]
    else:
        body = {k: v for k, v in params.items() if k not in _META_KEYS}
    return path, method, query, body


def _rest(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """Generic pass-through: forward the merged request to the real app."""
    _ensure_runtime(ctx)
    path, method, query, body = _request_parts(params)
    return asgi_dispatch.dispatch_rest(
        method,
        path,
        query=query,
        body=body,
        hermes_home=ctx.get("hermesHome") or ctx.get("hermes_home"),
        session_token=ctx.get("sessionToken"),
        profile=ctx.get("profile"),
    )


def _multipart_upload(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """POST /api/upload — bridge the FFI JSON attachment into real multipart.

    The desktop's ``upload_file`` command delivers ``{filename,
    content_base64, session_id, ...}``; the real handler parses a
    ``multipart/form-data`` body with a ``file`` part and a ``session_id``
    field. Build that body here so the real handler (upload cache under
    ``<home>/uploads/<session_id>/``) serves the request unchanged.
    """
    _ensure_runtime(ctx)
    boundary = f"----hermesembedded{uuid.uuid4().hex}"
    fields: dict[str, str] = {}
    for key, value in params.items():
        if key in _META_KEYS or key in ("filename", "content_base64", "mime_type"):
            continue
        if value is not None:
            fields[str(key)] = str(value)
    fields.setdefault("session_id", str(params.get("session_id") or "default"))

    filename = str(params.get("filename") or "attachment")
    try:
        content = base64.b64decode(str(params.get("content_base64") or ""), validate=True)
    except Exception as exc:  # noqa: BLE001 - surface as 400-ish body
        return {"ok": False, "error": f"invalid content_base64: {exc}"}

    chunks: list[bytes] = []
    for name, value in fields.items():
        chunks.append(
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'
            f"{value}\r\n".encode("utf-8")
        )
    mime = str(params.get("mime_type") or "application/octet-stream")
    safe_filename = filename.replace('"', "").replace("\\", "_").replace("\r", "").replace("\n", "")
    chunks.append(
        (
            f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{safe_filename}"\r\n'
            f"Content-Type: {mime}\r\n\r\n"
        ).encode("utf-8")
    )
    chunks.append(content)
    chunks.append(f"\r\n--{boundary}--\r\n".encode("utf-8"))

    params = dict(params)
    params["body"] = (
        f"multipart/form-data; boundary={boundary}",
        b"".join(chunks),
    )
    return _rest(params, ctx)


def handle_model(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """/api/model/* — real REST routes; the legacy list route rides provider.models."""
    path = str(params.get("path") or "")
    if path.rstrip("/").endswith("/api/model/list"):
        # No REST /api/model/list exists in Core; the equivalent data is the
        # gateway's provider.models catalog.
        return _gateway_rpc("provider.models", params, ctx)
    if path.rstrip("/").endswith("/api/model/abc123") or _looks_like_model_id_route(path):
        # Legacy GET /api/model/{id}: served by the real model info route.
        params = dict(params)
        params["path"] = "/api/model/info"
    return _rest(params, ctx)


def _looks_like_model_id_route(path: str) -> bool:
    tail = path.rstrip("/").rsplit("/", 1)[-1]
    return (
        path.startswith("/api/model/")
        and tail
        and tail not in {
            "info", "list", "options", "set", "moa", "auxiliary",
            "recommended-default", "disconnect", "save_key",
        }
    )


def handle_analytics(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """/api/analytics/* — real routes (the legacy summary path maps to usage)."""
    path = str(params.get("path") or "")
    if path.rstrip("/").endswith("/api/analytics/summary"):
        params = dict(params)
        params["path"] = "/api/analytics/usage"
    return _rest(params, ctx)


def handle_gateway_restart(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """POST /api/gateway/restart — in-process gateway: nothing to restart.

    The real route stops/spawns a ``hermes gateway`` subprocess; the embedded
    runtime owns the gateway inside this process, so a "restart" is answered
    as a healthy no-op instead of tearing the agent host down.
    """
    _ensure_runtime(ctx)
    return {"ok": True, "embedded": True, "restarted": False}


# ── Legacy singular REST bridges (frontend uses the gateway RPC now) ────


def handle_session(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """/api/session/* — bridge onto the real gateway session methods."""
    path = str(params.get("path") or "")
    action = params.get("action")
    if path.endswith("/list") or action == "list":
        return _gateway_rpc("session.list", params, ctx)
    if action == "resume" or path.endswith("/resume"):
        return _gateway_rpc("session.resume", params, ctx)
    return _gateway_rpc("session.create", params, ctx)


def handle_prompt(params: dict[str, Any], ctx: dict[str, Any]) -> Any:
    """/api/prompt — bridge onto the real gateway prompt methods."""
    action = params.get("action") or (params.get("method") or "submit")
    if action == "abort":
        return _gateway_rpc("prompt.abort", params, ctx)
    return _gateway_rpc("prompt.submit", params, ctx)


# ── Unified FFI entry ───────────────────────────────────────────────────


def _loads(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, (str, bytes)) and value:
        try:
            loaded = json.loads(value)
        except (TypeError, ValueError):
            return {}
        return loaded if isinstance(loaded, dict) else {}
    # Rust serializes a missing params as the JSON string "null" → None here.
    return {}


def handle_rpc(method: str, params_json: Any, ctx_json: Any = "{}") -> Any:
    """Unified FFI entry: dispatch a REST router name or gateway method.

    Args:
        method: router name (``get_version``, ``handle_env``, ...) or a
            JSON-RPC gateway method (``session.create``, ``prompt.submit``, ...).
        params_json: JSON string (or parsed dict) with the request params.
        ctx_json: JSON string (or dict) with ``hermesHome`` / ``sessionToken``
            / ``profile`` / ``connectionId``.

    Returns:
        A JSON-serializable result (Rust round-trips through ``json.dumps``).
    """
    params = _loads(params_json)
    ctx = _loads(ctx_json)

    # Connection lifecycle entries (Rust-only; not in the WebView registry).
    if method == "gateway.connect":
        return handle_gateway_connect(params, ctx)
    if method == "gateway.disconnect":
        return handle_gateway_disconnect(params, ctx)

    router = _ROUTERS.get(method)
    if router is not None:
        return router(params, ctx)

    # The frontend-registered gateway methods ride the real dispatcher; the
    # Rust registry (GATEWAY_FFI_METHODS) gates what reaches this point.
    if method == "gateway.disconnected":
        return handle_gateway_disconnect(params, ctx)
    if method == "model.list":
        # No such gateway method in Core; the catalog lives in provider.models.
        return _gateway_rpc("provider.models", params, ctx)
    if method == "model.info":
        params = dict(params)
        params["path"] = "/api/model/info"
        params.setdefault("method", "GET")
        return _rest(params, ctx)
    return _gateway_rpc(method, params, ctx)


_ROUTERS: dict[str, Any] = {
    "get_version": lambda params, ctx: get_version(),
    "get_gateway_config": lambda params, ctx: get_gateway_config(),
    "get_status": get_status,
    "get_config": get_config,
    "handle_session": handle_session,
    "handle_prompt": handle_prompt,
    "handle_model": handle_model,
    "handle_analytics": handle_analytics,
    "handle_gateway_restart": handle_gateway_restart,
    # Direct pass-throughs: the real app serves these routes natively.
    "handle_sessions": _rest,
    "handle_profiles_exact": _rest,
    "handle_profiles": _rest,
    "handle_env": _rest,
    "handle_fs": _rest,
    "handle_logs": _rest,
    "handle_media": _rest,
    "handle_memory": _rest,
    "handle_mcp_servers": _rest,
    "handle_mcp": _rest,
    "handle_oauth_providers": _rest,
    "handle_upload": _multipart_upload,
    "handle_audio": _rest,
    "handle_config_schema": _rest,
    "handle_skills": _rest,
    "handle_tools": _rest,
    "handle_cron": _rest,
    "handle_messaging": _rest,
    "handle_pairing": _rest,
    "handle_git": _rest,
}

# Named aliases so `from hermes_embedded import handle_skills` (the public
# surface __init__ re-exports) keeps working for pass-through routers.
handle_skills = _rest
handle_tools = _rest
handle_cron = _rest
handle_messaging = _rest
handle_pairing = _rest
handle_git = _rest
handle_profiles = _rest
handle_mcp = _rest
