import json
import random
import struct

from hermes_cli.dashboard_bridge.protocol import (
    BRIDGE_VERSION,
    MAX_FRAME_BYTES,
    BridgeProtocolError,
    decode_frame,
    encode_frame,
    event,
    response,
)


def _valid_frame(**overrides):
    base = {
        "version": BRIDGE_VERSION,
        "kind": "request",
        "requestId": "req-1",
        "type": "bridge.readiness",
        "payload": {},
    }
    base.update(overrides)
    return base


class TestFraming:
    def test_round_trip(self):
        frame = _valid_frame()
        assert decode_frame(encode_frame(frame)) == frame

    def test_round_trip_with_payload(self):
        frame = _valid_frame(
            kind="response",
            requestId="resp-1",
            type="bridge.hello",
            payload={"accepted": True, "version": 1},
        )
        assert decode_frame(encode_frame(frame)) == frame

    def test_length_prefix_correctness(self):
        frame = _valid_frame()
        raw = encode_frame(frame)
        (length,) = struct.unpack_from("<I", raw, 0)
        assert length == len(raw) - 4
        assert length == len(json.dumps(frame, separators=(",", ":")).encode("utf-8"))

    def test_truncated_frame(self):
        raw = encode_frame(_valid_frame())
        for truncate in (0, 3):
            try:
                decode_frame(raw[:truncate])
                assert False, "should have raised"
            except BridgeProtocolError:
                pass

    def test_zero_length(self):
        try:
            decode_frame(b"\x00\x00\x00\x00")
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_oversized_length(self):
        try:
            decode_frame(struct.pack("<I", MAX_FRAME_BYTES + 1) + b"x")
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_malformed_json(self):
        raw = struct.pack("<I", 5) + b"null\x00"
        try:
            decode_frame(raw)
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_oversized_payload(self):
        oversized = "x" * (MAX_FRAME_BYTES + 1)
        frame = _valid_frame(type="bridge.readiness", payload={"data": oversized})
        try:
            encode_frame(frame)
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_invalid_version(self):
        bad = _valid_frame(version=999)
        raw = struct.pack("<I", len(json.dumps(bad).encode("utf-8"))) + json.dumps(bad).encode("utf-8")
        try:
            decode_frame(raw)
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_missing_kind(self):
        try:
            decode_frame(encode_frame(_valid_frame(kind=None)))
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_unknown_kind(self):
        try:
            decode_frame(encode_frame(_valid_frame(kind="invalid")))
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_oversized_request_id(self):
        try:
            encode_frame(_valid_frame(requestId="x" * 129))
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_empty_type(self):
        try:
            encode_frame(_valid_frame(type=""))
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_non_dict_payload(self):
        try:
            encode_frame(_valid_frame(payload=[]))
            assert False, "should have raised"
        except BridgeProtocolError:
            pass

    def test_binary_utf8_valid(self):
        for _ in range(10):
            text = "".join(
                chr(code)
                for code in (
                    random.randrange(0x20, 0xD800),
                    random.randrange(0xE000, 0x10FFFF),
                )
            )
            frame = _valid_frame(payload={"text": text})
            assert decode_frame(encode_frame(frame)) == frame


class TestResponse:
    def test_ok_response(self):
        req = _valid_frame(requestId="req-1", type="bridge.readiness")
        resp = response(req, {"status": "ready"})
        assert resp["kind"] == "response"
        assert resp["requestId"] == "req-1"
        assert resp["type"] == "bridge.readiness"
        assert resp["payload"]["status"] == "ready"

    def test_error_response(self):
        req = _valid_frame(requestId="req-1", type="bridge.hello")
        resp = response(req, {"accepted": False}, error="handshake-rejected")
        assert resp["kind"] == "response"
        assert resp["payload"]["error"] == "handshake-rejected"


class TestEvent:
    def test_projection(self):
        ev = event("message.delta", "corr-1", {"delta": "hello"})
        assert ev["kind"] == "event"
        assert ev["type"] == "message.delta"
        assert ev["payload"]["correlationId"] == "corr-1"
        assert ev["payload"]["delta"] == "hello"

    def test_terminal_complete(self):
        ev = event("message.complete", "corr-1", {"status": "completed"})
        assert ev["payload"]["status"] == "completed"