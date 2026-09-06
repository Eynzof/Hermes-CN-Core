"""hermes_embedded — the REAL in-process embedding entry package.

This package lives in Hermes-CN-Core (merged from the desktop repo's
self-contained reference package). The desktop's Rust process embeds CPython
and calls these functions directly through the CPython C ABI (pyo3); **no
HTTP/WS is used between Rust and Python**.

Contract with the Rust bridge (desktop ``src/embedded/``):
- ``ffi_surface_version`` must equal the Rust ``FFI_SURFACE_VERSION``.
- REST routes map to ``handle_<router>(params: dict, ctx: dict)`` — each one
  drives the REAL ``hermes_cli.web_server`` FastAPI application in-process.
- Gateway JSON-RPC methods map through ``handle_rpc(method, params, ctx)``
  into the REAL ``tui_gateway.server`` dispatcher; agent events stream back
  through ``rust_transport.RustBridgeTransport`` into the Rust event bridge.
- Connection lifecycle: ``handle_gateway_connect`` / ``handle_gateway_disconnect``.
"""

from .api import (
    FFI_SURFACE_VERSION,
    ffi_surface_version,
    get_version,
    handle_gateway_connect,
    handle_gateway_disconnect,
    handle_rpc,
    handle_session,
    handle_prompt,
    handle_model,
    handle_skills,
    handle_tools,
    handle_mcp,
    handle_cron,
    handle_messaging,
    handle_pairing,
    handle_git,
    handle_profiles,
    handle_analytics,
    get_gateway_config,
    get_status,
    get_config,
)
from .rust_transport import (
    RustBridgeTransport,
    bind_connection,
    unbind_connection,
    transport_for,
)

__all__ = [
    "FFI_SURFACE_VERSION",
    "ffi_surface_version",
    "get_version",
    "handle_gateway_connect",
    "handle_gateway_disconnect",
    "handle_rpc",
    "handle_session",
    "handle_prompt",
    "handle_model",
    "handle_skills",
    "handle_tools",
    "handle_mcp",
    "handle_cron",
    "handle_messaging",
    "handle_pairing",
    "handle_git",
    "handle_profiles",
    "handle_analytics",
    "get_gateway_config",
    "get_status",
    "get_config",
    "RustBridgeTransport",
    "bind_connection",
    "unbind_connection",
    "transport_for",
]
