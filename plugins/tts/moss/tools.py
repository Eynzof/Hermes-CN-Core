"""Moss plugin tools — dialogue TTS, voice design/clone/list, transcription, vision.

All tools are registered into the ``moss`` toolset and gated by
``_check_moss_available()`` (Spotify pattern): they stay registered so
``hermes tools`` lists them, but dispatch is blocked until a Moss API key
is configured.  Handlers return the standard ``tool_result`` /
``tool_error`` JSON envelopes.

Tools:

* ``moss_dialogue_tts`` / ``moss_voice_design`` / ``moss_voice_clone`` /
  ``moss_voice_list`` — TTS capabilities.
* ``moss_transcribe`` — transcription + multi-speaker diarization
  (``POST /v1/audio/transcriptions``).
* ``moss_vision`` — image/video understanding (``POST /v1/responses``).
"""
from __future__ import annotations

import datetime
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tts_provider import resolve_output_format
from moss_tts import MossError

from plugins.tts.moss.api import (
    MAX_AUDIO_BYTES,
    MAX_IMAGE_BYTES,
    MAX_VIDEO_BYTES,
    MossApiError,
    extract_vision_text,
    transcribe_audio,
    understand,
    upload_file,
    validate_public_url,
)
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
    if isinstance(exc, (MossError, MossApiError)):
        return tool_error(str(exc), success=False, provider="moss")
    return tool_error(
        f"Moss tool failed: {type(exc).__name__}: {exc}",
        success=False,
        provider="moss",
    )


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def _string_list(value: Any) -> List[str]:
    """Normalize a param to a list of non-empty strings."""
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, (list, tuple)):
        items = list(value)
    else:
        return []
    out: List[str] = []
    for item in items:
        text = str(item or "").strip()
        if text:
            out.append(text)
    return out


