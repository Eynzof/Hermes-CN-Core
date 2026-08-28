"""Real Windows Named Pipe end-to-end test with a fake backend."""

from __future__ import annotations

import struct
import threading
import time
import uuid

import pywintypes
import pytest
import sys
import win32file
import win32pipe

from hermes_cli.dashboard_bridge.listener import (
    DashboardBridgeListener,
    current_windows_sid,
    pipe_name_for_current_user,
)
from hermes_cli.dashboard_bridge.protocol import (
    BRIDGE_VERSION,
    MAX_FRAME_BYTES,
    BridgeProtocolError,
    decode_frame,
    encode_frame,
    response as mk_response,
)

pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="Named pipe bridge is Windows-only",
)


def _unique_pipe_name() -> str:
    return (
        r"\\.\pipe\hermes-dashboard-bridge-test-" + uuid.uuid4().hex[:12]
    )


def _read_exact(handle, size: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < size:
        try:
            _, data = win32file.ReadFile(handle, size - len(chunks))
        except pywintypes.error as exc:
            if exc.winerror in (109, 232):
                raise EOFError("pipe closed") from exc
            raise
        if not data:
            raise EOFError("pipe closed")
        chunks.extend(data)
    return bytes(chunks)


class PipeClient:
    def __init__(self, pipe_name: str) -> None:
        self.handle = win32file.CreateFile(
            pipe_name,
            win32file.GENERIC_READ | win32file.GENERIC_WRITE,
            0,
            None,
            win32file.OPEN_EXISTING,
            0,
            None,
        )

    def send(self, frame: dict) -> None:
        win32file.WriteFile(self.handle, encode_frame(frame))

    def recv(self) -> dict:
        header = _read_exact(self.handle, 4)
        length = int.from_bytes(header, "little")
        if length == 0 or length > MAX_FRAME_BYTES:
            raise BridgeProtocolError("invalid frame length")
        payload = _read_exact(self.handle, length)
        return decode_frame(header + payload)

    def close(self) -> None:
        try:
            win32file.CloseHandle(self.handle)
        except Exception:
            pass


class FakeBackend:
    def __init__(self, *, delay: float = 0.05) -> None:
        self._submitted: list[tuple[str, str, callable]] = []
        self._lock = threading.Lock()
        self._in_flight_corr: str | None = None
        self._delay = delay

    def readiness(self) -> dict:
        return {"status": "ready"}

    def submit(self, input_text: str, correlation: str, emit: callable) -> bool:
        with self._lock:
            if self._in_flight_corr is not None:
                return False
            self._in_flight_corr = correlation
        threading.Thread(
            target=self._run,
            args=(correlation, emit),
            daemon=True,
        ).start()
        return True

    def in_flight(self, correlation: str | None) -> bool:
        with self._lock:
            if correlation is None:
                return self._in_flight_corr is not None
            return self._in_flight_corr == correlation

    def close(self) -> None:
        with self._lock:
            self._in_flight_corr = None

    def _run(self, correlation: str, emit: callable) -> None:
        time.sleep(self._delay)
        emit("message.delta", {"delta": "acknowledged"})
        time.sleep(self._delay)
        emit("message.complete", {"status": "completed"})
        with self._lock:
            if self._in_flight_corr == correlation:
                self._in_flight_corr = None


class TestRealNamedPipe:
    def test_happy_path(self):
        pipe_name = _unique_pipe_name()
        listener = DashboardBridgeListener(
            FakeBackend,
            pipe_name=pipe_name,
        )
        try:
            listener.start()
            assert listener.wait_ready(timeout=3), "listener not ready"
            client = PipeClient(pipe_name)
            try:
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "h-1",
                        "type": "bridge.hello",
                        "payload": {
                            "client": "dlr",
                            "version": BRIDGE_VERSION,
                            "nonce": "test-nonce-12345",
                            "capabilities": ["conversation"],
                        },
                    }
                )
                resp = client.recv()
                assert resp["kind"] == "response"
                assert resp["type"] == "bridge.hello"
                assert resp["payload"]["accepted"] is True

                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "r-1",
                        "type": "bridge.readiness",
                        "payload": {},
                    }
                )
                resp = client.recv()
                assert resp["payload"]["status"] == "ready"

                correlation = "corr-1"
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "s-1",
                        "type": "conversation.submit",
                        "payload": {
                            "input": "Return only the word acknowledged.",
                            "correlationId": correlation,
                        },
                    }
                )
                resp = client.recv()
                assert resp["payload"]["accepted"] is True

                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "e-1",
                        "type": "conversation.events",
                        "payload": {"correlationId": correlation},
                    }
                )
                resp = client.recv()
                assert resp["payload"]["accepted"] is True
                events = [client.recv(), client.recv()]

                assert events[0]["type"] == "message.delta"
                assert events[0]["payload"]["correlationId"] == correlation
                assert events[0]["payload"]["delta"] == "acknowledged"
                assert events[1]["type"] == "message.complete"
                assert events[1]["payload"]["status"] == "completed"
                assert events[1]["payload"]["correlationId"] == correlation

                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "c-1",
                        "type": "bridge.close",
                        "payload": {},
                    }
                )
                client.recv()
            finally:
                client.close()
        finally:
            listener.stop()

    def test_handshake_required_for_methods(self):
        pipe_name = _unique_pipe_name()
        listener = DashboardBridgeListener(FakeBackend, pipe_name=pipe_name)
        try:
            listener.start()
            assert listener.wait_ready(timeout=3), "listener not ready"
            client = PipeClient(pipe_name)
            try:
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "h-1",
                        "type": "bridge.readiness",
                        "payload": {},
                    }
                )
                resp = client.recv()
                assert resp["payload"]["error"] == "handshake-required"
            finally:
                client.close()
        finally:
            listener.stop()

    def test_busy_rejects_second_submit(self):
        pipe_name = _unique_pipe_name()
        backend = FakeBackend(delay=0.5)
        listener = DashboardBridgeListener(lambda: backend, pipe_name=pipe_name)
        try:
            listener.start()
            assert listener.wait_ready(timeout=3), "listener not ready"
            client = PipeClient(pipe_name)
            try:
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "h-1",
                        "type": "bridge.hello",
                        "payload": {
                            "client": "dlr",
                            "version": BRIDGE_VERSION,
                            "nonce": "n",
                            "capabilities": ["conversation"],
                        },
                    }
                )
                client.recv()
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "r-1",
                        "type": "bridge.readiness",
                        "payload": {},
                    }
                )
                client.recv()
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "s-1",
                        "type": "conversation.submit",
                        "payload": {
                            "input": "hello",
                            "correlationId": "corr-1",
                        },
                    }
                )
                resp = client.recv()
                assert resp["payload"]["accepted"] is True
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "s-2",
                        "type": "conversation.submit",
                        "payload": {
                            "input": "second",
                            "correlationId": "corr-2",
                        },
                    }
                )
                resp = client.recv()
                assert resp["payload"]["accepted"] is False
                assert resp["payload"]["error"] == "busy"
            finally:
                client.close()
        finally:
            listener.stop()

    def test_unknown_method(self):
        pipe_name = _unique_pipe_name()
        listener = DashboardBridgeListener(FakeBackend, pipe_name=pipe_name)
        try:
            listener.start()
            assert listener.wait_ready(timeout=3), "listener not ready"
            client = PipeClient(pipe_name)
            try:
                client.send(
                    {
                        "version": BRIDGE_VERSION,
                        "kind": "request",
                        "requestId": "u-1",
                        "type": "unknown.method",
                        "payload": {},
                    }
                )
                resp = client.recv()
                assert resp["payload"]["error"] == "unknown-method"
            finally:
                client.close()
        finally:
            listener.stop()

    def test_current_windows_sid_parses_real_sid(self):
        """The production pipe name derives from the real user SID, not ''.

        Regression: scanning the SID token from index 0 treated the leading
        "S" as a non-digit and truncated every SID to "", so
        ``pipe_name_for_current_user()`` pointed at a hash of the empty string
        and ``DashboardBridgeListener()`` raised during construction.
        """
        sid = current_windows_sid()
        assert sid.startswith("S-")
        # A real SID is never just "S-" and carries numeric authority + subauths.
        assert len(sid) > 4
        assert all(
            char.isdigit() or char == "-" for char in sid.split("-", 1)[1]
        )

        name = pipe_name_for_current_user()
        assert name.startswith(r"\\.\pipe\hermes-dashboard-v2-")
        suffix = name.rsplit("-", 1)[-1]
        assert len(suffix) == 16
        assert all(c in "0123456789abcdef" for c in suffix)