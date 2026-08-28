"""Mocked unit tests for the Moss STT provider + HTTP layer.

No network — ``plugins.tts.moss.api`` functions (and ``requests.post``
where the real builder is exercised) are monkeypatched.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugins.tts.moss import api as moss_api
from plugins.tts.moss.api import (
    MAX_KEYTERMS,
    MAX_KEYTERM_LENGTH,
    MossApiError,
    validate_public_url,
)
from plugins.tts.moss.transcription import (
    MODEL_DIARIZE_PRO,
    MODEL_TRANSCRIBE,
    MossTranscriptionProvider,
    normalize_segments,
)


@pytest.fixture
def provider() -> MossTranscriptionProvider:
    return MossTranscriptionProvider()


@pytest.fixture
def fake_api(monkeypatch):
    """Patch the api module with canned responses and recorded calls."""
    state = SimpleNamespace(
        calls={"transcribe": [], "transcribe_audio": [], "upload_file": []},
        responses={
            "transcribe": {"text": "你好，世界。", "duration": 3.2},
            "transcribe_audio": {"text": "from url"},
            "upload_file": "file-abc-123",
        },
    )

    def fake_transcribe(file_path, **kw):
        state.calls["transcribe"].append(kw)
        return state.responses["transcribe"]

    def fake_transcribe_audio(audio_path, **kw):
        state.calls["transcribe_audio"].append(kw)
        return state.responses["transcribe_audio"]

    monkeypatch.setattr(moss_api, "transcribe", fake_transcribe)
    monkeypatch.setattr(moss_api, "transcribe_audio", fake_transcribe_audio)
    return state


def _audio_file(tmp_path: Path, name: str = "clip.mp3", size: int = 64) -> str:
    p = tmp_path / name
    p.write_bytes(b"\x00" * size)
    return str(p)


# ---------------------------------------------------------------------------
# Basics
# ---------------------------------------------------------------------------


class TestBasics:
    def test_name_and_display(self, provider):
        assert provider.name == "moss"
        assert provider.display_name == "Moss"

    def test_list_models_contains_both(self, provider):
        ids = [m["id"] for m in provider.list_models()]
        assert MODEL_TRANSCRIBE in ids
        assert MODEL_DIARIZE_PRO in ids

    def test_default_model(self, provider):
        assert provider.default_model() == MODEL_TRANSCRIBE

    def test_setup_schema(self, provider):
        schema = provider.get_setup_schema()
        assert schema["name"] == "Moss"
        assert schema["badge"] == "paid"
        assert schema["env_vars"][0]["key"] == "MOSS_API_KEY"

    def test_is_available_false_without_key(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.tts.moss.transcription.resolve_moss_api_key", lambda: ""
        )
        monkeypatch.setattr("moss_tts._load_api_key", lambda: "")
        assert MossTranscriptionProvider().is_available() is False

    def test_is_available_true_with_key(self, monkeypatch):
        monkeypatch.setattr(
            "plugins.tts.moss.transcription.resolve_moss_api_key", lambda: "sk-test"
        )
        assert MossTranscriptionProvider().is_available() is True


# ---------------------------------------------------------------------------
# transcribe()
# ---------------------------------------------------------------------------


class TestTranscribe:
    def test_sync_basic_envelope(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        result = provider.transcribe(path)
        assert result["success"] is True
        assert result["transcript"] == "你好，世界。"
        assert result["provider"] == "moss"
        assert result["model"] == MODEL_TRANSCRIBE
        assert result["duration"] == 3.2
        assert "segments" not in result
        call = fake_api.calls["transcribe"][0]
        assert call["model"] == MODEL_TRANSCRIBE
        assert call["diarize"] is False
        assert call["async_mode"] is False
        assert call["keyterms"] is None

    def test_explicit_model(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        provider.transcribe(path, model=MODEL_DIARIZE_PRO)
        assert fake_api.calls["transcribe"][0]["model"] == MODEL_DIARIZE_PRO

    def test_diarize_forces_diarize_pro(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        fake_api.responses["transcribe"] = {
            "text": "",
            "duration": 5.0,
            "segments": [
                {"start": 0.0, "end": 1.5, "text": "你好", "speaker": "S01"},
                {"start": 1.6, "end": 3.0, "text": "好的", "speaker": "S02"},
            ],
        }
        result = provider.transcribe(path, diarize=True)
        call = fake_api.calls["transcribe"][0]
        assert call["model"] == MODEL_DIARIZE_PRO
        assert call["diarize"] is True
        assert result["model"] == MODEL_DIARIZE_PRO
        assert result["segments"][0] == {
            "start": 0.0, "end": 1.5, "text": "你好", "speaker": "S01",
        }
        # Transcript is assembled from diarized segments when text is empty.
        assert "S01" in result["transcript"] and "S02" in result["transcript"]

    def test_keyterms_dropped_on_plain_model(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        provider.transcribe(path, keyterms=["术语", "boost"])
        # Plain model is not diarize-pro → keyterms are dropped before the API.
        assert fake_api.calls["transcribe"][0]["keyterms"] is None

    def test_keyterms_passed_on_diarize_model(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        provider.transcribe(path, diarize=True, keyterms=["术语", "boost"])
        assert fake_api.calls["transcribe"][0]["keyterms"] == ["术语", "boost"]

    def test_language_is_ignored_not_fatal(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        result = provider.transcribe(path, language="zh")
        assert result["success"] is True

    def test_response_format_passthrough(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        provider.transcribe(path, response_format="text")
        assert fake_api.calls["transcribe"][0]["response_format"] == "text"

    def test_unsupported_response_format_falls_back_to_json(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        provider.transcribe(path, response_format="yaml")
        assert fake_api.calls["transcribe"][0]["response_format"] == "json"


class TestTranscribeErrors:
    def test_missing_file_error_envelope(self, provider):
        result = provider.transcribe("/nonexistent/clip.mp3")
        assert result["success"] is False
        assert result["transcript"] == ""
        assert "not found" in result["error"]
        assert result["provider"] == "moss"

    def test_file_too_large_rejected(self, provider, tmp_path, monkeypatch):
        # Force a tiny cap so a small file trips the size guard.
        monkeypatch.setattr(
            MossTranscriptionProvider, "_stt_section",
            classmethod(lambda cls, cfg=None: {"max_file_size": 10}),
        )
        path = _audio_file(tmp_path, size=64)
        result = provider.transcribe(path)
        assert result["success"] is False
        assert "too large" in result["error"]

    def test_api_failure_becomes_error_envelope(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)

        def boom(file_path, **kw):
            raise MossApiError("HTTP 400 invalid_request_error")

        original = moss_api.transcribe
        moss_api.transcribe = boom
        try:
            result = provider.transcribe(path)
        finally:
            moss_api.transcribe = original
        assert result["success"] is False
        assert "HTTP 400" in result["error"]

    def test_never_raises(self, provider, tmp_path, monkeypatch):
        path = _audio_file(tmp_path)
        monkeypatch.setattr(
            moss_api, "transcribe",
            lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
        )
        result = provider.transcribe(path)
        assert result["success"] is False
        assert "boom" in result["error"]


class TestAsyncAndPoll:
    def test_async_mode_returns_task_id(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        fake_api.responses["transcribe"] = {"id": "task-9", "task_id": "task-9", "status": "PROCESSING"}
        result = provider.transcribe(path, async_mode=True)
        assert result["success"] is True
        assert result["async"] is True
        assert result["task_id"] == "task-9"
        assert fake_api.calls["transcribe"][0]["async_mode"] is True

    def test_async_missing_task_id_is_error(self, provider, fake_api, tmp_path):
        path = _audio_file(tmp_path)
        fake_api.responses["transcribe"] = {"status": "PROCESSING"}
        result = provider.transcribe(path, async_mode=True)
        assert result["success"] is False
        assert "task_id" in result["error"]

    def test_poll_task_delegates_to_moss_provider(self, provider, monkeypatch):
        from plugins.tts.moss import provider as provider_mod

        captured = {}

        def fake_poll(self, task_id, timeout=180.0, **kw):
            captured["task_id"] = task_id
            return {"status": "SUCCESS", "result": {"text": "done"}}

        monkeypatch.setattr(provider_mod.MossProvider, "poll_task", fake_poll)
        out = provider.poll_task("task-1", timeout=30)
        assert captured["task_id"] == "task-1"
        assert out["status"] == "SUCCESS"


# ---------------------------------------------------------------------------
# segment normalization
# ---------------------------------------------------------------------------


class TestNormalizeSegments:
    def test_normalizes_fields(self):
        raw = [
            {"start": 0.0, "end": 1.0, "text": "a", "speaker": "S01"},
            {"start": 1.0, "end": 2.0, "text": "b", "speaker": "S02"},
            {"start": 2.0, "end": 3.0, "text": "c"},  # missing speaker
        ]
        out = normalize_segments(raw)
        assert len(out) == 3
        assert out[0] == {"start": 0.0, "end": 1.0, "text": "a", "speaker": "S01"}
        assert out[1]["speaker"] == "S02"
        assert out[2]["speaker"] == ""

    def test_empty_or_non_list(self):
        assert normalize_segments(None) == []
        assert normalize_segments("nope") == []

    def test_filters_fully_empty_entries(self):
        # Entries with neither text nor speaker are dropped; a speaker-only
        # entry (silence labeled to a speaker) is kept.
        assert normalize_segments([{"text": "  ", "speaker": ""}]) == []
        assert len(normalize_segments([{"text": "", "speaker": "S01"}])) == 1


# ---------------------------------------------------------------------------
# api-level validation (real builder, no network)
# ---------------------------------------------------------------------------


class TestKeytermsValidation:
    def test_ok_pairs(self):
        pairs = moss_api._keyterms_form(["你好", "boost"])
        assert pairs == [("keyterms", "你好"), ("keyterms", "boost")]

    def test_dedup_and_blank(self):
        pairs = moss_api._keyterms_form(["a", "a", "", "b"])
        assert pairs == [("keyterms", "a"), ("keyterms", "b")]

    def test_too_many_rejected(self):
        with pytest.raises(MossApiError, match="Too many"):
            moss_api._keyterms_form([f"k{i}" for i in range(MAX_KEYTERMS + 1)])

    def test_too_long_rejected(self):
        with pytest.raises(MossApiError, match="chars"):
            moss_api._keyterms_form(["x" * (MAX_KEYTERM_LENGTH + 1)])


class TestURLValidation:
    def test_rejects_localhost(self):
        with pytest.raises(MossApiError, match="public"):
            validate_public_url("http://localhost:8000/a.mp3")

    def test_rejects_private_ip(self):
        with pytest.raises(MossApiError, match="public"):
            validate_public_url("http://192.168.1.1/a.mp3")

    def test_rejects_non_http(self):
        with pytest.raises(MossApiError, match="scheme"):
            validate_public_url("ftp://example.com/a.mp3")

    def test_accepts_public(self):
        validate_public_url("https://example.com/a.mp3")


class TestTranscribeAudioRouting:
    def test_local_path_uses_multipart_transcribe(self, monkeypatch):
        sent = {}
        monkeypatch.setattr(moss_api, "transcribe", lambda p, **kw: sent.update(p=p) or {"text": "x"})
        out = moss_api.transcribe_audio("C:/tmp/clip.mp3", model=MODEL_TRANSCRIBE)
        assert out["text"] == "x"
        assert sent["p"] == "C:/tmp/clip.mp3"

    def test_text_response_format_normalized(self, tmp_path, monkeypatch):
        """response_format=text returns a plain-text body → {text: ...} envelope."""
        audio = tmp_path / "clip.mp3"
        audio.write_bytes(b"\x00" * 64)

        class _TextResp:
            status_code = 200
            text = "欢迎使用转写测试。"

        monkeypatch.setattr(moss_api.requests, "post", lambda *a, **kw: _TextResp())
        monkeypatch.setattr(
            moss_api, "build_http_kwargs",
            lambda: {"api_key": "k", "base_url": "https://api.mosi.cn/v1", "timeout": 60, "headers": {}},
        )
        out = moss_api.transcribe(str(audio), model=MODEL_TRANSCRIBE, response_format="text")
        assert out == {"text": "欢迎使用转写测试。"}

    def test_file_id_uses_json(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"text": "from file_id"}

            def __getattr__(self, item):
                return None

        def fake_post(url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            return _Resp()

        monkeypatch.setattr(moss_api.requests, "post", fake_post)
        monkeypatch.setattr(
            moss_api, "build_http_kwargs",
            lambda: {"api_key": "k", "base_url": "https://api.mosi.cn/v1", "timeout": 60, "headers": {}},
        )
        out = moss_api.transcribe_audio("file_id:file-1", model=MODEL_DIARIZE_PRO, diarize=True)
        assert captured["json"]["file_id"] == "file-1"
        assert captured["json"]["model"] == MODEL_DIARIZE_PRO
        assert captured["json"]["diarize"] is True
        assert out["text"] == "from file_id"

    def test_url_uses_json_and_requires_public(self, monkeypatch):
        captured = {}

        class _Resp:
            status_code = 200

            def json(self):
                return {"text": "from url"}

        def fake_post(url, **kw):
            captured["url"] = url
            captured["json"] = kw.get("json")
            return _Resp()

        monkeypatch.setattr(moss_api.requests, "post", fake_post)
        monkeypatch.setattr(
            moss_api, "build_http_kwargs",
            lambda: {"api_key": "k", "base_url": "https://api.mosi.cn/v1", "timeout": 60, "headers": {}},
        )
        out = moss_api.transcribe_audio("https://example.com/a.mp3", model=MODEL_TRANSCRIBE)
        assert captured["json"]["url"] == "https://example.com/a.mp3"
        assert out["text"] == "from url"

        # Private URL rejected before any request.
        with pytest.raises(MossApiError, match="public"):
            moss_api.transcribe_audio("http://127.0.0.1/a.mp3", model=MODEL_TRANSCRIBE)