def _is_url(value: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(value).scheme in ("http", "https")


def _transcription_envelope(data: dict, model: str) -> str:
    """Build the standard success envelope from a transcriptions response."""
    envelope: Dict[str, Any] = {
        "success": True,
        "transcript": str(data.get("text") or data.get("transcript") or "").strip(),
        "provider": "moss",
        "model": model,
    }
    if data.get("segments") is not None:
        envelope["segments"] = data.get("segments")
    if data.get("duration") is not None:
        envelope["duration"] = data.get("duration")
    return tool_result(envelope)


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


def _handle_moss_transcribe(args: dict, **kw) -> str:
    """Transcribe audio (moss_transcribe) — local file, file_id, or URL."""
    try:
        task_id = str(args.get("task_id") or "").strip()
        if task_id:
            # Poll an in-flight async transcription (second call with task_id).
            from plugins.tts.moss.transcription import MossTranscriptionProvider

            done = MossTranscriptionProvider().poll_task(task_id)
            return tool_result({**done, "success": True, "provider": "moss"})

        audio_path = str(args.get("audio_path") or "").strip()
        if not audio_path:
            return tool_error(
                "audio_path is required — a local file path, a `file_id:...` "
                "handle, or a public URL",
                success=False,
                provider="moss",
            )

        model = str(args.get("model") or "moss-transcribe-1.0").strip() or "moss-transcribe-1.0"
        diarize = _truthy(args.get("diarize"))
        if diarize:
            # Diarization requires the diarize-pro model per the docs.
            model = "moss-transcribe-diarize-pro"
        response_format = str(args.get("response_format") or "json").strip() or "json"
        async_mode = _truthy(args.get("async_mode"))
        keyterms = _string_list(args.get("keyterms")) or None
        language = str(args.get("language") or "").strip()
        if language:
            logger.debug(
                "moss_transcribe: language=%s is a best-effort hint (Moss has "
                "no language param) — ignoring", language,
            )

        data = transcribe_audio(
            audio_path,
            model=model,
            diarize=diarize,
            response_format=response_format,
            keyterms=keyterms,
            async_mode=async_mode,
        )
        if async_mode:
            tid = str(data.get("task_id") or data.get("id") or "").strip()
            if not tid:
                return tool_error(
                    f"Moss async transcription response missing task_id: {data!r}",
                    success=False,
                    provider="moss",
                )
            return tool_result({
                "success": True,
                "task_id": tid,
                "provider": "moss",
                "async": True,
                "model": model,
            })
        return _transcription_envelope(data, model)
    except Exception as exc:  # noqa: BLE001
        return _moss_error(exc)


def _handle_moss_vision(args: dict, **kw) -> str:
    """Image/video understanding (moss_vision) via POST /v1/responses."""
    try:
        instruction = str(args.get("instruction") or "").strip()
        if not instruction:
            return tool_error(
                "instruction is required — what to look at / ask about the media",
                success=False,
                provider="moss",
            )

        images = _string_list(args.get("images"))
        video = str(args.get("video") or "").strip()
        image_urls = _string_list(args.get("image_urls"))
        video_url = str(args.get("video_url") or "").strip()
        model = str(args.get("model") or "moss-vl-1.0").strip() or "moss-vl-1.0"
        max_output_tokens = args.get("max_output_tokens")

        if not (images or video or image_urls or video_url):
            return tool_error(
                "Provide at least one image or one video (images / image_urls / video / video_url)",
                success=False,
                provider="moss",
            )
        if len(images) + len(image_urls) > 5:
            return tool_error(
                f"Moss vision supports at most 5 images per request "
                f"(got {len(images) + len(image_urls)})",
                success=False,
                provider="moss",
            )
        if (images or image_urls) and (video or video_url):
            return tool_error(
                "Moss vision does not support mixing images and video in one request",
                success=False,
                provider="moss",
            )
        if video and video_url:
            return tool_error(
                "Provide either `video` or `video_url`, not both",
                success=False,
                provider="moss",
            )

        # Resolve image sources: local file → upload; URL → pass through;
        # `file_id:...` → pass through.
        image_file_ids: List[str] = []
        urls: List[str] = list(image_urls)
        for src in images:
            if src.startswith("file_id:"):
                image_file_ids.append(src.split(":", 1)[1].strip())
                continue
            if _is_url(src):
                validate_public_url(src)
                urls.append(src)
                continue
            path = Path(src).expanduser()
            if not path.is_file():
                return tool_error(f"Image file not found: {src}", success=False, provider="moss")
            if path.stat().st_size > MAX_IMAGE_BYTES:
                return tool_error(
                    f"Image file too large: {path.stat().st_size / (1024*1024):.1f}MB "
                    f"(max {MAX_IMAGE_BYTES / (1024*1024):.0f}MB)",
                    success=False,
                    provider="moss",
                )
            image_file_ids.append(upload_file(str(path), purpose="image"))

        # Resolve the (single) video source.
        video_file_id: Optional[str] = None
        if video:
            if video.startswith("file_id:"):
                video_file_id = video.split(":", 1)[1].strip()
            elif _is_url(video):
                validate_public_url(video)
                video_url = video
            else:
                path = Path(video).expanduser()
                if not path.is_file():
                    return tool_error(f"Video file not found: {video}", success=False, provider="moss")
                if path.stat().st_size > MAX_VIDEO_BYTES:
                    return tool_error(
                        f"Video file too large: {path.stat().st_size / (1024*1024):.1f}MB "
                        f"(max {MAX_VIDEO_BYTES / (1024*1024):.0f}MB)",
                        success=False,
                        provider="moss",
                    )
                video_file_id = upload_file(str(path), purpose="video")

        data = understand(
            instruction,
            image_urls=urls,
            image_file_ids=image_file_ids,
            video_url=video_url or None,
            video_file_id=video_file_id,
            model=model,
            max_output_tokens=max_output_tokens,
        )
        text = extract_vision_text(data)
        status = data.get("status")
        result: Dict[str, Any] = {
            "success": True,
            "text": text,
            "status": status,
            "provider": "moss",
            "model": model,
        }
        if status == "incomplete":
            details = data.get("incomplete_details") or {}
            reason = details.get("reason") if isinstance(details, dict) else details
            if reason == "max_output_tokens":
                result["warning"] = (
                    "Response truncated because max_output_tokens was reached — "
                    "retry with a higher max_output_tokens (up to 8192) for the full answer"
                )
            else:
                result["warning"] = f"Response incomplete (reason: {reason})"
        return tool_result(result)
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

MOSS_TRANSCRIBE_SCHEMA = {
    "name": "moss_transcribe",
    "description": (
        "Moss speech-to-text: transcribe an audio file into text, with "
        "optional multi-speaker diarization (moss-transcribe-diarize-pro). "
        "Accepts a local file path, a `file_id:<id>` handle, or a public "
        "URL. Returns {transcript} and, when diarized, {segments} with "
        "start/end/text/speaker. With async_mode=true it returns a task_id; "
        "call again with that task_id to poll for the result."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "audio_path": {
                "type": "string",
                "description": (
                    "Audio to transcribe: a local file path, a `file_id:<id>` "
                    "handle from a previous Moss file upload, or a public URL. "
                    "Localhost/private URLs are rejected."
                ),
            },
            "task_id": {
                "type": "string",
                "description": (
                    "When set, poll a previously-started async transcription "
                    "(from async_mode=true) and return its final result."
                ),
            },
            "model": {
                "type": "string",
                "enum": ["moss-transcribe-1.0", "moss-transcribe-diarize-pro"],
                "description": (
                    "Transcription model. Diarization automatically uses "
                    "moss-transcribe-diarize-pro when diarize=true."
                ),
            },
            "diarize": {
                "type": "boolean",
                "description": (
                    "When true, force moss-transcribe-diarize-pro and return "
                    "speaker-separated segments."
                ),
            },
            "keyterms": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 20,
                "description": (
                    "Vocabulary boost terms (≤20, ≤30 chars each). Only "
                    "supported on the diarize-pro model; ignored otherwise."
                ),
            },
            "response_format": {
                "type": "string",
                "enum": ["json", "text", "diarized_json"],
                "description": "Response shape. json returns text + segments.",
            },
            "language": {
                "type": "string",
                "description": "Optional language hint (logged/ignored by the backend — Moss has no language param).",
            },
            "async_mode": {
                "type": "boolean",
                "description": "Start an async task and return its task_id; poll it by calling moss_transcribe with task_id.",
            },
        },
        "required": ["audio_path"],
    },
}

