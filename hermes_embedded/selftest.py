"""Self-test for the embedded FFI surface — run with:

    python -m hermes_embedded.selftest

Verifies the Rust ↔ Python contract without a Rust process: FFI surface
version, unified ``handle_rpc`` dispatch, the REAL REST app delegation
(config / env / sessions against a temp hermes home), and the real gateway
dispatcher path with a collecting sink (session lifecycle + a complete prompt
turn requires a configured model provider, so only shape/no-crash checks run
here — full agent turns are covered by the desktop E2E suite).

Set ``HERMES_EMBEDDED_SELFTEST_SKIP_CORE=1`` to skip the Core-dependent
checks (contract-only mode for environments without Core imports).
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

from .api import FFI_SURFACE_VERSION, GatewayRpcError, get_version, handle_rpc
from .rust_transport import RustBridgeTransport

EXPECTED_RUST_FFI_SURFACE_VERSION = "0.2.0"  # desktop src/embedded/mod.rs


def main() -> int:
    failures: list[str] = []

    if FFI_SURFACE_VERSION != EXPECTED_RUST_FFI_SURFACE_VERSION:
        failures.append(
            f"ffi_surface_version mismatch: python={FFI_SURFACE_VERSION} "
            f"rust={EXPECTED_RUST_FFI_SURFACE_VERSION}"
        )

    version = get_version()
    if not version or version == "unknown":
        failures.append(f"get_version() returned {version!r} (expected hermes_cli.__version__)")

    if handle_rpc("get_version", "{}", "{}") != version:
        failures.append("handle_rpc('get_version') did not return get_version()")

    if os.environ.get("HERMES_EMBEDDED_SELFTEST_SKIP_CORE") == "1":
        if failures:
            print("FAIL:", *failures, sep="\n  - ", file=sys.stderr)
            return 1
        print(
            f"OK: hermes_embedded contract selftest passed "
            f"(ffi_surface_version={FFI_SURFACE_VERSION}, version={version})"
        )
        return 0

    frames: list[dict] = []

    with tempfile.TemporaryDirectory(
        prefix="hermes-embedded-selftest-", ignore_cleanup_errors=True
    ) as home:
        ctx = json.dumps({"hermesHome": home, "sessionToken": "selftest-token", "profile": ""})
        Path(home, ".env").write_text("SELFTEST_KEY=v1\n", encoding="utf-8")
        Path(home, "logs").mkdir()
        Path(home, "logs", "agent.log").write_text("line1\nline2\n", encoding="utf-8")

        # ── Real REST app delegation ─────────────────────────────────────
        config = handle_rpc("get_config", "{}", ctx)
        if not isinstance(config, dict):
            failures.append(f"get_config -> {type(config).__name__} (expected dict)")

        env = handle_rpc("handle_env", json.dumps({"path": "/api/env", "method": "GET", "query": {}}), ctx)
        if not isinstance(env, dict) or env.get("SELFTEST_KEY", {}).get("is_set") is not True:
            failures.append(f"handle_env GET did not reflect the real .env: {env!r}")

        logs = handle_rpc(
            "handle_logs",
            json.dumps({"path": "/api/logs", "method": "GET", "query": {"file": "agent", "lines": "2"}}),
            ctx,
        )
        if not isinstance(logs, dict) or [str(l).rstrip("\r\n") for l in logs.get("lines", [])] != ["line1", "line2"]:
            failures.append(f"handle_logs did not tail the real log: {logs!r}")

        sessions = handle_rpc("handle_sessions", json.dumps({"path": "/api/sessions", "method": "GET", "query": {}}), ctx)
        if not isinstance(sessions, dict) or not isinstance(sessions.get("sessions"), list):
            failures.append(f"handle_sessions list shape wrong: {sessions!r}")

        status = handle_rpc("get_status", "{}", ctx)
        if status.get("mode") != "embedded" or status.get("gateway_running") is not True:
            failures.append(f"get_status missing embedded overrides: {status!r}")

        # A route without an FFI entry must never be served silently.
        try:
            handle_rpc("handle_fs", json.dumps({"path": "/api/unknown", "method": "GET", "query": {}}), ctx)
        except Exception:  # noqa: BLE001
            failures.append("handle_fs /api/unknown raised (expected the app's 404 JSON)")

        # ── Real gateway dispatcher ──────────────────────────────────────
        # Inject a collecting sink the way Rust does (via set_sink), then
        # exercise the connection lifecycle through handle_rpc.
        from . import rust_transport as _rt

        _rt.set_sink(lambda cid, frame: frames.append(json.loads(frame)) or True)

        connected = handle_rpc(
            "gateway.connect", json.dumps({"connectionId": "selftest"}), ctx
        )
        if not isinstance(connected, dict) or connected.get("ok") is not True:
            failures.append(f"gateway.connect failed: {connected!r}")
        if not any(f.get("params", {}).get("type") == "gateway.ready" for f in frames):
            failures.append("gateway.connect did not emit gateway.ready through the sink")

        listing = handle_rpc("session.list", "{}", json.dumps({**json.loads(ctx), "connectionId": "selftest"}))
        if not isinstance(listing, dict):
            failures.append(f"session.list -> {type(listing).__name__} (expected dict)")

        # input.detect_drop for an UNKNOWN session must come back as a real
        # JSON-RPC error frame ("session not found") — the exact wire shape
        # the WebSocket gateway produces; never a fabricated stub response.
        try:
            handle_rpc("input.detect_drop", json.dumps({"session_id": "s1", "text": "x"}),
                       json.dumps({**json.loads(ctx), "connectionId": "selftest"}))
            failures.append("input.detect_drop on unknown session did not error")
        except GatewayRpcError as exc:
            if "session not found" not in str(exc):
                failures.append(f"input.detect_drop unexpected error: {exc}")

        disconnected = handle_rpc("gateway.disconnect", json.dumps({"connectionId": "selftest"}), ctx)
        if not isinstance(disconnected, dict) or disconnected.get("ok") is not True:
            failures.append(f"gateway.disconnect failed: {disconnected!r}")

    # transport semantics (unchanged contract)
    # transport semantics (unchanged contract)
    _rt_frames: list[dict] = []
    transport = RustBridgeTransport("unit", lambda cid, frame: _rt_frames.append(json.loads(frame)) or True)
    if transport.write({"type": "message", "payload": {"text": "hi"}}) is not True:
        failures.append("transport.write did not return True")
    transport.close()
    if transport.write({"type": "x"}) is not False:
        failures.append("transport.write after close did not return False")
    if len(_rt_frames) != 1:
        failures.append(f"transport delivered {len(_rt_frames)} frames, expected 1")

    if failures:
        print("FAIL:", *failures, sep="\n  - ", file=sys.stderr)
        return 1
    print(
        f"OK: hermes_embedded selftest passed "
        f"(ffi_surface_version={FFI_SURFACE_VERSION}, core={version})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
