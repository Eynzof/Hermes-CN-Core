"""Live E2E tests for the Moss plugin (plugins/tts/moss).

Gated on a Moss API key (skipif no key). The key is read from
``MOSS_API_KEY`` or a key file whose path is configured via
``MOSS_KEY_FILE`` (the same fallback MossClient uses). These tests perform
real network I/O — they are skipped by default in CI without a key.

Coverage: provider synthesize (mp3/wav), streaming PCM (48 kHz),
list_voices, clone → reuse, dialogue, voice design, async poll.
"""
from __future__ import annotations

import os
import re
import time
from pathlib import Path

import pytest

from moss_tts import is_mp3, is_wav

_MOSS_API_KEY = os.environ.get("MOSS_API_KEY", "").strip()
if not _MOSS_API_KEY:
    key_file = os.environ.get("MOSS_KEY_FILE", "").strip()
    if key_file:
        try:
            text = Path(key_file).read_text(encoding="utf-8")
            m = re.search(r'"api_key"\s*:\s*"([^"]+)"', text)
            _MOSS_API_KEY = (m.group(1) if m else text.strip()).strip()
        except Exception:
            _MOSS_API_KEY = ""

pytestmark = pytest.mark.skipif(
    not _MOSS_API_KEY,
    reason="MOSS_API_KEY / MOSS_KEY_FILE not set",
)


@pytest.fixture
def provider(monkeypatch):
    from plugins.tts.moss.provider import MossProvider

    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    return MossProvider()


def test_provider_synthesize_mp3_and_wav(provider, tmp_path):
    mp3 = str(tmp_path / "out.mp3")
    written = provider.synthesize("欢迎使用 Moss 插件端到端测试。", mp3)
    assert Path(written).is_file() and Path(written).stat().st_size > 1024
    assert is_mp3(written)

    wav = str(tmp_path / "out.wav")
    wav_written = provider.synthesize("这是 WAV 格式。", wav, format="wav")
    assert Path(wav_written).is_file() and Path(wav_written).stat().st_size > 1024
    assert is_wav(wav_written)


def test_streaming_pcm(provider, tmp_path, monkeypatch):
    from plugins.tts.moss import streaming as _moss_streaming  # noqa: F401 — @register runs at import
    from tools.tts_streaming import _try_instantiate

    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    streamer = _try_instantiate("moss", {"moss": {"voice_id": provider.default_voice()}})
    assert streamer is not None
    assert streamer.sample_rate == 48000
    chunks = list(streamer.stream("这是流式测试。"))
    total = sum(len(c) for c in chunks)
    assert total > 2048
    assert total <= 16 * 1024 * 1024


def test_list_voices_includes_builtin_and_clones(provider):
    voices = provider.list_voices()
    builtin = [v for v in voices if v.get("builtin")]
    assert len(builtin) >= 15
    assert all(v.get("id") and v.get("voice_id") for v in builtin[:3])


def test_clone_then_reuse(provider, tmp_path):
    sample = str(tmp_path / "sample.mp3")
    provider.synthesize("克隆音色参考音频。", sample)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    created = provider.create_voice(
        sample, name=f"pytest-clone-{stamp}", description="live e2e"
    )
    voice_id = created.get("voice_id") or created.get("id")
    assert voice_id
    assert any(v.get("id") == voice_id for v in provider.list_voices())

    reused = str(tmp_path / "reuse.mp3")
    provider.synthesize("这是克隆音色。", reused, voice=voice_id)
    assert Path(reused).is_file() and Path(reused).stat().st_size > 1024


def test_dialogue_sync(provider, tmp_path):
    voices = provider.list_voices()
    speakers = [
        {"id": "a", "voice_id": voices[0]["id"]},
        {"id": "b", "voice_id": voices[1]["id"]},
    ]
    segments = [
        {"speaker": "a", "text": "你好，请问今天天气如何？"},
        {"speaker": "b", "text": "今天是晴天，非常适合出门散步。"},
    ]
    out = str(tmp_path / "dialogue.mp3")
    written = provider.synthesize_dialogue(speakers, segments, out)
    assert Path(written).is_file() and Path(written).stat().st_size > 1024
    assert is_mp3(written)


def test_voice_design(provider, tmp_path):
    out = str(tmp_path / "design.mp3")
    written = provider.design_voice("热情洋溢的播客主持人", "这是声音设计测试。", out)
    assert Path(written).is_file() and Path(written).stat().st_size > 1024
    assert is_mp3(written)


def test_async_synthesize_then_poll(provider):
    task = provider.async_synthesize("这是异步任务测试。")
    assert task.get("task_id")
    done = provider.poll_task(task["task_id"], timeout=180)
    assert str(done.get("status", "")).upper() in ("SUCCESS", "COMPLETED")
    url = done.get("url") or (
        done.get("result", {}).get("url") if isinstance(done.get("result"), dict) else ""
    )
    assert isinstance(url, str) and url.startswith("http")
