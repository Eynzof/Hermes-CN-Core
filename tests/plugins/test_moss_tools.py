"""Mocked unit tests for the Moss plugin tools (plugins/tts/moss/tools.py).

No network — the provider methods are faked.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from plugins.tts.moss import tools as moss_tools
from plugins.tts.moss.provider import MossProvider


class FakeProvider:
    """Stand-in for MossProvider used by the tool handlers."""

    def __init__(self):
        self.is_available_result = True
        self.dialogue_result = "/tmp/dialogue.mp3"
        self.design_result = "/tmp/design.mp3"
        self.create_voice_result = {"voice_id": "clone-1", "id": "clone-1"}
        self.voices = [{"id": "v1", "voice_id": "v1", "display": "Built-in"}]

    def is_available(self):
        return self.is_available_result

    def synthesize_dialogue(self, speakers, segments, output_path, **kw):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"mp3")
        return self.dialogue_result

    def design_voice(self, instruction, text, output_path, **kw):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"mp3")
        return self.design_result

    def create_voice(self, audio_sample_path, **kw):
        return self.create_voice_result

    def list_voices(self):
        return self.voices


@pytest.fixture
def fake(monkeypatch):
    fake = FakeProvider()
    monkeypatch.setattr(moss_tools, "_provider", lambda: fake)
    monkeypatch.setattr(MossProvider, "_section", lambda self=None: {"max_text_length": 5000})
    return fake


def _load(payload: str) -> dict:
    return json.loads(payload)


class TestCheckFn:
    def test_available_true(self, monkeypatch):
        monkeypatch.setattr(MossProvider, "is_available", lambda self: True)
        assert moss_tools._check_moss_available() is True

    def test_available_false(self, monkeypatch):
        monkeypatch.setattr(MossProvider, "is_available", lambda self: False)
        assert moss_tools._check_moss_available() is False

    def test_never_raises(self, monkeypatch):
        def boom(self):
            raise RuntimeError("boom")

        monkeypatch.setattr(MossProvider, "is_available", boom)
        assert moss_tools._check_moss_available() is False


class TestDialogueValidation:
    def test_happy_path(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"id": "a", "voice_id": "v1"}, {"id": "b", "voice_id": "v2"}],
            "segments": [{"speaker": "a", "text": "你好"}, {"speaker": "b", "text": "你好呀"}],
            "output_path": "/tmp/dialogue.mp3",
        }))
        assert result["success"] is True
        assert result["file_path"] == "/tmp/dialogue.mp3"
        assert result["MEDIA"] == "/tmp/dialogue.mp3"

    def test_speaker_not_declared(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"id": "a", "voice_id": "v1"}],
            "segments": [{"speaker": "zz", "text": "hello"}],
        }))
        assert result["success"] is False
        assert "not" in result["error"] and "declared" in result["error"]

    def test_empty_segments_rejected(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"id": "a", "voice_id": "v1"}],
            "segments": [],
        }))
        assert result["success"] is False
        assert "segments" in result["error"]

    def test_empty_text_rejected(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"id": "a", "voice_id": "v1"}],
            "segments": [{"speaker": "a", "text": "   "}],
        }))
        assert result["success"] is False
        assert "empty text" in result["error"]

    def test_over_max_text_rejected(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"id": "a", "voice_id": "v1"}],
            "segments": [{"speaker": "a", "text": "x" * 5001}],
        }))
        assert result["success"] is False
        assert "exceeding" in result["error"]

    def test_missing_speakers_list(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({"segments": [{"speaker": "a", "text": "hi"}]}))
        assert result["success"] is False

    def test_missing_speaker_id(self, fake):
        result = _load(moss_tools._handle_moss_dialogue_tts({
            "speakers": [{"voice_id": "v1"}],
            "segments": [{"speaker": "a", "text": "hi"}],
        }))
        assert result["success"] is False
        assert "missing required field 'id'" in result["error"]


class TestVoiceDesign:
    def test_happy_path(self, fake):
        result = _load(moss_tools._handle_moss_voice_design({
            "instruction": "energetic", "text": "hello", "output_path": "/tmp/design.mp3",
        }))
        assert result["success"] is True
        assert result["file_path"] == "/tmp/design.mp3"

    def test_instruction_required(self, fake):
        result = _load(moss_tools._handle_moss_voice_design({"text": "hello"}))
        assert result["success"] is False
        assert "instruction" in result["error"]

    def test_text_required(self, fake):
        result = _load(moss_tools._handle_moss_voice_design({"instruction": "energetic"}))
        assert result["success"] is False
        assert "text" in result["error"]


class TestVoiceClone:
    def test_happy_path(self, fake, tmp_path):
        sample = tmp_path / "sample.mp3"
        sample.write_bytes(b"mp3data")
        result = _load(moss_tools._handle_moss_voice_clone({
            "audio_sample_path": str(sample), "name": "My Clone",
        }))
        assert result["success"] is True
        assert result["voice_id"] == "clone-1"
        assert result["voice"]["voice_id"] == "clone-1"

    def test_path_required(self, fake):
        result = _load(moss_tools._handle_moss_voice_clone({}))
        assert result["success"] is False
        assert "audio_sample_path" in result["error"]

    def test_path_missing_file(self, fake):
        result = _load(moss_tools._handle_moss_voice_clone({
            "audio_sample_path": "/nonexistent/sample.mp3",
        }))
        assert result["success"] is False
        assert "not found" in result["error"]


class TestVoiceList:
    def test_happy_path(self, fake):
        result = _load(moss_tools._handle_moss_voice_list({}))
        assert result["success"] is True
        assert result["count"] == 1
        assert result["voices"][0]["id"] == "v1"


class TestSchemas:
    def test_dialogue_schema_shape(self):
        schema = moss_tools.MOSS_DIALOGUE_TTS_SCHEMA
        assert schema["name"] == "moss_dialogue_tts"
        assert schema["parameters"]["required"] == ["speakers", "segments"]

    def test_tools_all_registered_schemas(self):
        assert moss_tools.MOSS_VOICE_DESIGN_SCHEMA["name"] == "moss_voice_design"
        assert moss_tools.MOSS_VOICE_CLONE_SCHEMA["name"] == "moss_voice_clone"
        assert moss_tools.MOSS_VOICE_LIST_SCHEMA["name"] == "moss_voice_list"

    def test_transcribe_schema_shape(self):
        schema = moss_tools.MOSS_TRANSCRIBE_SCHEMA
        assert schema["name"] == "moss_transcribe"
        assert schema["parameters"]["required"] == ["audio_path"]
        props = schema["parameters"]["properties"]
        assert props["model"]["enum"] == ["moss-transcribe-1.0", "moss-transcribe-diarize-pro"]

    def test_vision_schema_shape(self):
        schema = moss_tools.MOSS_VISION_SCHEMA
        assert schema["name"] == "moss_vision"
        assert schema["parameters"]["required"] == ["instruction"]
        props = schema["parameters"]["properties"]
        assert props["images"]["maxItems"] == 5


class TestMossTranscribe:
    @pytest.fixture
    def fake_api(self, monkeypatch, tmp_path):
        state = {"calls": [], "responses": {"text": "转写结果", "duration": 1.5}}

        def fake_transcribe_audio(audio_path, **kw):
            state["calls"].append(kw)
            return state["responses"]

        monkeypatch.setattr(moss_tools, "transcribe_audio", fake_transcribe_audio)
        p = tmp_path / "clip.mp3"
        p.write_bytes(b"\x00" * 64)
        state["path"] = str(p)
        return state

    def test_happy_path(self, fake_api):
        result = _load(moss_tools._handle_moss_transcribe({"audio_path": fake_api["path"]}))
        assert result["success"] is True
        assert result["transcript"] == "转写结果"
        assert result["provider"] == "moss"
        assert result["model"] == "moss-transcribe-1.0"
        assert fake_api["calls"][0]["diarize"] is False

    def test_audio_path_required(self, fake_api):
        result = _load(moss_tools._handle_moss_transcribe({}))
        assert result["success"] is False
        assert "audio_path" in result["error"]

    def test_diarize_forces_diarize_pro_model(self, fake_api):
        _load(moss_tools._handle_moss_transcribe({
            "audio_path": fake_api["path"], "diarize": True,
        }))
        call = fake_api["calls"][0]
        assert call["model"] == "moss-transcribe-diarize-pro"
        assert call["diarize"] is True

    def test_keyterms_passed_through(self, fake_api):
        _load(moss_tools._handle_moss_transcribe({
            "audio_path": fake_api["path"], "keyterms": ["术语", "boost"],
        }))
        assert fake_api["calls"][0]["keyterms"] == ["术语", "boost"]

    def test_async_mode_returns_task_id(self, fake_api):
        fake_api["responses"] = {"task_id": "task-42", "status": "PROCESSING"}
        result = _load(moss_tools._handle_moss_transcribe({
            "audio_path": fake_api["path"], "async_mode": True,
        }))
        assert result["success"] is True
        assert result["async"] is True
        assert result["task_id"] == "task-42"

    def test_task_id_polls(self, fake_api, monkeypatch):
        from plugins.tts.moss.transcription import MossTranscriptionProvider

        captured = {}

        def fake_poll(self, task_id, **kw):
            captured["task_id"] = task_id
            return {"status": "SUCCESS", "result": {"text": "轮询结果"}}

        monkeypatch.setattr(MossTranscriptionProvider, "poll_task", fake_poll)
        result = _load(moss_tools._handle_moss_transcribe({"task_id": "task-1"}))
        assert result["success"] is True
        assert captured["task_id"] == "task-1"

    def test_api_error_envelope(self, fake_api, monkeypatch):
        def boom(audio_path, **kw):
            raise RuntimeError("boom")

        monkeypatch.setattr(moss_tools, "transcribe_audio", boom)
        result = _load(moss_tools._handle_moss_transcribe({"audio_path": fake_api["path"]}))
        assert result["success"] is False
        assert "boom" in result["error"]
