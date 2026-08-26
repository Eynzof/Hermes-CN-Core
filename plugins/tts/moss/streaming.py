"""Moss streaming TTS — SSE → int16 PCM chunks.

Registers :class:`MossStreamer` in ``tools/tts_streaming._REGISTRY`` via
the ``@register("moss")`` decorator.  Both consumers
(``tools.tts_tool.stream_tts_to_speaker`` and the gateway
``StreamingTTSConsumer``) resolve it automatically when
``tts.provider: moss`` (or ``tts.streaming.provider: moss``) — no core
change.

``sample_rate`` is a pinned class constant on purpose: the consumers read
``streamer.sample_rate`` **once at init**, before the SSE stream starts,
so the mid-stream ``speech.created.sample_rate`` value can only be used as
a cross-check, never renegotiated.
"""
from __future__ import annotations

import base64
import logging
from typing import Iterator

from moss_tts import DEFAULT_VERSION, MODEL_TTS, MossError

from plugins.tts.moss.client import build_client, resolve_moss_api_key
from tools.tts_streaming import _STREAM_SENTENCE_BYTE_CAP, StreamingTTSProvider, register

logger = logging.getLogger(__name__)


def _key_present() -> bool:
    """True when a Moss key is resolvable (shared chain or file fallback).

    Never raises — mirrors ``MossProvider.is_available()``.
    """
    try:
        if resolve_moss_api_key():
            return True
        from moss_tts import _load_api_key

        return bool(_load_api_key())
    except Exception:
        return False


@register("moss")
class MossStreamer(StreamingTTSProvider):
    """Moss ``/v1/audio/speech`` streaming (SSE → base64 PCM)."""

    # Pinned from a live probe (Phase 0, 2026-08-26: speech.created
    # reported 48000) — see plan.md Phase 0 / Phase 2.  The consumers read
    # this once at init and cannot renegotiate mid-stream.
    sample_rate: int = 48000
    channels: int = 1
    sample_width: int = 2

    @staticmethod
    def available() -> bool:
        return _key_present()

    def stream(self, text: str) -> Iterator[bytes]:
        client = build_client(self.tts_config)
        section = self.section or {}
        voice_id = str(section.get("voice_id") or "").strip() or None
        model = str(section.get("model") or MODEL_TTS).strip() or MODEL_TTS
        version = str(section.get("version") or DEFAULT_VERSION).strip() or DEFAULT_VERSION

        pause: float | None = None
        pause_raw = section.get("pause")
        if pause_raw is not None:
            try:
                pause = float(pause_raw)
            except (TypeError, ValueError):
                logger.warning(
                    "tts.moss.pause is not a number: %r; ignoring", pause_raw
                )

        total = 0
        for event in client.speech_stream(
            text,
            voice_id=voice_id,
            model=model,
            version=version,
            response_format="pcm",
            stream_format="sse",
            pause=pause,
        ):
            etype = event.get("type")
            if etype == "speech.created":
                live = event.get("sample_rate")
                if live is not None:
                    try:
                        if int(live) != self.sample_rate:
                            logger.warning(
                                "Moss streaming sample_rate %s != pinned %s "
                                "(consumers cannot renegotiate mid-stream)",
                                live,
                                self.sample_rate,
                            )
                    except (TypeError, ValueError):
                        logger.warning(
                            "Moss streaming sample_rate is not an int: %r", live
                        )
            elif etype == "speech.audio.delta":
                try:
                    chunk = base64.b64decode(event["audio"])
                except (KeyError, TypeError, ValueError) as exc:
                    logger.warning("Moss stream: bad audio delta: %s", exc)
                    continue
                total += len(chunk)
                if total > _STREAM_SENTENCE_BYTE_CAP:
                    logger.warning(
                        "Moss stream exceeded %d bytes for one sentence; truncating",
                        _STREAM_SENTENCE_BYTE_CAP,
                    )
                    return
                yield chunk
            elif etype == "speech.audio.done":
                return
            elif etype == "error":
                raise MossError(f"Moss stream error event: {event}")
