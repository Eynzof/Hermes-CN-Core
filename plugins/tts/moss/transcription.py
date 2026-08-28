"""Moss speech-to-text provider — transcription + multi-speaker diarization.

Implements :class:`agent.transcription_provider.TranscriptionProvider`
for the Moss (https://api.mosi.cn/v1) audio API.  Registered by
``plugins/tts/moss`` via ``PluginContext.register_transcription_provider``
and dispatched by ``tools.transcription_tools._dispatch_to_plugin_provider``
when ``stt.provider: moss`` — no core change (``moss`` is deliberately
absent from ``BUILTIN_STT_PROVIDERS`` so plugin dispatch fires).

Capabilities:

* ``transcribe`` — sync or ``async_mode`` (returns ``task_id``);
  ``diarize=True`` forces ``moss-transcribe-diarize-pro`` and returns
  normalized ``segments`` (``start``/``end``/``text``/``speaker``).
* ``poll_task`` — reuses :meth:`MossProvider.poll_task` (same
  ``GET /v1/audio/tasks/{task_id}`` endpoint as the TTS async path).

The HTTP work lives in :mod:`plugins.tts.moss.api` (self-contained,
``requests``-based); this class owns the ABC contract, config defaults
and envelope normalization.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from agent.transcription_provider import TranscriptionProvider

from plugins.tts.moss.client import resolve_moss_api_key

logger = logging.getLogger(__name__)

MODEL_TRANSCRIBE = "moss-transcribe-1.0"
MODEL_DIARIZE_PRO = "moss-transcribe-diarize-pro"

_RESPONSE_FORMATS = frozenset({"json", "text", "diarized_json"})
_DEFAULT_MAX_FILE_SIZE = 512 * 1024 * 1024  # 512 MB (Moss doc cap)


def _key_present() -> bool:
    """True when a Moss key is resolvable (shared chain or file fallback).

    Never raises — mirrors ``MossProvider.is_available()`` /
    ``streaming._key_present()``.
    """
    try:
        if resolve_moss_api_key():
            return True
        from moss_tts import _load_api_key

        return bool(_load_api_key())
    except Exception:
        return False


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value != 0
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    return False


def normalize_segments(raw: Any) -> List[Dict[str, Any]]:
    """Normalize a Moss ``segments`` payload to ``{start,end,text,speaker}``."""
    if not isinstance(raw, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        seg = {
            "start": item.get("start"),
            "end": item.get("end"),
            "text": str(item.get("text") or "").strip(),
            "speaker": str(item.get("speaker") or "").strip(),
        }
        if seg["text"] or seg["speaker"]:
            out.append(seg)
    return out


class MossTranscriptionProvider(TranscriptionProvider):
    """Moss (mosi.cn) speech-to-text backend."""

    @property
    def name(self) -> str:
        return "moss"

    @property
    def display_name(self) -> str:
        return "Moss"

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

    @classmethod
    def _stt_section(cls, cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Read ``stt.moss`` (STT-specific overrides) from the live config."""
        cfg = cfg if cfg is not None else cls._config()
        try:
            from hermes_cli.config import load_config

            full = load_config()
        except Exception:  # pragma: no cover — defensive
            full = {}
        stt = full.get("stt") if isinstance(full, dict) else {}
        stt = stt if isinstance(stt, dict) else {}
        section = stt.get("moss") if isinstance(stt, dict) else {}
        return section if isinstance(section, dict) else {}

    # ------------------------------------------------------------- availability

    def is_available(self) -> bool:
        """True when a Moss key is resolvable (shared chain or key file).

        Never raises (picker / setup display contract).
        """
        return _key_present()

    # ------------------------------------------------------------------ models

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {"id": MODEL_TRANSCRIBE, "display": "Moss Transcribe"},
            {
                "id": MODEL_DIARIZE_PRO,
                "display": "Moss Transcribe Diarize Pro",
                "diarize": True,
            },
        ]

    def default_model(self) -> Optional[str]:
        return MODEL_TRANSCRIBE

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "Moss",
            "badge": "paid",
            "tag": "中文/英文转写 · 多说话人",
            "env_vars": [
                {
                    "key": "MOSS_API_KEY",
                    "prompt": "Moss API key",
                    "url": "https://platform.mosi.cn",
                }
            ],
        }

    # --------------------------------------------------------------- transcribe

    def transcribe(
        self,
        file_path: str,
        *,
        model: Optional[str] = None,
        language: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Transcribe *file_path*; returns the standard STT envelope.

        On success::

            {
                "success": True,
                "transcript": "<text>",
                "provider": "moss",
                "segments": [...],   # when diarized
                "duration": 12.34,   # when the API returns it
                "model": "moss-transcribe-1.0",
            }

        ``async_mode=True`` (via ``extra``) returns ``{success, task_id,
        provider, async: true}`` — poll with :meth:`poll_task`.

        Never raises — exceptions become the error envelope.
        """
        try:
            cfg = self._config()
            section = self._section(cfg)
            stt = self._stt_section(cfg)

            resolved_model = str(
                model or stt.get("model") or section.get("model")
                or self.default_model() or MODEL_TRANSCRIBE
            ).strip() or MODEL_TRANSCRIBE

            diarize = _as_bool(
                extra.get("diarize", stt.get("diarize", section.get("diarize", False)))
            )
            if diarize:
                # Diarization requires the diarize-pro model per the docs.
                resolved_model = MODEL_DIARIZE_PRO

            response_format = str(
                extra.get("response_format")
                or stt.get("response_format")
                or section.get("response_format")
                or "json"
            ).strip().lower() or "json"
            if response_format not in _RESPONSE_FORMATS:
                logger.warning(
                    "Moss: unsupported response_format %r; falling back to 'json'",
                    response_format,
                )
                response_format = "json"

            max_size = _resolve_max_file_size(stt, section)
            path = Path(file_path)
            if not path.is_file():
                return _error_envelope(f"Audio file not found: {file_path}")
            size = path.stat().st_size
            if size > max_size:
                return _error_envelope(
                    f"File too large: {size / (1024*1024):.1f}MB "
                    f"(max {max_size / (1024*1024):.0f}MB)"
                )

            if language:
                logger.debug(
                    "Moss transcription has no language hint param; ignoring "
                    "language=%s (best-effort, mirrors TTS speed handling)",
                    language,
                )

            keyterms = extra.get("keyterms") or stt.get("prompt") or None
            if keyterms and resolved_model != MODEL_DIARIZE_PRO:
                logger.debug(
                    "Moss: keyterms are only supported on %s; dropping %r",
                    MODEL_DIARIZE_PRO,
                    keyterms,
                )
                keyterms = None

            async_mode = _as_bool(extra.get("async_mode"))
            if async_mode:
                data = self._api_transcribe(
                    path, model=resolved_model, diarize=diarize,
                    response_format=response_format, keyterms=keyterms,
                    async_mode=True,
                )
                task_id = str(data.get("task_id") or data.get("id") or "").strip()
                if not task_id:
                    raise ValueError(
                        f"Moss async transcription response missing task_id: {data!r}"
                    )
                return {
                    "success": True,
                    "task_id": task_id,
                    "provider": "moss",
                    "async": True,
                    "model": resolved_model,
                }

            data = self._api_transcribe(
                path, model=resolved_model, diarize=diarize,
                response_format=response_format, keyterms=keyterms,
                async_mode=False,
            )
            return self._success_envelope(data, diarize=diarize, model=resolved_model)
        except Exception as exc:  # noqa: BLE001 — ABC contract: never raise
            logger.warning("Moss transcription failed: %s", exc, exc_info=True)
            return _error_envelope(f"{type(exc).__name__}: {exc}")

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def _api_transcribe(path: Path, **kwargs: Any) -> Dict[str, Any]:
        """Thin seam so tests can monkeypatch ``api.transcribe``."""
        from plugins.tts.moss import api

        return api.transcribe(str(path), **kwargs)

    def _success_envelope(
        self, data: Dict[str, Any], *, diarize: bool, model: str
    ) -> Dict[str, Any]:
        transcript = str(
            data.get("text") or data.get("transcript") or ""
        ).strip()
        envelope: Dict[str, Any] = {
            "success": True,
            "transcript": transcript,
            "provider": "moss",
            "model": model,
        }
        duration = data.get("duration")
        if duration is not None:
            envelope["duration"] = duration
        segments = data.get("segments")
        if segments is not None or diarize:
            normalized = normalize_segments(segments)
            envelope["segments"] = normalized
            if not transcript and normalized:
                # Build a readable transcript from diarized segments.
                envelope["transcript"] = "\n".join(
                    f"[{s['speaker']}] {s['text']}".strip()
                    for s in normalized
                    if s["text"]
                )
        return envelope

    # ------------------------------------------------------------------- async

    def poll_task(
        self,
        task_id: str,
        timeout: float = 180.0,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Poll an async transcription task to completion.

        Reuses :meth:`MossProvider.poll_task` — the same
        ``GET /v1/audio/tasks/{task_id}`` endpoint as the TTS async path.
        """
        from plugins.tts.moss.provider import MossProvider

        return MossProvider().poll_task(task_id, timeout=timeout, **extra)


def _resolve_max_file_size(stt: Dict[str, Any], section: Dict[str, Any]) -> int:
    for source in (stt, section):
        raw = source.get("max_file_size") if isinstance(source, dict) else None
        if isinstance(raw, int) and raw > 0:
            return raw
    return _DEFAULT_MAX_FILE_SIZE


def _error_envelope(message: str) -> Dict[str, Any]:
    return {
        "success": False,
        "transcript": "",
        "error": message,
        "provider": "moss",
    }
