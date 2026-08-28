"""Mocked unit tests for the Moss TTS provider (plugins/tts/moss/provider.py).

No network — ``build_client`` is monkeypatched to a fake MossClient.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

from plugins.tts.moss.provider import MossProvider
from moss_tts import DOC_VOICES


class FakeMossClient:
    """Minimal stand-in for moss_tts.MossClient."""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.config = config or {}
        self.calls: Dict[str, list] = {}
        self.audio_bytes = b"\xff\xfb\x90\x64" + b"\x00" * 512  # MPEG sync + pad
        self.speech_result: Any = None
        self.voices: list = []
        self.create_voice_result: Optional[Dict[str, Any]] = None

    def _record(self, name: str, kwargs: Dict[str, Any]):
        self.calls.setdefault(name, []).append(kwargs)

    def speech(self, text: str, **kwargs):
        self._record("speech", {"text": text, **kwargs})
        if self.speech_result is not None:
            return self.speech_result
        return self.audio_bytes

    def save_audio(self, data, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(bytes(data))
        return output_path

    def download(self, url, output_path):
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_bytes(b"downloaded")
        return output_path

    def speakers(self, speakers, segments, **kwargs):
        self._record("speakers", {"speakers": speakers, "segments": segments, **kwargs})
        if kwargs.get("async_mode"):
            return {"task_id": "task-speakers"}
        return self.audio_bytes

    def voice_generations(self, instruction, input_text, **kwargs):
        self._record("voice_generations", {"instruction": instruction, "input_text": input_text, **kwargs})
        if kwargs.get("async_mode"):
            return {"task_id": "task-design"}
        return self.audio_bytes

    def create_voice(self, audio_sample_path, name=None, description=None, **kwargs):
        self._record("create_voice", {"path": audio_sample_path, "name": name, "description": description})
        if self.create_voice_result is not None:
            return self.create_voice_result
        return {"id": "clone-abc-123", "voice_id": "clone-abc-123", "name": name}

    def list_voices(self):
        return self.voices

    def poll_task(self, task_id, timeout=180.0, **kwargs):
        return {"status": "SUCCESS", "url": "https://example.com/a.mp3"}


@pytest.fixture
def provider(monkeypatch):
    from plugins.tts.moss import provider as provider_module

    fake = FakeMossClient()
    monkeypatch.setattr(provider_module, "build_client", lambda cfg=None: fake)
    p = MossProvider()
    p._fake = fake
    return p


class TestBasics:
    def test_name_and_display(self):
        p = MossProvider()
        assert p.name == "moss"
        assert p.display_name == "Moss"

    def test_voice_compatible_true(self):
        assert MossProvider().voice_compatible is True

    def test_setup_schema(self):
        schema = MossProvider().get_setup_schema()
        assert schema["name"] == "Moss"
        assert schema["badge"] == "paid"
        assert schema["env_vars"][0]["key"] == "MOSS_API_KEY"

    def test_is_available_false_when_no_key(self, monkeypatch):
        from plugins.tts.moss import provider as provider_module

        monkeypatch.setattr(provider_module, "resolve_moss_api_key", lambda cfg=None: "")
        monkeypatch.setattr("moss_tts._load_api_key", lambda: "")
        assert MossProvider().is_available() is False

    def test_is_available_true_with_key(self, monkeypatch):
        from plugins.tts.moss import provider as provider_module

        monkeypatch.setattr(provider_module, "resolve_moss_api_key", lambda cfg=None: "sk-test")
        assert MossProvider().is_available() is True


class TestListVoices:
    def test_builtin_voices_normalized(self, provider):
        voices = provider.list_voices()
        builtin = [v for v in voices if v.get("builtin")]
        assert len(builtin) == len(DOC_VOICES)
        first = builtin[0]
        assert first["id"] == DOC_VOICES[0]["voice_id"]
        assert first["voice_id"] == DOC_VOICES[0]["voice_id"]
        assert first["display"] == DOC_VOICES[0]["name"]
        assert first["language"] == DOC_VOICES[0]["lang"]

    def test_cloned_voices_merged_keyed_id(self, provider):
        provider._fake.voices = [
            {"id": "clone-1", "name": "My Clone", "lang": "中文·普通话"},
            {"id": "clone-2", "name": "Other"},
        ]
        voices = provider.list_voices()
        cloned = [v for v in voices if v.get("cloned")]
        assert len(cloned) == 2
        assert cloned[0]["id"] == "clone-1"
        assert cloned[0]["voice_id"] == "clone-1"
        assert cloned[0]["display"] == "My Clone"

    def test_clone_fetch_failure_is_best_effort(self, provider):
        provider._fake.voices = "not a list"  # would raise inside list_voices

        class Boom:
            def list_voices(self):
                raise RuntimeError("boom")

        provider._fake = Boom()
        voices = provider.list_voices()
        assert len(voices) == len(DOC_VOICES)  # built-ins only


class TestListModels:
    def test_three_models_with_max_text_length(self, provider):
        models = provider.list_models()
        assert [m["id"] for m in models] == ["moss-tts", "moss-ttsd", "moss-voice-generator"]
        assert all(m["max_text_length"] == 5000 for m in models)


class TestSynthesize:
    def test_mp3_direct_delivery(self, provider, tmp_path):
        out = str(tmp_path / "a.mp3")
        written = provider.synthesize("你好", out)
        assert written == out
        call = provider._fake.calls["speech"][0]
        assert call["response_format"] == "mp3"
        assert call["delivery_method"] == "audio"
        assert Path(out).read_bytes().startswith(b"\xff\xfb")

    def test_wav_direct(self, provider, tmp_path):
        out = str(tmp_path / "a.wav")
        written = provider.synthesize("你好", out, format="wav")
        assert written == out
        assert provider._fake.calls["speech"][0]["response_format"] == "wav"

    def test_ogg_requests_mp3_then_converts(self, provider, tmp_path, monkeypatch):
        out = str(tmp_path / "a.ogg")
        monkeypatch.setattr("plugins.tts.moss.provider._has_ffmpeg", lambda: True)
        monkeypatch.setattr(
            "plugins.tts.moss.provider.subprocess.run",
            lambda *a, **kw: type("R", (), {"returncode": 0})(),
        )
        # The conversion helper checks work.exists() and size — simulate by
        # writing the out path from the "ffmpeg" side.
        def fake_run(*a, **kw):
            Path(out).write_bytes(b"OggS")
            return type("R", (), {"returncode": 0})()

        monkeypatch.setattr("plugins.tts.moss.provider.subprocess.run", fake_run)
        written = provider.synthesize("你好", out, format="ogg")
        assert written.endswith(".ogg")
        assert provider._fake.calls["speech"][0]["response_format"] == "mp3"
        assert Path(written).read_bytes().startswith(b"OggS")

    def test_ogg_without_ffmpeg_returns_mp3(self, provider, tmp_path, monkeypatch):
        out = str(tmp_path / "a.ogg")
        monkeypatch.setattr("plugins.tts.moss.provider._has_ffmpeg", lambda: False)
        written = provider.synthesize("你好", out, format="ogg")
        assert written == out  # closest equivalent, correct extension attempt logged

    def test_url_delivery_fallback(self, provider, tmp_path):
        out = str(tmp_path / "a.mp3")
        provider._fake.speech_result = {"url": "https://example.com/a.mp3"}
        written = provider.synthesize("你好", out)
        assert written == out
        assert Path(out).read_bytes() == b"downloaded"

    def test_json_without_url_raises(self, provider, tmp_path):
        provider._fake.speech_result = {"status": "ERROR", "error": "nope"}
        with pytest.raises(Exception, match="missing url"):
            provider.synthesize("你好", str(tmp_path / "a.mp3"))

    def test_pause_passed_through(self, provider, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "plugins.tts.moss.provider.MossProvider._config",
            lambda self: {"moss": {"pause": 0.8}},
        )
        provider.synthesize("你好", str(tmp_path / "a.mp3"))
        assert provider._fake.calls["speech"][0]["pause"] == 0.8

    def test_configured_voice_resolution(self, provider, tmp_path, monkeypatch):
        monkeypatch.setattr(
            "plugins.tts.moss.provider.MossProvider._config",
            lambda self: {"moss": {"voice_id": "configured-voice"}},
        )
        provider.synthesize("你好", str(tmp_path / "a.mp3"))
        assert provider._fake.calls["speech"][0]["voice_id"] == "configured-voice"


class TestDialogueDesignCloneAsync:
    def test_dialogue_sync(self, provider, tmp_path):
        speakers = [{"id": "a", "voice_id": "v1"}, {"id": "b", "voice_id": "v2"}]
        segments = [{"speaker": "a", "text": "hi"}]
        out = str(tmp_path / "d.mp3")
        written = provider.synthesize_dialogue(speakers, segments, out)
        assert written == out
        call = provider._fake.calls["speakers"][0]
        assert call["speakers"] == speakers
        assert call["segments"] == segments
        assert call["model"] == "moss-ttsd"

    def test_dialogue_async_returns_task_id(self, provider):
        result = provider.synthesize_dialogue([], [{"speaker": "a", "text": "x"}], "/tmp/x.mp3", async_mode=True)
        assert result["task_id"] == "task-speakers"

    def test_design_sync(self, provider, tmp_path):
        out = str(tmp_path / "d.mp3")
        written = provider.design_voice("energetic", "hello", out)
        assert written == out
        call = provider._fake.calls["voice_generations"][0]
        assert call["instruction"] == "energetic"
        assert call["input_text"] == "hello"

    def test_design_async(self, provider):
        result = provider.design_voice("energetic", "hello", "/tmp/x.mp3", async_mode=True)
        assert result["task_id"] == "task-design"

    def test_create_voice_normalizes_id(self, provider):
        provider._fake.create_voice_result = {"id": "raw-id-1", "object": "audio.voice"}
        result = provider.create_voice("/tmp/sample.mp3", name="n")
        assert result["voice_id"] == "raw-id-1"

    def test_create_voice_missing_id_raises(self, provider):
        provider._fake.create_voice_result = {"name": "n"}
        with pytest.raises(Exception, match="missing id"):
            provider.create_voice("/tmp/sample.mp3")

    def test_async_synthesize_and_poll(self, provider):
        provider._fake.speech_result = {"task_id": "task-async-1", "status": "PROCESSING"}
        task = provider.async_synthesize("hello")
        assert task["task_id"] == "task-async-1"
        done = provider.poll_task(task["task_id"])
        assert done["status"] == "SUCCESS"


class TestABCDefaults:
    def test_new_abc_methods_default_to_not_implemented(self):
        from agent.tts_provider import TTSProvider

        class Minimal(TTSProvider):
            @property
            def name(self):
                return "minimal"

            def synthesize(self, text, output_path, **kw):
                return output_path

        m = Minimal()
        with pytest.raises(NotImplementedError):
            m.synthesize_dialogue([], [], "/tmp/x.mp3")
        with pytest.raises(NotImplementedError):
            m.design_voice("style", "text", "/tmp/x.mp3")
        with pytest.raises(NotImplementedError):
            m.create_voice("/tmp/sample.mp3")
        with pytest.raises(NotImplementedError):
            m.async_synthesize("text")
        with pytest.raises(NotImplementedError):
            m.poll_task("task")


# ---------------------------------------------------------------------------
# Moss STT provider (plugins/tts/moss/transcription.py) — ABC compliance
# ---------------------------------------------------------------------------


class TestMossTranscriptionProviderCompliance:
    def test_is_a_transcription_provider(self):
        from agent.transcription_provider import TranscriptionProvider
        from plugins.tts.moss.transcription import MossTranscriptionProvider

        assert issubclass(MossTranscriptionProvider, TranscriptionProvider)

    def test_name_and_default_model(self):
        from plugins.tts.moss.transcription import (
            MODEL_DIARIZE_PRO,
            MODEL_TRANSCRIBE,
            MossTranscriptionProvider,
        )

        p = MossTranscriptionProvider()
        assert p.name == "moss"
        assert p.default_model() == MODEL_TRANSCRIBE
        models = [m["id"] for m in p.list_models()]
        assert MODEL_TRANSCRIBE in models
        assert MODEL_DIARIZE_PRO in models

    def test_is_available_never_raises(self):
        from plugins.tts.moss.transcription import MossTranscriptionProvider

        assert isinstance(MossTranscriptionProvider().is_available(), bool)

    def test_setup_schema_surfaces_moss_key(self):
        from plugins.tts.moss.transcription import MossTranscriptionProvider

        schema = MossTranscriptionProvider().get_setup_schema()
        assert schema["name"] == "Moss"
        assert schema["badge"] == "paid"
        assert any(v["key"] == "MOSS_API_KEY" for v in schema["env_vars"])


# ---------------------------------------------------------------------------
# register() wiring — STT provider + tools (relationship assertions, not
# exact counts — no change-detector tests).
# ---------------------------------------------------------------------------


class FakePluginCtx:
    """Minimal stand-in for PluginContext used by register()."""

    def __init__(self):
        self.tts_providers = []
        self.stt_providers = []
        self.tools = []

    def register_tts_provider(self, provider):
        self.tts_providers.append(provider)

    def register_transcription_provider(self, provider):
        self.stt_providers.append(provider)

    def register_tool(self, **kw):
        self.tools.append(kw)


def test_register_wires_stt_provider_and_new_tools():
    from plugins.tts.moss import register

    ctx = FakePluginCtx()
    register(ctx)

    # STT provider registered under the name "moss" so `stt.provider: moss`
    # routes gateway voice messages to the plugin dispatch path.
    assert any(getattr(p, "name", None) == "moss" for p in ctx.stt_providers)
    # The TTS provider is still registered alongside.
    assert any(getattr(p, "name", None) == "moss" for p in ctx.tts_providers)

    tool_names = {t["name"] for t in ctx.tools}
    assert "moss_transcribe" in tool_names
    assert "moss_vision" in tool_names
    # Every moss tool is check_fn-gated (Spotify pattern) so non-Moss users
    # pay zero schema cost.
    for tool in ctx.tools:
        assert tool.get("toolset") == "moss"
        assert callable(tool.get("check_fn"))
        assert callable(tool.get("handler"))
        assert tool.get("schema", {}).get("name") == tool["name"]
