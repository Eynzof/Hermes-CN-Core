"""Bounded framing and the allow-listed Dashboard bridge protocol."""

from __future__ import annotations

import json
from typing import Any

BRIDGE_VERSION = 1
MAX_FRAME_BYTES = 1024 * 1024
MAX_REQUEST_ID_LENGTH = 128
MAX_TYPE_LENGTH = 128
MAX_INPUT_LENGTH = 32_768
MAX_NONCE_LENGTH = 128

REQUEST_TYPES = frozenset(
    {
        "bridge.hello",
        "bridge.readiness",
        "conversation.submit",
        "conversation.events",
        "bridge.close",
    }
)
EVENT_TYPES = frozenset(
    {"message.delta", "message.complete", "error", "task.failed"}
)
TERMINAL_TYPES = frozenset({"message.complete", "error", "task.failed"})


class BridgeProtocolError(ValueError):
    """Raised when a frame violates the bounded typed protocol."""


def is_record(value: Any) -> bool:
    return isinstance(value, dict)


def bounded_string(value: Any, maximum: int, *, allow_empty: bool = False) -> bool:
    return (
        isinstance(value, str)
        and (allow_empty or bool(value))
        and len(value) <= maximum
    )


def validate_frame(value: Any, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> dict:
    if not is_record(value):
        raise BridgeProtocolError("invalid bridge frame")
    if value.get("version") != BRIDGE_VERSION:
        raise BridgeProtocolError("unsupported bridge version")
    if value.get("kind") not in {"request", "response", "event", "close"}:
        raise BridgeProtocolError("invalid bridge frame kind")
    if not bounded_string(value.get("requestId"), MAX_REQUEST_ID_LENGTH, allow_empty=True):
        raise BridgeProtocolError("invalid bridge request id")
    if not bounded_string(value.get("type"), MAX_TYPE_LENGTH):
        raise BridgeProtocolError("invalid bridge type")
    if not is_record(value.get("payload")):
        raise BridgeProtocolError("invalid bridge payload")
    encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
        "utf-8"
    )
    if not encoded or len(encoded) > max_frame_bytes:
        raise BridgeProtocolError("oversized bridge frame")
    return value


def encode_frame(value: dict, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> bytes:
    try:
        encoded = json.dumps(
            validate_frame(value, max_frame_bytes=max_frame_bytes),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise BridgeProtocolError("invalid bridge text") from exc
    if len(encoded) > 0xFFFFFFFF:
        raise BridgeProtocolError("oversized bridge frame")
    return len(encoded).to_bytes(4, "little") + encoded


def decode_frame(value: bytes, *, max_frame_bytes: int = MAX_FRAME_BYTES) -> dict:
    if len(value) < 4:
        raise BridgeProtocolError("truncated bridge frame")
    length = int.from_bytes(value[:4], "little")
    if length == 0 or length > max_frame_bytes:
        raise BridgeProtocolError("invalid bridge frame length")
    if len(value) != length + 4:
        raise BridgeProtocolError("truncated bridge frame")
    try:
        parsed = json.loads(value[4:].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BridgeProtocolError("invalid bridge json") from exc
    return validate_frame(parsed, max_frame_bytes=max_frame_bytes)


def response(request: dict, payload: dict, *, error: str | None = None) -> dict:
    body = dict(payload)
    if error is not None:
        body = {"error": error}
    return {
        "version": BRIDGE_VERSION,
        "kind": "response",
        "requestId": request.get("requestId", ""),
        "type": request.get("type", ""),
        "payload": body,
    }


def event(event_type: str, correlation_id: str, payload: dict) -> dict:
    body = dict(payload)
    body["correlationId"] = correlation_id
    return {
        "version": BRIDGE_VERSION,
        "kind": "event",
        "requestId": "",
        "type": event_type,
        "payload": body,
    }
