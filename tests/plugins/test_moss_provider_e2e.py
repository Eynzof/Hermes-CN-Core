"""Live E2E tests for the Moss plugin (plugins/tts/moss).

Gated on a Moss API key (skipif no key). The key is NEVER hardcoded —
it is passed in via the pytest CLI arguments ``--moss-key <key>`` or
``--moss-key-file <path>`` (a config/key file such as ``D:/moss.txt``
whose first line is ``"api_key": "<key>"`` or a raw token), or via the
``MOSS_API_KEY`` / ``MOSS_KEY_FILE`` env vars (the same fallback
MossClient uses). These tests perform real network I/O — they are
skipped by default in CI without a key.

Coverage: provider synthesize (mp3/wav), streaming PCM (48 kHz),
list_voices, clone → reuse, dialogue, voice design, async poll, plus the
new STT + MOSS-VL workflows: transcription round-trip, two-speaker
diarization, and image OCR via ``moss_vision``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest

from moss_tts import is_mp3, is_wav


def _read_key_from_file(path: str) -> str:
    """Read a Moss API key from a config/key file.

    Accepts either ``"api_key": "<key>"`` (JSON-ish, as in ``D:/moss.txt``)
    or a raw single-token file.
    """
    if not path:
        return ""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except Exception:
        return ""
    m = re.search(r'"api_key"\s*:\s*"([^"]+)"', text)
    return (m.group(1) if m else text.strip()).strip()


def _cli_option(name: str) -> str:
    """Read ``--name <value>`` / ``--name=<value>`` from the pytest argv."""
    args = sys.argv[1:]
    for i, arg in enumerate(args):
        if arg == f"--{name}" and i + 1 < len(args):
            return args[i + 1].strip()
        if arg.startswith(f"--{name}="):
            return arg.split("=", 1)[1].strip()
    return ""


# Resolution order: --moss-key CLI arg → MOSS_API_KEY env → --moss-key-file
# CLI arg → MOSS_KEY_FILE env → the plan's D:/moss.txt config. No key is
# hardcoded in this file.
_DEFAULT_KEY_FILE = "D:/moss.txt"
_MOSS_API_KEY = (
    _cli_option("moss-key")
    or os.environ.get("MOSS_API_KEY", "").strip()
    or _read_key_from_file(_cli_option("moss-key-file"))
    or _read_key_from_file(os.environ.get("MOSS_KEY_FILE", "").strip())
    or (_read_key_from_file(_DEFAULT_KEY_FILE) if Path(_DEFAULT_KEY_FILE).is_file() else "")
)

pytestmark = pytest.mark.skipif(
    not _MOSS_API_KEY,
    reason="No Moss API key — pass --moss-key or --moss-key-file (e.g. D:/moss.txt), or set MOSS_API_KEY/MOSS_KEY_FILE",
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


# ---------------------------------------------------------------------------
# New workflows — STT (transcribe + diarize) and MOSS-VL (image/video)
# ---------------------------------------------------------------------------


def _make_ocr_image(path: Path) -> None:
    """Render a text image with PIL for the OCR round-trip."""
    from PIL import Image, ImageDraw, ImageFont

    img = Image.new("RGB", (900, 240), "white")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.truetype("arial.ttf", 72)
    except Exception:  # noqa: BLE001 — fall back to the default bitmap font
        font = ImageFont.load_default()
    draw.text((40, 60), "HELLO MOSS 2026", fill="black", font=font)
    img.save(path, "PNG")


def _moss_http_headers() -> dict:
    return {"Authorization": f"Bearer {_MOSS_API_KEY}"}


def _synthesize_speech_via_http(text: str, out_path: Path, fmt: str = "wav") -> Path:
    """Synthesize speech via raw HTTP (bypasses the moss_tts SDK).

    Keeps the STT/VL e2e self-contained: the transcription + vision paths
    are deliberately SDK-independent (self-contained HTTP in
    ``plugins/tts/moss/api.py``), so their e2e should not require the SDK
    either.
    """
    import requests

    resp = requests.post(
        "https://api.mosi.cn/v1/audio/speech",
        headers={**_moss_http_headers(), "Content-Type": "application/json"},
        json={
            "model": "moss-tts",
            "version": "flash-20260626",
            "input": text,
            "voice_id": "94aa4989-c7e9-5007-ae42-ab401823e6c9",
            "response_format": fmt,
            "delivery_method": "audio",
        },
        timeout=120,
    )
    assert resp.status_code == 200, f"speech synthesis failed: {resp.text[:300]}"
    out_path.write_bytes(resp.content)
    return out_path


def _synthesize_dialogue_via_http(out_path: Path) -> Path:
    """Synthesize a two-speaker dialogue via raw HTTP for diarization."""
    import requests

    voices = requests.get(
        "https://api.mosi.cn/v1/audio/voices", headers=_moss_http_headers(), timeout=60
    ).json()
    items = voices if isinstance(voices, list) else (
        voices.get("data") or voices.get("voices") or []
    )
    assert len(items) >= 2, "need at least 2 Moss voices"
    v0 = items[0].get("voice_id") or items[0].get("id")
    v1 = items[1].get("voice_id") or items[1].get("id")

    resp = requests.post(
        "https://api.mosi.cn/v1/audio/speech/speakers",
        headers={**_moss_http_headers(), "Content-Type": "application/json"},
        json={
            "model": "moss-ttsd",
            "speakers": [{"id": "a", "voice_id": v0}, {"id": "b", "voice_id": v1}],
            "segments": [
                {"speaker": "a", "text": "你好，请问今天天气如何？"},
                {"speaker": "b", "text": "今天是晴天，非常适合出门散步。"},
            ],
            "response_format": "mp3",
            "delivery_method": "audio",
        },
        timeout=120,
    )
    assert resp.status_code == 200, f"dialogue synthesis failed: {resp.text[:300]}"
    out_path.write_bytes(resp.content)
    return out_path


def test_transcribe_roundtrip(provider, tmp_path):
    """Synthesize a WAV, then transcribe it back to text (provider path)."""
    from plugins.tts.moss.transcription import MossTranscriptionProvider

    wav = _synthesize_speech_via_http("欢迎使用 Moss 端到端转写测试。", tmp_path / "roundtrip.wav")
    assert wav.is_file() and wav.stat().st_size > 1024

    stt = MossTranscriptionProvider()
    result = stt.transcribe(str(wav))
    assert result["success"] is True
    assert result["provider"] == "moss"
    assert result["transcript"].strip()
    assert result["model"] == "moss-transcribe-1.0"


def test_transcribe_tool_roundtrip(provider, tmp_path):
    """Round-trip through the moss_transcribe tool handler."""
    import json as _json

    from plugins.tts.moss import tools as moss_tools

    wav = _synthesize_speech_via_http("这是通过工具转写测试。", tmp_path / "tool.wav")

    result = _json.loads(moss_tools._handle_moss_transcribe({"audio_path": str(wav)}))
    assert result["success"] is True
    assert result["transcript"].strip()
    assert result["provider"] == "moss"


def test_transcribe_diarize_two_speakers(provider, tmp_path):
    """Synthesize a two-speaker dialogue, then diarize it."""
    from plugins.tts.moss.transcription import MossTranscriptionProvider

    out = _synthesize_dialogue_via_http(tmp_path / "dialogue.mp3")
    assert out.is_file() and out.stat().st_size > 1024

    stt = MossTranscriptionProvider()
    result = stt.transcribe(str(out), diarize=True)
    assert result["success"] is True
    assert result["model"] == "moss-transcribe-diarize-pro"
    # Diarization returns speaker-separated segments; at least one speaker
    # label must be present (the exact count varies per clip).
    segs = result.get("segments") or []
    assert segs, "diarization returned no segments"
    assert any(s.get("speaker") for s in segs)
    assert any(s.get("text") for s in segs)


def test_vision_image_ocr(provider, tmp_path, monkeypatch):
    """Upload a generated PNG and OCR it via moss_vision (full flow)."""
    import json as _json

    from plugins.tts.moss import tools as moss_tools

    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    img = tmp_path / "ocr.png"
    _make_ocr_image(img)

    result = _json.loads(moss_tools._handle_moss_vision({
        "instruction": "OCR this image. Return the exact text you see.",
        "images": [str(img)],
    }))
    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["provider"] == "moss"
    text = result["text"].strip()
    assert text, "moss_vision returned empty text for a text image"
    assert any(tok in text.upper() for tok in ("HELLO", "MOSS", "2026"))


def test_vision_upload_file_id_flow(provider, tmp_path, monkeypatch):
    """Upload via /v1/files → file_id, then query MOSS-VL with that file_id."""
    import json as _json

    from plugins.tts.moss import api as moss_api
    from plugins.tts.moss import tools as moss_tools

    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    img = tmp_path / "upload.png"
    _make_ocr_image(img)

    file_id = moss_api.upload_file(str(img), purpose="image")
    assert isinstance(file_id, str) and file_id.strip()

    result = _json.loads(moss_tools._handle_moss_vision({
        "instruction": "Describe this image briefly.",
        "images": [f"file_id:{file_id}"],
    }))
    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["text"].strip()


def _make_test_video(path: Path) -> bool:
    """Render a tiny test video; False when no encoder (ffmpeg) is available."""
    import shutil
    import subprocess

    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        try:
            import imageio_ffmpeg

            ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:  # noqa: BLE001 — no video encoder available
            return False
    try:
        result = subprocess.run(
            [
                ffmpeg, "-y", "-f", "lavfi", "-i",
                "testsrc=duration=1:size=160x120:rate=10",
                "-pix_fmt", "yuv420p", str(path),
            ],
            capture_output=True,
            timeout=90,
            stdin=subprocess.DEVNULL,
        )
        return result.returncode == 0 and path.stat().st_size > 0
    except Exception:  # noqa: BLE001 — best-effort
        return False


def test_vision_video_understanding(provider, tmp_path, monkeypatch):
    """Upload a short generated MP4 and ask moss_vision to describe it."""
    import json as _json

    from plugins.tts.moss import tools as moss_tools

    video = tmp_path / "clip.mp4"
    if not _make_test_video(video):
        pytest.skip("no ffmpeg / video encoder available to generate a test clip")

    monkeypatch.setenv("MOSS_API_KEY", _MOSS_API_KEY)
    result = _json.loads(moss_tools._handle_moss_vision({
        "instruction": "Describe what happens in this video briefly.",
        "video": str(video),
    }))
    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["text"].strip()
