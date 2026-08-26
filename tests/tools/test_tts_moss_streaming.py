"""Unit tests for MossStreamer (plugins/tts/moss/streaming.py).

Uses a fake SSE event iterator — no network.
"""
from __future__ import annotations

import base64

import pytest

from plugins.tts.moss.streaming import MossStreamer
from tools.tts_streaming import _STREAM_SENTENCE_BYTE_CAP


class FakeStreamClient:
    def __init__(self, events):
        self.events = events
        self.last_kwargs = None

    def speech_stream(self, text, **kwargs):
        self.last_kwargs = {"text": text, **kwargs}
        return iter(self.events)


def _delta(data: bytes) -> dict:
    return {"type": "speech.audio.delta", "audio": base64.b64encode(data).decode()}


def _make_streamer(monkeypatch, events, *, available=True):
    fake = FakeStreamClient(events)
    from plugins.tts.moss import streaming as streaming_module

    monkeypatch.setattr(streaming_module, "build_client", lambda cfg=None: fake)
    monkeypatch.setattr(streaming_module, "_key_present", lambda: available)
    return MossStreamer({"moss": {"voice_id": "v1"}}, {"voice_id": "v1"}), fake


class TestEventFlow:
    def test_yields_pcm_in_order_and_stops_at_done(self, monkeypatch):
        events = [
            {"type": "task.created", "status": "PROCESSING"},
            {"type": "speech.created", "sample_rate": 48000, "channels": 1, "bit_depth": 16},
            _delta(b"\x01\x02\x03\x04"),
            _delta(b"\x05\x06"),
            {"type": "speech.audio.done"},
        ]
        streamer, fake = _make_streamer(monkeypatch, events)
        chunks = list(streamer.stream("hello"))
        assert chunks == [b"\x01\x02\x03\x04", b"\x05\x06"]
        assert fake.last_kwargs["response_format"] == "pcm"
        assert fake.last_kwargs["voice_id"] == "v1"

    def test_sample_rate_cross_check_warns_when_mismatched(self, monkeypatch, caplog):
        events = [
            {"type": "speech.created", "sample_rate": 44100},
            _delta(b"\x01"),
            {"type": "speech.audio.done"},
        ]
        streamer, _ = _make_streamer(monkeypatch, events)
        with caplog.at_level("WARNING", logger="plugins.tts.moss.streaming"):
            list(streamer.stream("hello"))
        assert "44100" in caplog.text and "48000" in caplog.text

    def test_pinned_sample_rate_is_class_constant(self):
        assert MossStreamer.sample_rate == 48000
        assert MossStreamer.channels == 1
        assert MossStreamer.sample_width == 2


class TestByteCap:
    def test_truncates_when_cap_exceeded(self, monkeypatch):
        big = b"\x00" * (_STREAM_SENTENCE_BYTE_CAP // 2)
        events = [_delta(big), _delta(big), {"type": "speech.audio.done"}]
        streamer, _ = _make_streamer(monkeypatch, events)
        total = sum(len(c) for c in streamer.stream("hello"))
        assert total <= _STREAM_SENTENCE_BYTE_CAP


class TestErrorEvent:
    def test_error_event_raises(self, monkeypatch):
        events = [_delta(b"\x01"), {"type": "error", "message": "boom"}]
        streamer, _ = _make_streamer(monkeypatch, events)
        with pytest.raises(Exception, match="stream error event"):
            list(streamer.stream("hello"))


class TestAvailability:
    def test_available_with_key(self, monkeypatch):
        streamer, _ = _make_streamer(monkeypatch, [])
        assert streamer.available() is True

    def test_unavailable_without_key(self, monkeypatch):
        streamer, _ = _make_streamer(monkeypatch, [], available=False)
        assert streamer.available() is False