MOSS_VISION_SCHEMA = {
    "name": "moss_vision",
    "description": (
        "Moss MOSS-VL: image/video understanding (OCR, captioning, video "
        "Q&A) via the moss-vl-1.0 model. Pass 1-5 images OR exactly 1 video "
        "(never mixed). Each media item may be a local file path, a "
        "`file_id:<id>` handle, or a public URL. Returns {text, status}. "
        "When status is 'incomplete' (max_output_tokens truncation) a "
        "warning is included — retry with a higher max_output_tokens."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "instruction": {
                "type": "string",
                "description": "What to look at / ask about the media (e.g. 'OCR this receipt', 'describe the scene').",
            },
            "images": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Images: local paths, `file_id:<id>` handles, or public URLs (≤5 total across images + image_urls).",
            },
            "image_urls": {
                "type": "array",
                "items": {"type": "string"},
                "maxItems": 5,
                "description": "Explicit public image URLs (alternative to images).",
            },
            "video": {
                "type": "string",
                "description": "Single video: local path, `file_id:<id>` handle, or public URL (cannot be combined with images).",
            },
            "video_url": {
                "type": "string",
                "description": "Explicit public video URL (alternative to video).",
            },
            "model": {
                "type": "string",
                "description": "Moss vision model (default moss-vl-1.0; moss-vl-1.0-2026-07-08 accepted).",
            },
            "max_output_tokens": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8192,
                "description": "Cap on output tokens (1-8192). Truncation is reported via status/warning.",
            },
        },
        "required": ["instruction"],
    },
}
