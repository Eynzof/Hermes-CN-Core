"""Moss TTS provider — single-voice sync/async + dialogue + design + clone.

Implements :class:`agent.tts_provider.TTSProvider` for the Moss
(https://api.mosi.cn/v1) audio API.  Registered by ``plugins/tts/moss``
via ``PluginContext.register_tts_provider()`` and dispatched by
``tools.tts_tool._dispatch_to_plugin_provider`` when
``tts.provider: moss`` — no core dispatch changes (Moss is deliberately
absent from ``BUILTIN_TTS_PROVIDERS`` so plugin dispatch fires).

Capabilities:

* ``synthesize`` — single-voice TTS (sync ``audio``/``url`` delivery,
  optional pause via the client's ``pause: float`` param).
* ``synthesize_dialogue`` — multi-speaker dialogue (``/audio/speech/speakers``).
* ``design_voice`` — voice design from an instruction
  (``/audio/voice/generations``); returns audio, not a persisted voice.
* ``create_voice`` — voice clone from a reference audio sample
  (``/audio/voices``, multipart).
* ``async_synthesize`` / ``poll_task`` — async single-voice tasks.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.tts_provider import TTSProvider, resolve_output_format
from moss_tts import DOC_VOICES, DEFAULT_VERSION, MODEL_TTS, MODEL_TTSD, MODEL_VOICE_GENERATOR, MossError

from plugins.tts.moss.client import build_client, resolve_moss_api_key

logger = logging.getLogger(__name__)

# Formats Moss accepts directly for a single HTTP response.
_DIRECT_FORMATS = frozenset({"mp3", "wav"})
# Formats we approximate by requesting mp3 and transcoding with ffmpeg so the
# file extension matches the requested format (ABC contract).
_FFMPEG_CODECS = {
    "ogg": "libvorbis",
    "opus": "libopus",
    "flac": "flac",
}


def _has_ffmpeg() -> bool:
    return shutil.which("ffmpeg") is not None


class MossProvider(TTSProvider):
    """Moss (mosi.cn) text-to-speech backend."""

    @property
    def name(self) -> str:
        return "moss"

    @property
    def display_name(self) -> str:
        return "Moss"

    @property
    def voice_compatible(self) -> bool:
        """Opt into voice-bubble delivery (ffmpeg → Opus on the platform
        pipeline) for Telegram/Matrix/Feishu/WhatsApp/Signal."""
        return True

    # ------------------------------------------------------------------ config

    @staticmethod
    def _config() -> Dict[str, Any]:
        try:
            from tools.tts_tool import _load_tts_config

            cfg = _load_tts_config()
            return cfg if isinstance(cfg, dict) else {}
        except Exception as exc:  # pragma: no cover — defensive
            logger.debug("Could not load tts config: %s", exc)
            return {}

    @classmethod
    def _section(cls, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        cfg = cfg if cfg is not None else cls._config()
        section = cfg.get("moss") if isinstance(cfg, dict) else {}
        return section if isinstance(section, dict) else {}

    # ------------------------------------------------------------- availability

    def is_available(self) -> bool:
        """True when a Moss key is resolvable and requests is importable.

        Never raises (picker / setup display contract). Catches the
        ``ValueError`` MossClient raises when no key is found anywhere.
        """
        try:
            import requests  # noqa: F401

            if resolve_moss_api_key(self._config()):
                return True
            # Fall back to the client's own loader (env MOSS_API_KEY →
            # MOSS_KEY_FILE key file), which the shared chain does not consult.
            from moss_tts import _load_api_key

            return bool(_load_api_key())
        except Exception:
            return False

    # ------------------------------------------------------------------ voices

    def list_voices(self) -> List[Dict[str, Any]]:
        """Built-in 15 voices + cloned voices from ``GET /audio/voices``.

        Built-in entries are keyed ``voice_id`` in the docs; cloned
        entries are keyed ``id`` in the API. Both are normalized to the
        ABC's ``id`` field, and the original key is preserved as
        ``voice_id`` so text_to_speech and dialogue resolution both work.
        """
        voices: List[Dict[str, Any]] = []
        for v in DOC_VOICES:
            voices.append({
                "id": v.get("voice_id", ""),
                "voice_id": v.get("voice_id", ""),
                "display": v.get("name", v.get("voice_id", "")),
                "language": v.get("lang", ""),
                "desc": v.get("desc", ""),
                "builtin": True,
            })
        try:
            client = build_client(self._config())
            for raw in client.list_voices():
                if not isinstance(raw, dict):
                    continue
                vid = str(raw.get("id") or raw.get("voice_id") or "").strip()
                if not vid:
                    continue
                voices.append({
                    "id": vid,
                    "voice_id": vid,
                    "display": str(raw.get("name") or raw.get("display") or vid),
                    "language": str(raw.get("lang") or raw.get("language") or ""),
                    "desc": str(raw.get("desc") or raw.get("description") or ""),
                    "cloned": True,
                })
        except Exception as exc:  # noqa: BLE001 — cloned voices are best-effort
            logger.debug("Moss list_voices: could not fetch cloned voices: %s", exc)
        return voices

    def list_models(self) -> List[Dict[str, Any]]:
        max_len = self._section().get("max_text_length") or 5000
        return [
            {"id": MODEL_TTS, "display": "Moss TTS", "max_text_length": max_len},
            {"id": MODEL_TTSD, "display": "Moss TTSD (dialogue)", "max_text_length": max_len},
            {"id": MODEL_VOICE_GENERATOR, "display": "Moss voice generator (design)", "max_text_length": max_len},
        ]

    def default_voice(self) -> Optional[str]:
        cfg = self._config()
        section = self._section(cfg)
        configured = str(section.get("voice_id") or "").strip()
        if configured:
            return configured
        return super().default_voice()

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Moss",
            "badge": "paid",
            "tag": "中文/英文 TTS · 15 内置音色 · 克隆/对话",
            "env_vars": [
                {
                    "key": "MOSS_API_KEY",
                    "prompt": "Moss API key",
                    "url": "https://platform.mosi.cn",
                }
            ],
        }

    # --------------------------------------------------------------- helpers

    def _resolve_voice_id(
        self, voice: Optional[str], section: Dict[str, Any]
    ) -> Optional[str]:
        if isinstance(voice, str) and voice.strip():
            return voice.strip()
        configured = str(section.get("voice_id") or "").strip()
        if configured:
            return configured
        return self.default_voice()

    @staticmethod
    def _request_format(fmt: str) -> str:
        return "mp3" if fmt not in _DIRECT_FORMATS else fmt

    def _maybe_convert(self, path: str, fmt: str) -> str:
        """Transcode an mp3 file to *fmt* (ogg/opus/flac) when ffmpeg exists.

        Returns the converted path on success, else the original path
        (mp3 bytes) with a warning — the ABC contract's "closest
        equivalent" fallback, and the caller/pipeline repairs the
        container if the extension mismatches.
        """
        codec = _FFMPEG_CODECS.get(fmt)
        if codec is None:
            return path
        src = Path(path)
        out = src.with_suffix(f".{fmt}")
        in_place = os.path.abspath(src) == os.path.abspath(out)
        work = out.with_suffix(f".{fmt}.tmp") if in_place else out
        if not _has_ffmpeg():
            logger.warning(
                "Moss: %s requested but ffmpeg not found; returning mp3 file",
                fmt,
            )
            return path
        try:
            result = subprocess.run(
                ["ffmpeg", "-y", "-i", str(src), "-c:a", codec, str(work)],
                capture_output=True,
                timeout=120,
                stdin=subprocess.DEVNULL,
                creationflags=(
                    getattr(subprocess, "CREATE_NO_WINDOW", 0)
                    if os.name == "nt"
                    else 0
                ),
            )
            if result.returncode == 0 and work.exists() and work.stat().st_size > 0:
                if in_place:
                    os.replace(work, out)
                return str(out)
            logger.warning(
                "Moss ffmpeg conversion to %s failed (rc=%s); returning mp3 file",
                fmt,
                result.returncode,
            )
        except subprocess.TimeoutExpired:
            logger.warning("Moss ffmpeg conversion to %s timed out", fmt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Moss ffmpeg conversion to %s failed: %s", fmt, exc)
        finally:
            if in_place and work.exists():
                try:
                    work.unlink()
                except OSError:
                    pass
        return path

    @staticmethod
    def _save_speech_result(client: Any, data: Any, output_path: str) -> str:
        """Persist a speech result (bytes or a dict with ``url``)."""
        if isinstance(data, (bytes, bytearray)):
            return str(client.save_audio(data, output_path))
        if isinstance(data, dict):
            url = data.get("url")
            if isinstance(url, str) and url:
                return str(client.download(url, output_path))
            raise MossError(f"Moss speech response missing url: {data!r}")
        raise MossError(f"Unexpected Moss speech response: {type(data).__name__}")

    @staticmethod
    def _deliver_speech(client: Any, text: str, **kwargs: Any) -> Any:
        """Call ``client.speech`` preferring ``delivery_method="audio"``.

        Falls back to ``delivery_method="url"`` on any failure so a
        large/async-style response that the endpoint refuses to return as
        raw audio still works (the JSON result is returned and the caller
        downloads the ``url``).
        """
        delivery = str(kwargs.pop("delivery_method", "audio") or "audio").lower()
        try:
            return client.speech(text, delivery_method=delivery, **kwargs)
        except Exception:
            if delivery == "audio":
                logger.debug(
                    "Moss audio delivery failed; retrying with url delivery",
                    exc_info=True,
                )
                return client.speech(text, delivery_method="url", **kwargs)
            raise

    # --------------------------------------------------------------- synthesize

    def synthesize(
        self,
        text: str,
        output_path: str,
        *,
        voice: Optional[str] = None,
        model: Optional[str] = None,
        speed: Optional[float] = None,
        format: str = "mp3",
        **extra: Any,
    ) -> str:
        """Synthesize single-voice speech and write audio to *output_path*."""
        cfg = self._config()
        section = self._section(cfg)
        fmt = resolve_output_format(format)
        request_fmt = self._request_format(fmt)
        client = build_client(cfg)

        voice_id = self._resolve_voice_id(voice, section)
        model_id = str(model or section.get("model") or MODEL_TTS).strip() or MODEL_TTS
        version = str(section.get("version") or DEFAULT_VERSION).strip() or DEFAULT_VERSION
        webhook_url = str(section.get("webhook_url") or "").strip() or None
        delivery = str(section.get("delivery_method") or "audio").lower()

        pause: Optional[float] = None
        pause_raw = section.get("pause")
        if pause_raw is not None:
            try:
                pause = float(pause_raw)
            except (TypeError, ValueError):
                logger.warning("tts.moss.pause is not a number: %r; ignoring", pause_raw)

        if speed is not None:
            logger.debug("Moss single-voice TTS has no speed param; ignoring speed=%s", speed)
        if extra.get("instructions"):
            logger.debug(
                "Moss single-voice TTS has no instructions param; ignoring "
                "(use moss_voice_design for style synthesis)"
            )

        data = self._deliver_speech(
            client,
            text,
            voice_id=voice_id,
            model=model_id,
            version=version,
            response_format=request_fmt,
            pause=pause,
            webhook_url=webhook_url,
            delivery_method=delivery,
        )
        written = self._save_speech_result(client, data, output_path)
        return self._maybe_convert(written, fmt)

    # --------------------------------------------------------------- dialogue

    def synthesize_dialogue(
        self,
        speakers: list,
        segments: list,
        output_path: str,
        *,
        model: Optional[str] = None,
        format: str = "mp3",
        async_mode: bool = False,
        **extra: Any,
    ) -> Any:
        """Multi-speaker dialogue TTS.

        Sync: writes audio and returns the path string.
        ``async_mode=True``: returns ``{"task_id": str, ...}``.
        """
        cfg = self._config()
        section = self._section(cfg)
        fmt = resolve_output_format(format)
        request_fmt = self._request_format(fmt)
        client = build_client(cfg)

        model_id = str(model or section.get("dialogue_model") or MODEL_TTSD).strip() or MODEL_TTSD
        webhook_url = str(section.get("webhook_url") or "").strip() or None
        delivery = str(section.get("delivery_method") or "audio").lower()

        if async_mode:
            result = client.speakers(
                speakers,
                segments,
                model=model_id,
                response_format=request_fmt,
                delivery_method="url",
                async_mode=True,
                webhook_url=webhook_url,
            )
            task_id = result.get("task_id") if isinstance(result, dict) else None
            if not task_id:
                raise MossError(f"Moss dialogue async response missing task_id: {result!r}")
            return {"task_id": task_id, "provider": "moss", "async": True}

        try:
            data = client.speakers(
                speakers,
                segments,
                model=model_id,
                response_format=request_fmt,
                delivery_method=delivery,
                webhook_url=webhook_url,
            )
        except Exception:
            if delivery == "audio":
                logger.debug(
                    "Moss dialogue audio delivery failed; retrying with url delivery",
                    exc_info=True,
                )
                data = client.speakers(
                    speakers,
                    segments,
                    model=model_id,
                    response_format=request_fmt,
                    delivery_method="url",
                    webhook_url=webhook_url,
                )
            else:
                raise
        written = self._save_speech_result(client, data, output_path)
        return self._maybe_convert(written, fmt)

    # ------------------------------------------------------------- voice design

    def design_voice(
        self,
        instruction: str,
        text: str,
        output_path: str,
        *,
        model: Optional[str] = None,
        format: str = "mp3",
        async_mode: bool = False,
        **extra: Any,
    ) -> Any:
        """Voice design: synthesize speech in a style described by *instruction*.

        The instruction creates a style, not a persisted voice — Moss
        returns audio directly.  Sync returns the path string;
        ``async_mode=True`` returns ``{"task_id": str, ...}``.
        """
        cfg = self._config()
        section = self._section(cfg)
        fmt = resolve_output_format(format)
        request_fmt = self._request_format(fmt)
        client = build_client(cfg)

        model_id = str(model or section.get("design_model") or MODEL_VOICE_GENERATOR).strip() or MODEL_VOICE_GENERATOR
        webhook_url = str(section.get("webhook_url") or "").strip() or None
        delivery = str(section.get("delivery_method") or "audio").lower()

        if async_mode:
            result = client.voice_generations(
                instruction,
                text,
                model=model_id,
                response_format=request_fmt,
                delivery_method="url",
                async_mode=True,
                webhook_url=webhook_url,
            )
            task_id = result.get("task_id") if isinstance(result, dict) else None
            if not task_id:
                raise MossError(f"Moss voice design async response missing task_id: {result!r}")
            return {"task_id": task_id, "provider": "moss", "async": True}

        try:
            data = client.voice_generations(
                instruction,
                text,
                model=model_id,
                response_format=request_fmt,
                delivery_method=delivery,
                webhook_url=webhook_url,
            )
        except Exception:
            if delivery == "audio":
                logger.debug(
                    "Moss voice design audio delivery failed; retrying with url delivery",
                    exc_info=True,
                )
                data = client.voice_generations(
                    instruction,
                    text,
                    model=model_id,
                    response_format=request_fmt,
                    delivery_method="url",
                    webhook_url=webhook_url,
                )
            else:
                raise
        written = self._save_speech_result(client, data, output_path)
        return self._maybe_convert(written, fmt)

    # ------------------------------------------------------------- voice clone

    def create_voice(
        self,
        audio_sample_path: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Clone a voice from a reference audio sample; returns voice info.

        The Moss API keys the new voice with ``id`` (observed) or
        ``voice_id`` (defensive parse).  We return the raw provider
        response with a normalized ``voice_id`` added.
        """
        client = build_client(self._config())
        result = client.create_voice(
            audio_sample_path,
            name=name or None,
            description=description or None,
        )
        if not isinstance(result, dict):
            raise MossError(f"Unexpected create_voice response: {result!r}")
        vid = str(result.get("voice_id") or result.get("id") or "").strip()
        if not vid:
            raise MossError(f"Moss create_voice response missing id: {result!r}")
        normalized = dict(result)
        normalized["voice_id"] = vid
        return normalized

    # ------------------------------------------------------------------- async

    def async_synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        model: Optional[str] = None,
        format: str = "mp3",
        **extra: Any,
    ) -> Dict[str, Any]:
        """Start an async single-voice task; returns ``{"task_id": str, ...}``."""
        cfg = self._config()
        section = self._section(cfg)
        fmt = resolve_output_format(format)
        request_fmt = self._request_format(fmt)
        client = build_client(cfg)

        resolved_voice = self._resolve_voice_id(voice_id, section)
        model_id = str(model or section.get("model") or MODEL_TTS).strip() or MODEL_TTS
        version = str(section.get("version") or DEFAULT_VERSION).strip() or DEFAULT_VERSION
        webhook_url = str(section.get("webhook_url") or "").strip() or None

        result = client.speech(
            text,
            voice_id=resolved_voice,
            model=model_id,
            version=version,
            response_format=request_fmt,
            delivery_method="url",
            async_mode=True,
            webhook_url=webhook_url,
        )
        if not isinstance(result, dict):
            raise MossError(f"Moss async response is not JSON: {result!r}")
        task_id = result.get("task_id")
        if not task_id:
            raise MossError(f"Moss async response missing task_id: {result!r}")
        return {"task_id": task_id, "provider": "moss", "async": True}

    def poll_task(
        self,
        task_id: str,
        timeout: float = 180.0,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Poll an async task to completion; returns the terminal task dict."""
        client = build_client(self._config())
        return client.poll_task(task_id, timeout=timeout)
