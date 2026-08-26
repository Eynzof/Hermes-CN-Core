"""Moss plugin tools — dialogue TTS, voice design, voice clone, voice list.

All tools are registered into the ``moss`` toolset and gated by
``_check_moss_available()`` (Spotify pattern): they stay registered so
``hermes tools`` lists them, but dispatch is blocked until a Moss API key
is configured.  Handlers return the standard ``tool_result`` /
``tool_error`` JSON envelopes.
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tts_provider import resolve_output_format
from moss_tts import MossError

from plugins.tts.moss.client import build_client
from plugins.tts.moss.provider import MossProvider
from tools.registry import tool_error, tool_result

logger = logging.getLogger(__name__)

_DEFAULT_MAX_SEGMENTS = 20
_DEFAULT_MAX_TEXT_LENGTH = 5000

# ---------------------------------------------------------------------------
# availability gate
# ---------------------------------------------------------------------------


def _check_moss_available() -> bool:
    try:
        return MossProvider().is_available()
    except Exception:
        return False


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _provider() -> MossProvider:
    return MossProvider()


def _default_output_dir() -> Path:
    try:
        from hermes_constants import get_hermes_dir

        return Path(get_hermes_dir("cache/audio", "audio_cache"))
    except Exception:
        return Path("cache") / "audio"


def _resolve_output_path(
    output_path: Optional[str], fmt: str, prefix: str
) -> str:
    """Return a caller-supplied path or a timestamped default under cache/audio."""
    if output_path and str(output_path).strip():
        return str(Path(output_path).expanduser())
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    out_dir = _default_output_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    return str(out_dir / f"{prefix}_{stamp}.{fmt}")


def _max_text_length() -> int:
    section = MossProvider._section()
    raw = section.get("max_text_length")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _DEFAULT_MAX_TEXT_LENGTH


def _media_tag(path: str) -> str:
    return f"[[audio_as_voice]]\nMEDIA:{path}"


def _audio_result(path: str, provider: str = "moss") -> str:
    return tool_result({
        "success": True,
        "file_path": path,
        "MEDIA": path,
        "media_tag": _media_tag(path),
        "provider": provider,
    })


def _moss_error(exc: Exception) -> str:
    if isinstance(exc, MossError):
        return tool_error(str(exc), success=False, provider="moss")
    return tool_error(
        f"Moss tool failed: {type(exc).__name__}: {exc}",
        success=False,
        provider="moss",
    )


def _validate_speakers(speakers: Any) -> List[Dict[str, str]]:
    """Validate the dialogue ``speakers`` list; returns normalized entries."""
    if not isinstance(speakers, list) or not speakers:
        raise ValueError("speakers must be a non-empty list of {id, voice_id}")
    normalized: List[Dict[str, str]] = []
    seen: set[str] = set()
    for i, spk in enumerate(speakers):
        if not isinstance(spk, dict):
            raise ValueError(f"speakers[{i}] must be an object with id and voice_id")
        sid = str(spk.get("id") or "").strip()
        voice_id = str(spk.get("voice_id") or spk.get("id") or "").strip()
        if not sid:
            raise ValueError(f"speakers[{i}] is missing required field 'id'")
        if not voice_id:
            raise ValueError(f"speakers[{i}] is missing required field 'voice_id'")
        if sid in seen:
            raise ValueError(f"speakers[{i}] duplicates speaker id {sid!r}")
        seen.add(sid)
        normalized.append({"id": sid, "voice_id": voice_id})
    return normalized


def _validate_segments(
    segments: Any, declared: set[str], max_text: int
) -> List[Dict[str, str]]:
    """Validate dialogue ``segments``; every speaker must be declared."""
    if not isinstance(segments, list) or not segments:
        raise ValueError("segments must be a non-empty list of {speaker, text}")
    normalized: List[Dict[str, str]] = []
    for i, seg in enumerate(segments):
        if not isinstance(seg, dict):
            raise ValueError(f"segments[{i}] must be an object with speaker and text")
        speaker = str(seg.get("speaker") or "").strip()
        text = str(seg.get("text") or "").strip()
        if not speaker:
            raise ValueError(f"segments[{i}] is missing required field 'speaker'")
        if not text:
            raise ValueError(f"segments[{i}] has empty text")
        if speaker not in declared:
            raise ValueError(
                f"segments[{i}] references speaker {speaker!r} which is not "
                "declared in speakers"
            )
        if len(text) > max_text:
            raise ValueError(
                f"segments[{i}] text is {len(text)} chars, exceeding the "
                f"Moss per-request cap of {max_text}"
            )
        normalized.append({"speaker": speaker, "text": text})
    return normalized


# ---------------------------------------------------------------------------
# handlers
# ---------------------------------------------------------------------------


def _handle_moss_dialogue_tts(args: dict, **kw) -> str:
    """Multi-speaker dialogue synthesis (moss_dialogue_tts)."""
    try:
        max_text = _max_text_length()
        speakers = _validate_speakers(args.get("speakers"))
        declared = {s["id"] for s in speakers}
        segments = _validate_segments(args.get("segments"), declared, max_text)
        if len(segments) > _DEFAULT_MAX_SEGMENTS:
            logger.warning(
                "moss_dialogue_tts: %d segments exceeds the recommended %d; "
                "the API may truncate or reject the request",
                len(segments),
                _DEFAULT_MAX_SEGMENTS,
            )
        fmt = resolve_output_format(args.get("response_format") or "mp3")
        output_path = _resolve_output_path(
            args.get("output_path"), fmt, "moss_dialogue"
        )
        async_mode = bool(args.get("async_mode"))
        result = _provider().synthesize_dialogue(
            speakers,
            segments,
            output_path,
            model=args.get("model"),
            format=fmt,
            async_mode=async_mode,
        )
        if isinstance(result, dict):
            return tool_result({**result, "success": True})
        return _audio_result(result)
    except Exception as exc:  # noqa: BLE001
        return _moss_error(exc)


def _handle_moss_voice_design(args: dict, **kw) -> str:
    """Voice design: synthesize speech in a described style (moss_voice_design)."""
    try:
        instruction = str(args.get("instruction") or "").strip()
        text = str(args.get("text") or "").strip()
        if not instruction:
            return tool_error(
                "instruction is required (describes the speaking style)",
                success=False,
                provider="moss",
            )
        if not text:
            return tool_error(
                "text is required (the script to speak)",
                success=False,
                provider="moss",
            )
        if len(text) > _max_text_length():
            return tool_error(
                f"text is {len(text)} chars, exceeding the Moss per-request "
                f"cap of {_max_text_length()}",
                success=False,
                provider="moss",
            )
        fmt = resolve_output_format(args.get("response_format") or "mp3")
        output_path = _resolve_output_path(
            args.get("output_path"), fmt, "moss_design"
        )
        async_mode = bool(args.get("async_mode"))
        result = _provider().design_voice(
            instruction,
            text,
            output_path,
            model=args.get("model"),
            format=fmt,
            async_mode=async_mode,
        )
        if isinstance(result, dict):
            return tool_result({**result, "success": True})
        return _audio_result(result)
    except Exception as exc:  # noqa: BLE001
        return _moss_error(exc)


def _handle_moss_voice_clone(args: dict, **kw) -> str:
    """Clone a voice from a reference audio sample (moss_voice_clone)."""
    try:
        sample = str(args.get("audio_sample_path") or "").strip()
        if not sample:
            return tool_error(
                "audio_sample_path is required (path to a reference audio file)",
                success=False,
                provider="moss",
            )
        sample_path = Path(sample).expanduser()
        if not sample_path.is_file():
            return tool_error(
                f"audio_sample_path not found: {sample_path}",
                success=False,
                provider="moss",
            )
        voice = _provider().create_voice(
            str(sample_path),
            name=args.get("name"),
            description=args.get("description"),
        )
        return tool_result({
            "success": True,
            "voice_id": voice.get("voice_id"),
            "voice": voice,
            "provider": "moss",
        })
    except Exception as exc:  # noqa: BLE001
        return _moss_error(exc)


def _handle_moss_voice_list(args: dict, **kw) -> str:
    """List Moss voices (built-in 15 + cloned) (moss_voice_list)."""
    try:
        voices = _provider().list_voices()
        return tool_result({
            "success": True,
            "voices": voices,
            "count": len(voices),
            "provider": "moss",
        })
    except Exception as exc:  # noqa: BLE001
        return _moss_error(exc)


# ---------------------------------------------------------------------------
# schemas
# ---------------------------------------------------------------------------

_COMMON_STRING = {"type": "string"}
_COMMON_BOOL = {"type": "boolean"}

_SPEAKER_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string", "description": "Short speaker label used by segments, e.g. 'a'"},
        "voice_id": {"type": "string", "description": "Moss voice id (built-in voice_id or cloned id)"},
    },
    "required": ["id", "voice_id"],
}

_SEGMENT_SCHEMA = {
    "type": "object",
    "properties": {
        "speaker": {"type": "string", "description": "Must match a speaker id declared in speakers"},
        "text": {"type": "string", "description": "Text spoken by this speaker"},
    },
    "required": ["speaker", "text"],
}

MOSS_DIALOGUE_TTS_SCHEMA = {
    "name": "moss_dialogue_tts",
    "description": (
        "Moss multi-speaker dialogue TTS. Synthesize a script with multiple "
        "speakers (each mapped to a voice_id) into one audio file. Returns a "
        "MEDIA: path for platform delivery. Requires a configured Moss API key. "
        "Do not use the generic long-form splitter on this — segments must stay "
        "whole."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "speakers": {
                "type": "array",
                "items": _SPEAKER_SCHEMA,
                "description": "Speakers: [{id, voice_id}, ...]. Voice ids come from moss_voice_list.",
            },
            "segments": {
                "type": "array",
                "items": _SEGMENT_SCHEMA,
                "description": "Segments: [{speaker, text}, ...]. Every speaker must be declared; ≤20 segments.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional absolute path for the output audio; defaults to the cache audio dir.",
            },
            "model": {
                "type": "string",
                "description": "Optional Moss model (default moss-ttsd).",
            },
            "response_format": {
                "type": "string",
                "enum": ["mp3", "wav", "ogg", "opus", "flac"],
                "description": "Output format (ogg/opus/flac are transcoded from mp3 via ffmpeg when available).",
            },
            "async_mode": {
                "type": "boolean",
                "description": "When true, start an async task and return its task_id instead of audio.",
            },
        },
        "required": ["speakers", "segments"],
    },
}

MOSS_VOICE_DESIGN_SCHEMA = {
    "name": "moss_voice_design",
    "description": (
        "Moss voice design: synthesize speech in a style described by an "
        "instruction (e.g. 'a warm, energetic podcast host'). The instruction "
        "creates a style, NOT a persisted voice — Moss returns the audio "
        "directly. Returns a MEDIA: path."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": "Style description for the generated voice.",
            },
            "text": {
                "type": "string",
                "description": "The script to speak in that style.",
            },
            "output_path": {
                "type": "string",
                "description": "Optional absolute path for the output audio; defaults to the cache audio dir.",
            },
            "model": {
                "type": "string",
                "description": "Optional Moss model (default moss-voice-generator).",
            },
            "response_format": {
                "type": "string",
                "enum": ["mp3", "wav", "ogg", "opus", "flac"],
            },
            "async_mode": {
                "type": "boolean",
                "description": "When true, start an async task and return its task_id instead of audio.",
            },
        },
        "required": ["instruction", "text"],
    },
}

MOSS_VOICE_CLONE_SCHEMA = {
    "name": "moss_voice_clone",
    "description": (
        "Clone a voice from a reference audio sample (mp3/wav). Returns a "
        "reusable voice_id that can be passed to moss_dialogue_tts speakers or "
        "used as tts.moss.voice_id. Cloned voices also appear in moss_voice_list."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "audio_sample_path": {
                "type": "string",
                "description": "Absolute path to a reference audio file (mp3/wav).",
            },
            "name": {"type": "string", "description": "Optional display name for the cloned voice."},
            "description": {"type": "string", "description": "Optional description for the cloned voice."},
        },
        "required": ["audio_sample_path"],
    },
}

MOSS_VOICE_LIST_SCHEMA = {
    "name": "moss_voice_list",
    "description": (
        "List Moss voices: the 15 built-in voices plus any voices you have "
        "cloned. Each entry has an id (use as voice_id in text_to_speech "
        "config, moss_dialogue_tts speakers, etc.)."
    ),
    "parameters": {
        "type": "object",
        "properties": {},
    },
}
