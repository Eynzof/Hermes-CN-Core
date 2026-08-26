"""Moss (mosi.cn) TTS plugin — bundled, auto-loaded.

Registers:

* :class:`MossProvider` (``agent.tts_provider.TTSProvider``) under the
  name ``moss`` so ``tts.provider: moss`` routes ``text_to_speech``
  through the plugin dispatch path (no core change — ``moss`` is
  deliberately not in ``BUILTIN_TTS_PROVIDERS``).
* :class:`MossStreamer` (``tools.tts_streaming`` registry) so
  conversational streaming picks it up when ``tts.provider: moss`` or
  ``tts.streaming.provider: moss``.
* Four gated tools in the ``moss`` toolset: ``moss_dialogue_tts``,
  ``moss_voice_design``, ``moss_voice_clone``, ``moss_voice_list``.
  Each is check_fn-gated on a configured Moss key (Spotify pattern), so
  they are listed by ``hermes tools`` but blocked until configured.

Credentials: ``MOSS_API_KEY`` env/.env, ``tts.moss.api_key`` in
config.yaml, ``hermes auth add moss``, or a key file path configured via
``MOSS_KEY_FILE`` (see ``moss_tts.MossClient``).
"""
from __future__ import annotations


def register(ctx) -> None:
    """Register provider, streamer, and gated tools. Called by the loader."""
    from plugins.tts.moss.provider import MossProvider

    ctx.register_tts_provider(MossProvider())

    # Importing the module runs the @register("moss") decorator, populating
    # tools/tts_streaming._REGISTRY.
    from plugins.tts.moss import streaming  # noqa: F401

    from plugins.tts.moss.tools import (
        MOSS_DIALOGUE_TTS_SCHEMA,
        MOSS_VOICE_CLONE_SCHEMA,
        MOSS_VOICE_DESIGN_SCHEMA,
        MOSS_VOICE_LIST_SCHEMA,
        _check_moss_available,
        _handle_moss_dialogue_tts,
        _handle_moss_voice_clone,
        _handle_moss_voice_design,
        _handle_moss_voice_list,
    )

    ctx.register_tool(
        name="moss_dialogue_tts",
        toolset="moss",
        schema=MOSS_DIALOGUE_TTS_SCHEMA,
        handler=_handle_moss_dialogue_tts,
        check_fn=_check_moss_available,
        emoji="🗣️",
    )
    ctx.register_tool(
        name="moss_voice_design",
        toolset="moss",
        schema=MOSS_VOICE_DESIGN_SCHEMA,
        handler=_handle_moss_voice_design,
        check_fn=_check_moss_available,
        emoji="🎨",
    )
    ctx.register_tool(
        name="moss_voice_clone",
        toolset="moss",
        schema=MOSS_VOICE_CLONE_SCHEMA,
        handler=_handle_moss_voice_clone,
        check_fn=_check_moss_available,
        emoji="🧬",
    )
    ctx.register_tool(
        name="moss_voice_list",
        toolset="moss",
        schema=MOSS_VOICE_LIST_SCHEMA,
        handler=_handle_moss_voice_list,
        check_fn=_check_moss_available,
        emoji="📋",
    )
