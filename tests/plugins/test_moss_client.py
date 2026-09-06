"""Request-contract tests for the repository-owned Moss HTTP client."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugins.tts.moss.client import (
    MODEL_TTS,
    MODEL_TTSD,
    MODEL_VOICE_GENERATOR,
    MossClient,
    MossError,
)


class FakeResponse:
    def __init__(self, *, data=None, content=b"audio", lines=(), status=200):
        self.status_code = status
        self.content = content
        self.text = ""
        self.reason = ""
        self._data = data
        self._lines = lines
        self.closed = False

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data

    def iter_lines(self, decode_unicode=False):  # noqa: ARG002
        yield from self._lines

    def close(self):
        self.closed = True


@pytest.fixture
def client() -> MossClient:
    return MossClient(api_key="test-key")


def test_speech_uses_documented_fields_and_full_model(client, monkeypatch):
    captured = SimpleNamespace(kwargs=None)

    def fake_post(*args, **kwargs):  # noqa: ARG001
        captured.kwargs = kwargs
        return FakeResponse(data={"status": "SUCCESS", "url": "https://cdn.test/a.mp3"})

    monkeypatch.setattr("plugins.tts.moss.client.requests.post", fake_post)
    result = client.speech(
        "你好",
        voice_id="voice-1",
        model="moss-tts",
        delivery_method="url",
        pause=0.8,
    )

    assert result["status"] == "SUCCESS"
    assert captured.kwargs["json"] == {
        "model": MODEL_TTS,
        "input": "你好 [pause 0.8s]",
        "voice_id": "voice-1",
        "response_format": "mp3",
        "delivery_method": "url",
    }


def test_dialogue_and_voice_design_use_full_models(client, monkeypatch):
    payloads = []

    def fake_post(*args, **kwargs):  # noqa: ARG001
        payloads.append(kwargs["json"])
        return FakeResponse(data={"task_id": "task-1"})

    monkeypatch.setattr("plugins.tts.moss.client.requests.post", fake_post)
    client.speakers([], [], model="moss-ttsd", async_mode=True)
    client.voice_generations(
        "温柔",
        "你好",
        model="moss-voice-generator",
        async_mode=True,
    )

    assert payloads[0]["model"] == MODEL_TTSD
    assert payloads[0]["async"] is True
    assert payloads[1]["model"] == MODEL_VOICE_GENERATOR
    assert payloads[1]["input"] == "你好"
    assert "version" not in payloads[0]
    assert "version" not in payloads[1]


def test_streaming_does_not_send_non_stream_fields(client, monkeypatch):
    captured = SimpleNamespace(kwargs=None)
    lines = [
        'data: {"type":"speech.created","sample_rate":48000}',
        'data: {"type":"speech.audio.done"}',
    ]

    def fake_post(*args, **kwargs):  # noqa: ARG001
        captured.kwargs = kwargs
        return FakeResponse(lines=lines)

    monkeypatch.setattr("plugins.tts.moss.client.requests.post", fake_post)
    events = list(client.speech_stream("hello", voice_id="voice-1"))

    assert [event["type"] for event in events] == [
        "speech.created",
        "speech.audio.done",
    ]
    assert captured.kwargs["json"] == {
        "model": MODEL_TTS,
        "input": "hello",
        "voice_id": "voice-1",
        "stream": True,
        "response_format": "pcm",
        "stream_format": "sse",
    }
    assert "delivery_method" not in captured.kwargs["json"]


def test_speech_rejects_missing_voice_and_invalid_pause(client):
    with pytest.raises(MossError, match="voice_id"):
        client.speech("hello")
    with pytest.raises(MossError, match="0.1 and 10.0"):
        client.speech("hello", voice_id="voice-1", pause=11)


def test_poll_task_uses_top_level_status(client, monkeypatch):
    response = FakeResponse(data={"id": "task-1", "status": "SUCCESS"})
    monkeypatch.setattr(
        "plugins.tts.moss.client.requests.get", lambda *args, **kwargs: response
    )

    assert client.poll_task("task-1", timeout=0)["status"] == "SUCCESS"


def test_streaming_rejects_malformed_sse(client, monkeypatch):
    response = FakeResponse(lines=["data: {not-json}"])
    monkeypatch.setattr(
        "plugins.tts.moss.client.requests.post", lambda *args, **kwargs: response
    )

    with pytest.raises(MossError, match="Invalid Moss SSE"):
        list(client.speech_stream("hello"))
    assert response.closed is True
