"""In-process dispatch into the real ``hermes_cli.web_server`` FastAPI app.

This is the Hard-FFI replacement for the loopback HTTP hop the desktop used to
make against the dashboard subprocess: instead of binding a port and sending an
HTTP request, ``dispatch_rest`` builds a synthetic ASGI scope and calls the
ASGI application callable directly. No socket is ever created, no listener is
started, and no HTTP client exists anywhere on this path — the FastAPI
application (with every ``web_routers.*`` APIRouter mounted on it) runs
in-process, so the embedded runtime serves the REAL Core handlers with their
real response shapes.

Response contract
-----------------
``dispatch_rest`` returns the parsed JSON body for JSON responses. Binary
responses (file downloads / media streams) are wrapped as
``{"data_url": "<mime>;base64,<...>"}`` — the same shape the dashboard's media
endpoint hands the frontend. Non-2xx JSON bodies (FastAPI ``{"detail": ...}``)
are returned as-is: the embedded REST envelope always reports HTTP 200 and the
frontend inspects the body, mirroring how the loopback proxy surfaced handler
errors.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import threading
from typing import Any

__all__ = ["dispatch_rest", "web_app", "ensure_core_environment"]

_import_lock = threading.Lock()
_app: Any = None
_app_session_token: str = ""


def ensure_core_environment(
    hermes_home: str | None,
    session_token: str | None,
    profile: str | None,
) -> None:
    """Pin the Core environment the HTTP headers/cookies used to carry.

    Must run before ``hermes_cli.web_server`` is imported: the dashboard
    resolves its session token at import time from
    ``HERMES_DASHBOARD_SESSION_TOKEN`` (falling back to a random secret), and
    every handler resolves the active hermes home / profile from
    ``HERMES_HOME`` / ``HERMES_PROFILE``.
    """
    if hermes_home:
        os.environ["HERMES_HOME"] = hermes_home
    if profile:
        os.environ["HERMES_PROFILE"] = profile
    if session_token:
        # Headers we inject below must match the token the app validates.
        os.environ.setdefault("HERMES_DASHBOARD_SESSION_TOKEN", session_token)


def app_session_token() -> str:
    """The token the imported app actually validates (``web_server._SESSION_TOKEN``).

    Read from the module rather than the environment: background threads
    (agent builds, cron tickers) may mutate ``os.environ`` at any time, and a
    stale env read would 401 every request after that.
    """
    web_app()
    return _app_session_token


def web_app() -> Any:
    """Import and return the real FastAPI application (lazily, thread-safe)."""
    global _app
    if _app is not None:
        return _app
    with _import_lock:
        if _app is None:
            # web_server imports emit deprecation noise; keep the FFI caller
            # log clean (the app itself is what matters here).
            import warnings

            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                from hermes_cli import web_server

            _app = web_server.app
            global _app_session_token
            _app_session_token = str(getattr(web_server, "_SESSION_TOKEN", ""))
    return _app


async def _call_app(app: Any, scope: dict, body: bytes) -> tuple[int, str, bytes]:
    """Drive one ASGI request/response cycle against ``app``."""
    status = 500
    headers: list[tuple[bytes, bytes]] = []
    out: bytearray = bytearray()
    response_started = False

    async def receive() -> dict:
        # The whole body is buffered up-front (desktop payloads are bounded by
        # the api_proxy 50 MiB cap), so a single receive then EOF is correct.
        if not receive._sent:
            receive._sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    receive._sent = False  # type: ignore[attr-defined]

    async def send(message: dict) -> None:
        nonlocal status, headers, response_started
        if message["type"] == "http.response.start":
            response_started = True
            status = int(message.get("status", 500))
            headers = [(k, v) for k, v in message.get("headers", [])]
        elif message["type"] == "http.response.body":
            chunk = message.get("body", b"")
            if chunk:
                out.extend(chunk)

    await app(scope, receive, send)
    if not response_started:
        raise RuntimeError("embedded dispatch: application never sent a response")
    content_type = ""
    for key, value in headers:
        if key.decode("latin-1").lower() == "content-type":
            content_type = value.decode("latin-1")
            break
    return status, content_type, bytes(out)


def dispatch_rest(
    method: str,
    path: str,
    query: dict[str, Any] | None = None,
    body: Any = None,
    hermes_home: str | None = None,
    session_token: str | None = None,
    profile: str | None = None,
) -> Any:
    """Run one REST request against the in-process Core app. No sockets.

    Returns the parsed JSON body (or a data-url wrapper for binary bodies).
    Raises on transport-level failures only — handler errors come back as the
    app's own error JSON so callers keep the dashboard's error semantics.
    """
    ensure_core_environment(hermes_home, session_token, profile)
    app = web_app()

    query_string = ""
    if query:
        from urllib.parse import urlencode

        query_string = urlencode(
            {k: v for k, v in query.items() if v is not None}, doseq=True
        )

    body_bytes = b""
    body_headers: list[tuple[bytes, bytes]] = []
    if isinstance(body, tuple) and len(body) == 2 and isinstance(body[1], (bytes, bytearray)):
        # Pre-encoded body: (content_type, raw_bytes) — used by the multipart
        # upload bridge.
        body_bytes = bytes(body[1])
        body_headers = [(b"content-type", body[0].encode("latin-1"))]
    elif body is not None and method.upper() not in ("GET", "HEAD"):
        if isinstance(body, (dict, list)):
            body_bytes = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            body_headers = [(b"content-type", b"application/json")]
        elif isinstance(body, (bytes, bytearray)):
            body_bytes = bytes(body)

    token = app_session_token()
    headers = [
        (b"host", b"localhost"),
        (b"accept", b"application/json"),
        (b"x-hermes-session-token", token.encode("utf-8")),
        (b"authorization", f"Bearer {token}".encode("utf-8")),
        *body_headers,
    ]

    scope = {
        "type": "http",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "method": method.upper(),
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "root_path": "",
        "headers": headers,
        "client": ("127.0.0.1", 0),
        "server": ("127.0.0.1", 0),
    }

    status, content_type, payload = _run(_call_app(app, scope, body_bytes))

    if content_type and "application/json" not in content_type:
        # Binary responses (file/media handlers): wrap as a data URL so the
        # FFI JSON boundary stays intact.
        if payload:
            mime = content_type.split(";")[0].strip() or "application/octet-stream"
            encoded = base64.b64encode(payload).decode("ascii")
            return {"data_url": f"data:{mime};base64,{encoded}", "status": status}
        return {"data_url": "", "status": status}

    if not payload:
        return {}
    try:
        return json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {"raw": base64.b64encode(payload).decode("ascii"), "status": status}


def _run(coro: Any) -> Any:
    """Run the ASGI cycle on a fresh event loop for this FFI call.

    FFI calls arrive on plain Rust blocking threads (no running loop), but a
    caller that already has a loop (tests) must not crash — fall back to a
    dedicated loop thread in that case.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - propagate to caller
            result["error"] = exc
        finally:
            done.set()

    threading.Thread(target=_worker, daemon=True).start()
    done.wait()
    if "error" in result:
        raise result["error"]
    return result["value"]
