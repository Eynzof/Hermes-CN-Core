"""End-to-end tests for Moss TTS.

These tests exercise the Moss ``/v1/audio/speech`` API documented in
``D:/moss.txt`` against the live service. They perform real network I/O,
download actual audio, and decode real SSE chunks.

They are SKIPPED by default; set ``MOSS_API_KEY`` to run them. The key can
be exported from the ``api_key`` field at the top of ``D:/moss.txt``.
"""
from __future__ import annotations

import base64
import json
import os
import re
from pathlib import Path

import pytest
import requests


MOSS_BASE_URL = "https://api.mosi.cn/v1"
DEFAULT_MOSS_MODEL = "moss-tts"
DEFAULT_MOSS_VERSION = "flash-20260626"
DEFAULT_MOSS_VOICE_ID = "94aa4989-c7e9-5007-ae42-ab401823e6c9"

# Capture the credential at import time; the root conftest's hermetic
# environment fixture clears API-key-shaped env vars before each test, so
# tests must re-inject it with monkeypatch inside the test body.
_MOSS_API_KEY = os.environ.get("MOSS_API_KEY", "").strip()
if not _MOSS_API_KEY:
    try:
        _moss_doc = Path("D:/moss.txt").read_text(encoding="utf-8")
        _match = re.search(r'"api_key"\s*:\s*"([^"]+)"', _moss_doc)
        if _match:
            _MOSS_API_KEY = _match.group(1)
    except Exception:
        _MOSS_API_KEY = ""


def _has_moss_creds() -> bool:
    return bool(_MOSS_API_KEY)


def _moss_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_MOSS_API_KEY}",
        "Content-Type": "application/json",
    }


def _assert_mp3_magic(path: Path) -> None:
    """Assert that *path* starts with an MP3 container magic sequence."""
    header = path.read_bytes()[:4]
    is_id3 = header.startswith(b"ID3")
    is_mpeg_sync = header[:2] == b"\xff\xfb"
    assert is_id3 or is_mpeg_sync, f"Expected MP3 magic, got {header!r}"


@pytest.mark.skipif(not _has_moss_creds(), reason="MOSS_API_KEY not set")
def test_moss_tts_sync_url_delivery(tmp_path: Path, monkeypatch) -> None:
    """Non-streaming ``delivery_method=url`` returns a downloadable MP3 URL."""
    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    output_path = tmp_path / "moss_url.mp3"
    payload = {
        "model": DEFAULT_MOSS_MODEL,
        "version": DEFAULT_MOSS_VERSION,
        "input": "欢迎使用 Moss API。[pause 0.8s]现在开始生成语音。",
        "voice_id": DEFAULT_MOSS_VOICE_ID,
        "response_format": "mp3",
        "delivery_method": "url",
    }

    resp = requests.post(
        f"{MOSS_BASE_URL}/audio/speech",
        headers=_moss_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    data = resp.json()
    assert data.get("status") == "SUCCESS"
    assert data.get("object") == "audio.speech"
    assert data.get("response_format") == "mp3"
    audio_url = data.get("url")
    assert isinstance(audio_url, str) and audio_url.startswith("http")

    audio_resp = requests.get(audio_url, timeout=60)
    audio_resp.raise_for_status()
    output_path.write_bytes(audio_resp.content)

    assert output_path.stat().st_size > 1024
    _assert_mp3_magic(output_path)


@pytest.mark.skipif(not _has_moss_creds(), reason="MOSS_API_KEY not set")
def test_moss_tts_sync_audio_delivery(tmp_path: Path, monkeypatch) -> None:
    """Non-streaming ``delivery_method=audio`` returns MP3 bytes directly."""
    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    output_path = tmp_path / "moss_audio.mp3"
    payload = {
        "model": DEFAULT_MOSS_MODEL,
        "version": DEFAULT_MOSS_VERSION,
        "input": "欢迎使用 Moss API。",
        "voice_id": DEFAULT_MOSS_VOICE_ID,
        "response_format": "mp3",
        "delivery_method": "audio",
    }

    resp = requests.post(
        f"{MOSS_BASE_URL}/audio/speech",
        headers=_moss_headers(),
        json=payload,
        timeout=60,
    )
    resp.raise_for_status()

    assert resp.headers.get("content-type", "").startswith("audio/")
    output_path.write_bytes(resp.content)

    assert output_path.stat().st_size > 1024
    _assert_mp3_magic(output_path)


@pytest.mark.skipif(not _has_moss_creds(), reason="MOSS_API_KEY not set")
def test_moss_tts_streaming_sse(monkeypatch) -> None:
    """Streaming ``stream=true`` yields base64 PCM chunks over SSE until done."""
    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    payload = {
        "model": DEFAULT_MOSS_MODEL,
        "version": DEFAULT_MOSS_VERSION,
        "input": "欢迎使用 Moss API。",
        "voice_id": DEFAULT_MOSS_VOICE_ID,
        "stream": True,
        "response_format": "pcm",
        "stream_format": "sse",
    }

    resp = requests.post(
        f"{MOSS_BASE_URL}/audio/speech",
        headers=_moss_headers(),
        json=payload,
        timeout=60,
        stream=True,
    )
    resp.raise_for_status()
    assert resp.headers.get("content-type", "").startswith("text/event-stream")

    audio_bytes = bytearray()
    saw_created = False
    saw_done = False
    sample_rate: int | None = None

    for raw_line in resp.iter_lines(decode_unicode=True):
        if not raw_line or not raw_line.startswith("data: "):
            continue
        event = json.loads(raw_line[len("data: "):])
        event_type = event.get("type")

        if event_type == "task.created":
            saw_created = True
            assert event.get("status") == "PROCESSING"
        elif event_type == "speech.created":
            sample_rate = event.get("sample_rate")
            assert isinstance(sample_rate, int)
            assert sample_rate > 0
        elif event_type == "speech.audio.delta":
            chunk = base64.b64decode(event["audio"])
            audio_bytes.extend(chunk)
        elif event_type == "speech.audio.done":
            saw_done = True
            break
        elif event_type == "error":
            pytest.fail(f"Moss streaming returned error event: {event}")

    assert saw_created, "Never received task.created event"
    assert sample_rate is not None, "Never received speech.created event"
    # 16-bit mono PCM: ~1 second of audio at 48kHz is ~96kB; expect at least a few KB.
    assert len(audio_bytes) > 2048, "Stream produced no audio bytes"
    assert saw_done, "Stream ended without speech.audio.done"
