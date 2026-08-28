"""Windows Named Pipe listener for the typed Dashboard bridge.

The pipe is local-only, named after the current Windows user, and guarded by a
DACL that grants access to the current user and SYSTEM only. Each accepted
connection runs its own read thread; all writes are serialized through a
per-connection queue so the conversation worker can emit events from another
thread. The protocol handler is transport-agnostic (``handle_connection``) so
tests can drive it with an in-memory transport on any platform.
"""

from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable

from hermes_cli.dashboard_bridge.protocol import (
    BRIDGE_VERSION,
    EVENT_TYPES,
    MAX_FRAME_BYTES,
    MAX_INPUT_LENGTH,
    MAX_NONCE_LENGTH,
    MAX_REQUEST_ID_LENGTH,
    REQUEST_TYPES,
    TERMINAL_TYPES,
    BridgeProtocolError,
    bounded_string,
    decode_frame,
    encode_frame,
    event,
    is_record,
    response,
)

_log = logging.getLogger(__name__)

BackendFactory = Callable[..., Any]


def current_windows_sid() -> str:
    if sys.platform != "win32":
        return ""
    output = subprocess.run(
        ["whoami.exe", "/user"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    match = None
    if output.stdout:
        for line in output.stdout.splitlines():
            if "S-" in line:
                start = line.find("S-")
                token = line[start:].strip()
                # Skip the leading "S-" prefix: every character of the numeric
                # SID is a digit or "-". Scanning from index 0 would stop at
                # the "S" itself and truncate the whole token to "".
                end = len(token)
                for index in range(2, len(token)):
                    char = token[index]
                    if not (char.isdigit() or char == "-"):
                        end = index
                        break
                match = token[:end]
                break
    if not match or not match.startswith("S-"):
        raise RuntimeError("Windows user SID unavailable")
    return match


def pipe_name_for_current_user() -> str:
    if sys.platform != "win32":
        return f"\\\\.\\pipe\\hermes-dashboard-v2-test-{os.getpid()}"
    sid = current_windows_sid()
    sid_hash = hashlib.sha256(sid.encode("utf-8")).hexdigest()[:16]
    return f"\\\\.\\pipe\\hermes-dashboard-v2-{sid_hash}"


class Transport:
    """Minimal blocking transport interface used by the protocol handler."""

    def recv(self) -> bytes:  # pragma: no cover - interface
        raise NotImplementedError

    def send(self, frame: dict) -> bool:  # pragma: no cover - interface
        raise NotImplementedError

    def close(self) -> None:  # pragma: no cover - interface
        raise NotImplementedError


def _read_exact(handle: Any, size: int) -> bytes:
    import win32file

    chunks = bytearray()
    while len(chunks) < size:
        try:
            _, data = win32file.ReadFile(handle, size - len(chunks))
        except Exception as exc:
            winerror = getattr(exc, "winerror", None)
            if winerror in (109, 232):  # broken pipe / no data
                raise EOFError("pipe closed") from exc
            raise
        if not data:
            raise EOFError("pipe closed")
        chunks.extend(data)
    return bytes(chunks)


class WindowsPipeTransport(Transport):
    """Blocking pywin32 transport with serialized bounded writes."""

    def __init__(self, handle: Any, send_frame: Callable[[dict], None]) -> None:
        self._handle = handle
        self._send_frame = send_frame
        self._write_lock = threading.Lock()
        self._closed = False

    def recv(self) -> bytes:
        header = _read_exact(self._handle, 4)
        length = int.from_bytes(header, "little")
        if length == 0 or length > MAX_FRAME_BYTES:
            raise BridgeProtocolError("invalid bridge frame length")
        payload = _read_exact(self._handle, length)
        return header + payload

    def send(self, frame: dict) -> bool:
        with self._write_lock:
            if self._closed:
                return False
            try:
                self._send_frame(frame)
                return True
            except Exception:
                _log.debug("Dashboard bridge write failed", exc_info=True)
                return False

    def close(self) -> None:
        import win32file

        with self._write_lock:
            self._closed = True
        try:
            win32file.CloseHandle(self._handle)
        except Exception:
            pass


def _valid_hello(frame: dict) -> bool:
    payload = frame.get("payload") or {}
    if payload.get("client") != "dlr":
        return False
    if payload.get("version") != BRIDGE_VERSION:
        return False
    nonce = payload.get("nonce")
    if not bounded_string(nonce, MAX_NONCE_LENGTH):
        return False
    capabilities = payload.get("capabilities")
    if not isinstance(capabilities, list):
        return False
    allowed = {"conversation"}
    return set(capabilities).issubset(allowed) and bool(capabilities)


class BridgeSession:
    """One accepted pipe connection's protocol state."""

    def __init__(self, backend: Any, transport: Transport) -> None:
        self.backend = backend
        self._transport = transport
        self._closed = False
        self._in_flight_correlation: str | None = None
        self._events_enabled = False
        self._pending_events: list[dict] = []
        self._terminal_seen = False
        self._lock = threading.RLock()
        self._condition = threading.Condition(self._lock)

    def submit(self, input_text: str, correlation: str) -> bool:
        with self._lock:
            if self._in_flight_correlation is not None:
                return False
            self._in_flight_correlation = correlation
        with self._lock:
            self._events_enabled = False
            self._pending_events.clear()
            self._terminal_seen = False
        accepted = self.backend.submit(
            input_text,
            correlation,
            lambda event_type, payload: self._emit(
                event_type, correlation, payload
            ),
        )
        if not accepted:
            with self._lock:
                if self._in_flight_correlation == correlation:
                    self._in_flight_correlation = None
        return accepted

    def in_flight(self, correlation: str | None) -> bool:
        with self._lock:
            return (
                self._in_flight_correlation is not None
                and (correlation is None or self._in_flight_correlation == correlation)
            )

    def enable_events(self, correlation: str) -> None:
        with self._lock:
            if self._closed or self._in_flight_correlation != correlation:
                return
            self._events_enabled = True
            pending = list(self._pending_events)
            self._pending_events.clear()
            terminal_seen = self._terminal_seen
        for frame in pending:
            self._transport.send(frame)
        if terminal_seen:
            with self._condition:
                if self._in_flight_correlation == correlation:
                    self._in_flight_correlation = None
                    self._condition.notify_all()

    def wait_for_terminal(self, correlation: str) -> None:
        with self._condition:
            self._condition.wait_for(
                lambda: self._closed or self._in_flight_correlation != correlation
            )

    def _emit(self, event_type: str, correlation: str, payload: dict) -> None:
        if event_type not in EVENT_TYPES:
            return
        frame = event(event_type, correlation, payload)
        terminal = event_type in TERMINAL_TYPES
        with self._lock:
            if self._closed or self._in_flight_correlation != correlation:
                return
            if terminal:
                self._terminal_seen = True
            if not self._events_enabled:
                self._pending_events.append(frame)
                return
        self._transport.send(frame)
        if terminal:
            with self._condition:
                if self._in_flight_correlation == correlation:
                    self._in_flight_correlation = None
                    self._condition.notify_all()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
        try:
            self.backend.close()
        except Exception:
            _log.debug("Dashboard bridge backend close failed", exc_info=True)


def handle_connection(
    backend_factory: BackendFactory,
    transport: Transport,
    *,
    max_frame_bytes: int = MAX_FRAME_BYTES,
) -> None:
    """Serve one pipe connection until close, error, or EOF."""
    backend: Any = None
    session: BridgeSession | None = None
    try:
        backend = backend_factory()
        while True:
            raw: bytes | None = None
            try:
                raw = transport.recv()
            except (EOFError, OSError):
                return
            if not raw:
                return
            frame = decode_frame(raw, max_frame_bytes=max_frame_bytes)
            if frame.get("kind") != "request":
                transport.send(response(frame, {"error": "not-a-request"}))
                continue
            request_type = frame.get("type", "")
            if request_type not in REQUEST_TYPES:
                transport.send(response(frame, {"error": "unknown-method"}))
                continue
            if request_type == "bridge.hello":
                if session is not None:
                    transport.send(response(frame, {"error": "duplicate-handshake"}))
                    continue
                if not _valid_hello(frame):
                    transport.send(
                        response(frame, {"accepted": False, "error": "handshake-rejected"})
                    )
                    continue
                session = BridgeSession(backend, transport)
                transport.send(response(frame, {"accepted": True, "version": BRIDGE_VERSION}))
                continue
            if session is None:
                transport.send(response(frame, {"error": "handshake-required"}))
                continue
            if request_type == "bridge.readiness":
                transport.send(response(frame, session.backend.readiness()))
                continue
            if request_type == "conversation.submit":
                payload = frame.get("payload") or {}
                input_text = payload.get("input")
                correlation = payload.get("correlationId")
                if not bounded_string(input_text, MAX_INPUT_LENGTH):
                    transport.send(
                        response(frame, {"accepted": False, "error": "invalid-submit"})
                    )
                    continue
                if not bounded_string(correlation, MAX_REQUEST_ID_LENGTH):
                    transport.send(
                        response(frame, {"accepted": False, "error": "invalid-submit"})
                    )
                    continue
                accepted = session.submit(str(input_text), str(correlation))
                transport.send(
                    response(
                        frame,
                        {"accepted": accepted}
                        if accepted
                        else {"accepted": False, "error": "busy"},
                    )
                )
                continue
            if request_type == "conversation.events":
                payload = frame.get("payload") or {}
                correlation = payload.get("correlationId")
                if session.in_flight(correlation):
                    transport.send(response(frame, {"accepted": True}))
                    session.enable_events(str(correlation))
                    session.wait_for_terminal(str(correlation))
                else:
                    transport.send(
                        response(
                            frame,
                            {"accepted": False, "error": "no-in-flight"},
                        )
                    )
                continue
            if request_type == "bridge.close":
                transport.send(response(frame, {"accepted": True}))
                return
    except BridgeProtocolError as exc:
        _log.debug("Dashboard bridge protocol error: %s", exc)
    finally:
        if session is not None:
            session.close()
        transport.close()


class DashboardBridgeListener:
    """Owns the user-scoped Named Pipe and one accept thread.

    On non-Windows hosts ``start()`` is a no-op (the bridge is Windows-only),
    so importing this module stays safe on CI.
    """

    def __init__(
        self,
        backend_factory: BackendFactory,
        *,
        pipe_name: str | None = None,
        max_frame_bytes: int = MAX_FRAME_BYTES,
    ) -> None:
        self._backend_factory = backend_factory
        self._pipe_name = pipe_name or pipe_name_for_current_user()
        self._max_frame_bytes = max_frame_bytes
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def pipe_name(self) -> str:
        return self._pipe_name

    def wait_ready(self, timeout: float | None = 3.0) -> bool:
        """Block until the listener has created the named pipe."""
        return self._ready.wait(timeout=timeout)

    def start(self) -> None:
        if sys.platform != "win32":
            return
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._serve_windows,
            name="dashboard-bridge-listener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        # Break a blocking ConnectNamedPipe/ReadFile wait so the accept thread
        # can observe the stop flag.
        try:
            import win32file

            win32file.CreateFile(
                self._pipe_name,
                win32file.GENERIC_READ | win32file.GENERIC_WRITE,
                0,
                None,
                win32file.OPEN_EXISTING,
                0,
                None,
            )
        except Exception:
            pass
        if self._thread is not None:
            self._thread.join(timeout=5)

    def _security_attributes(self) -> Any:
        import ntsecuritycon
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32con.TOKEN_QUERY
        )
        owner = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        acl = win32security.ACL()
        for sid in (owner, win32security.ConvertStringSidToSid("S-1-5-18")):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION,
                0,
                ntsecuritycon.FILE_ALL_ACCESS,
                sid,
            )
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorOwner(owner, False)
        descriptor.SetSecurityDescriptorDacl(True, acl, False)
        descriptor.SetSecurityDescriptorControl(
            win32security.SE_DACL_PROTECTED,
            win32security.SE_DACL_PROTECTED,
        )
        attributes = win32security.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = descriptor
        return attributes

    def _serve_windows(self) -> None:
        import win32file
        import win32pipe

        while not self._stop.is_set():
            try:
                handle = win32pipe.CreateNamedPipe(
                    self._pipe_name,
                    win32pipe.PIPE_ACCESS_DUPLEX,
                    win32pipe.PIPE_TYPE_BYTE
                    | win32pipe.PIPE_READMODE_BYTE
                    | win32pipe.PIPE_WAIT,
                    255,
                    65536,
                    65536,
                    0,
                    self._security_attributes(),
                )
                self._ready.set()
            except Exception:
                if self._stop.is_set():
                    return
                _log.debug("Dashboard bridge pipe create failed", exc_info=True)
                return
            try:
                connected = win32pipe.ConnectNamedPipe(handle, None)
            except Exception:
                connected = 0
            if self._stop.is_set():
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                return
            if connected not in (0, 535):  # ERROR_PIPE_CONNECTED = 535
                try:
                    win32file.CloseHandle(handle)
                except Exception:
                    pass
                continue

            def respond(frame: dict, _handle=handle) -> None:
                try:
                    win32file.WriteFile(_handle, encode_frame(frame))
                except Exception:
                    _log.debug("Dashboard bridge write failed", exc_info=True)

            transport = WindowsPipeTransport(handle, respond)
            threading.Thread(
                target=handle_connection,
                args=(self._backend_factory, transport),
                kwargs={"max_frame_bytes": self._max_frame_bytes},
                name="dashboard-bridge-conn",
                daemon=True,
            ).start()

