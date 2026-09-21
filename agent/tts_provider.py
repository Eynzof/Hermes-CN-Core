"""Text-to-Speech Provider ABC.

Providers register via ``PluginContext.register_tts_provider()`` and service
``text_to_speech`` only when ``tts.provider`` names neither a built-in
(``BUILTIN_TTS_PROVIDERS`` in :mod:`tools.tts_tool`; the registry rejects
colliding names) nor a ``tts.providers.<name>: type: command`` entry (config is
more local than a plugin install, so it wins). :meth:`TTSProvider.synthesize`
should raise on failure — the dispatcher builds the ``{success: False}`` envelope.

Providers live in ``<repo>/plugins/tts/<name>/`` (built-in plugins —
``plugins/tts/moss`` ships in this fork) or ``~/.hermes/plugins/tts/<name>/``
(user-installed). The hook is additive infrastructure with a real consumer
(Moss) and is available to any future backend (Cartesia, Fish Audio, …).
"""
from __future__ import annotations


import abc
import logging
from typing import Any, Dict, Iterator, List, Optional

from agent.provider_base import CatalogProviderBase

logger = logging.getLogger(__name__)


DEFAULT_OUTPUT_FORMAT = "mp3"
VALID_OUTPUT_FORMATS = frozenset({"mp3", "wav", "ogg", "opus", "flac"})


class TTSProvider(CatalogProviderBase):
    """Abstract base class for a text-to-speech backend.

    Subclasses must implement :attr:`name` (rejected at registration if it
    collides with a built-in TTS provider name) and :meth:`synthesize`.
    """

    def list_voices(self) -> List[Dict[str, Any]]:
        """Voice catalog entries: ``{"id"}`` required; ``display`` / ``language``
        / ``gender`` / ``preview_url`` optional. Default: empty."""
        return []

    def default_voice(self) -> Optional[str]:
        """Id of the first voice entry, or None if not applicable."""
        voices = self.list_voices()
        return voices[0].get("id") if voices else None

    @abc.abstractmethod
    def synthesize(
        self, text: str, output_path: str, *, voice: Optional[str]=None, model: Optional[str]=None,
        speed: Optional[float]=None, format: str=DEFAULT_OUTPUT_FORMAT, ** extra: Any,
    ) -> str:
        """Synthesize ``text`` into ``output_path`` and return the written path.

        ``text`` is already truncated to the provider's max length and the
        parent directory exists. ``voice`` / ``model`` fall back to
        :meth:`default_voice` / :meth:`default_model` when None; ``speed`` is a
        rate multiplier providers may ignore. If ``format`` is unsupported, pick
        the closest equivalent and make ``output_path`` carry the right
        extension. Unknown ``extra`` keys must be ignored. Raise on failure.
        """

    def stream(
        self, text: str, *, voice: Optional[str] = None, model: Optional[str] = None,
        format: str = "opus", **extra: Any,
    ) -> Iterator[bytes]:
        """Stream synthesized audio bytes (optional).

        Default raises :class:`NotImplementedError`; the dispatcher then falls
        back to :meth:`synthesize` + read-whole-file. ``format`` defaults to
        ``opus`` because the primary streaming consumer is voice-bubble
        delivery (Telegram et al.), which requires Opus.
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement streaming "
            "synthesis. Use synthesize() instead, or implement stream() "
            "if your backend supports it."
        )

    def warm(self) -> None:
        """Speech output was just turned on; pre-load so the first reply is hot.
        Called from the TTS lease path when this is the configured provider.
        Best-effort; default no-op."""

    def release(self) -> None:
        """Last speech-output lease released; free resident resources (counterpart
        of :meth:`warm`). Best-effort; default no-op."""

    @property
    def voice_compatible(self) -> bool:
        """Whether output suits voice-bubble delivery (mirrors
        ``tts.providers.<name>.voice_compatible``): True → the gateway converts
        to Opus via ffmpeg if needed; False → regular audio attachment. Default
        False (opt in).

        See #17843.
        """
        return False

    # ------------------------------------------------------------------
    # Optional additive capabilities (Moss plugin, phases 4–7)
    #
    # All default to ``NotImplementedError`` so existing providers and
    # plugin registrations are unaffected. Plugin tools that expose these
    # capabilities probe with ``hasattr`` / try-except before dispatching.
    # ------------------------------------------------------------------

    def synthesize_dialogue(
        self,
        speakers: list,
        segments: list,
        output_path: str,
        *,
        model: Optional[str] = None,
        format: str = DEFAULT_OUTPUT_FORMAT,
        async_mode: bool = False,
        **extra: Any,
    ) -> str:
        """Synthesize a multi-speaker dialogue and write audio to *output_path*.

        ``speakers``: list of ``{"id": str, "voice_id": str}``.
        ``segments``: list of ``{"speaker": str, "text": str}``.

        Returns the absolute path to the written file. Raises on
        failure. Default: not supported (raises).
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement dialogue "
            "synthesis (synthesize_dialogue)."
        )

    def design_voice(
        self,
        instruction: str,
        text: str,
        output_path: str,
        *,
        model: Optional[str] = None,
        format: str = DEFAULT_OUTPUT_FORMAT,
        async_mode: bool = False,
        **extra: Any,
    ) -> str:
        """Synthesize speech in a style described by *instruction*.

        Returns the absolute path to the written audio file (the
        instruction creates a style, not a persisted voice). Raises on
        failure. Default: not supported (raises).
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement voice design "
            "(design_voice)."
        )

    def create_voice(
        self,
        audio_sample_path: str,
        *,
        name: Optional[str] = None,
        description: Optional[str] = None,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Clone a voice from a reference audio sample.

        Returns a dict with at least ``voice_id`` (the reusable voice
        id) — implementations may include the full provider response.
        Raises on failure. Default: not supported (raises).
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement voice cloning "
            "(create_voice)."
        )

    def async_synthesize(
        self,
        text: str,
        *,
        voice_id: Optional[str] = None,
        model: Optional[str] = None,
        format: str = DEFAULT_OUTPUT_FORMAT,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Start an async TTS task; returns ``{"task_id": str, ...}``.

        The result is fetched later via :meth:`poll_task`. Default: not
        supported (raises).
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement async "
            "synthesis (async_synthesize)."
        )

    def poll_task(
        self,
        task_id: str,
        timeout: float = 180.0,
        **extra: Any,
    ) -> Dict[str, Any]:
        """Poll an async TTS task to completion; returns the task result.

        Default: not supported (raises).
        """
        raise NotImplementedError(
            f"TTS provider {self.name!r} does not implement async task "
            "polling (poll_task)."
        )


def resolve_output_format(value: Optional[str]) -> str:
    """Clamp an output_format to :data:`VALID_OUTPUT_FORMATS`; invalid values
    coerce to :data:`DEFAULT_OUTPUT_FORMAT` so the tool surface forgives agent
    mistakes instead of rejecting them."""
    v = value.strip().lower() if isinstance(value, str) else None
    return v if v in VALID_OUTPUT_FORMATS else DEFAULT_OUTPUT_FORMAT
