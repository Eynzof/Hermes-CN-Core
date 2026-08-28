"""Moss client construction — key + config resolution.

Key resolution follows the repo's single owner for STT/TTS credentials
(:func:`tools.tool_backend_helpers.resolve_provider_secret`):

1. ``tts.moss.api_key`` in config.yaml
2. env / ``~/.hermes/.env`` / active profile secret scope (``MOSS_API_KEY``)
3. the credential pool (``hermes auth add moss``)

When none of those find a key we pass ``api_key=None`` and let
:class:`moss_tts.MossClient` fall back to its own loader (env
``MOSS_API_KEY`` → key file at ``MOSS_KEY_FILE``).  If that also finds
nothing, ``MossClient.__init__`` raises :class:`ValueError` — callers
should treat that as "Moss unavailable", not a hard failure.
"""
from __future__ import annotations

import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)


def _load_tts_config() -> Dict[str, Any]:
    """Read the live TTS config (monkeypatch-friendly for tests)."""
    try:
        from tools.tts_tool import _load_tts_config

        cfg = _load_tts_config()
        return cfg if isinstance(cfg, dict) else {}
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Could not load tts config: %s", exc)
        return {}


def resolve_moss_api_key(tts_config: Dict[str, Any] | None = None) -> str:
    """Return the Moss API key via the shared provider-secret chain.

    Returns ``""`` when no key is found anywhere — the caller may fall
    back to :class:`MossClient`'s own file loader.
    """
    cfg = tts_config if tts_config is not None else _load_tts_config()
    section = cfg.get("moss") if isinstance(cfg, dict) else {}
    config_value = (
        str(section.get("api_key") or "").strip()
        if isinstance(section, dict)
        else ""
    )
    try:
        from tools.tool_backend_helpers import resolve_provider_secret

        return (
            resolve_provider_secret(
                "MOSS_API_KEY", "moss", config_value=config_value
            )
            or ""
        ).strip()
    except Exception as exc:  # pragma: no cover — defensive
        logger.debug("Moss key resolution failed: %s", exc)
        return config_value


def _resolve_endpoint(cfg: Dict[str, Any]) -> tuple[str, float]:
    """Resolve the base URL + timeout for the ``tts.moss`` section.

    Returns ``(base_url, timeout)`` with defaults
    ``https://api.mosi.cn/v1`` and ``60.0``. Used by both
    :func:`build_client` and the plugin's direct-HTTP layer
    (:func:`build_http_kwargs` / ``plugins.tts.moss.api``).
    """
    section = cfg.get("moss") if isinstance(cfg, dict) else {}
    section = section if isinstance(section, dict) else {}
    base_url = str(section.get("base_url") or "https://api.mosi.cn/v1").strip()
    try:
        timeout = float(section.get("timeout") or 60)
    except (TypeError, ValueError):
        timeout = 60.0
    return base_url.rstrip("/"), timeout


def build_http_kwargs(tts_config: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """Resolve key / base_url / timeout / auth headers for direct HTTP calls.

    This is the single resolution seam for the plugin's self-contained
    HTTP layer (``plugins.tts.moss.api``): transcription, file upload and
    MOSS-VL reuse the same key + endpoint + timeout resolution as TTS, so
    one ``tts.moss.api_key`` / ``MOSS_API_KEY`` / ``tts.moss.base_url`` /
    ``tts.moss.timeout`` configuration covers all three capabilities.

    Returns::

        {
            "api_key": str,   # "" when no key is configured anywhere
            "base_url": str,  # https://api.mosi.cn/v1 by default (no trailing /)
            "timeout": float, # seconds
            "headers": {..., "Authorization": "Bearer <key>"},  # empty when no key
        }
    """
    cfg = tts_config if tts_config is not None else _load_tts_config()
    base_url, timeout = _resolve_endpoint(cfg)
    api_key = resolve_moss_api_key(cfg)
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    return {
        "api_key": api_key,
        "base_url": base_url,
        "timeout": timeout,
        "headers": headers,
    }


def build_client(tts_config: Dict[str, Any] | None = None) -> Any:
    """Build a :class:`moss_tts.MossClient`, resolving key + config.

    Honors ``tts.moss.base_url`` and ``tts.moss.timeout`` when present.

    Raises:
        ValueError: when no API key is found anywhere (``MossClient``
            contract — callers should catch it and report unavailable).
        ImportError: when ``moss_tts`` / ``requests`` cannot be imported.
    """
    from moss_tts import MossClient

    cfg = tts_config if tts_config is not None else _load_tts_config()
    base_url, timeout = _resolve_endpoint(cfg)

    kwargs: Dict[str, Any] = {}
    if base_url and base_url != "https://api.mosi.cn/v1":
        kwargs["base_url"] = base_url
    kwargs["timeout"] = timeout

    api_key = resolve_moss_api_key(cfg)
    if api_key:
        kwargs["api_key"] = api_key
    # api_key=None lets MossClient fall back to env MOSS_API_KEY →
    # MOSS_KEY_FILE (configurable key file path).
    return MossClient(**kwargs)
