"""Preventive SSL CA certificate checks for Hermes Agent.

This module catches broken CA bundle paths before OpenAI/httpx turns them into
opaque ``FileNotFoundError: [Errno 2] No such file or directory`` failures.
"""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

from agent.errors import SSLConfigurationError

logger = logging.getLogger(__name__)

_CA_BUNDLE_ENV_VARS = (
    "HERMES_CA_BUNDLE",
    "SSL_CERT_FILE",
    "REQUESTS_CA_BUNDLE",
    "CURL_CA_BUNDLE",
)

_SKIP_VALUES = {"1", "true", "yes", "on"}


def _skip_ssl_guard_enabled() -> bool:
    return os.getenv("HERMES_SKIP_SSL_GUARD", "").strip().lower() in _SKIP_VALUES


# ---------------------------------------------------------------------------
# Process-level validation cache
# ---------------------------------------------------------------------------
# ``verify_ca_bundle`` builds a throwaway ``ssl.create_default_context()`` to
# prove the CA bundle loads.  On Windows that certificate load costs ~200ms and
# the guard runs on EVERY ``AIAgent`` construction (see agent_init.py), so a
# gateway spawning many agents/subagents re-pays it for a bundle that cannot
# change mid-process.  We memoise the *successful* validation, keyed on a cheap
# fingerprint of the CA-relevant env vars + the certifi bundle (path/size/mtime).
# When the fingerprint is unchanged the expensive re-load is skipped; any change
# (an env var edited, certifi reinstalled) invalidates it on the next call.
# Failures are never cached — they must re-raise every time they are hit.
_last_valid_fingerprint: "tuple | None" = None


def _ca_bundle_fingerprint() -> tuple:
    """Return a cheap change-signature for the CA configuration.

    Captures the four CA-bundle env vars plus certifi's bundle identity
    (path + size + mtime) without building an ``SSLContext`` — microseconds
    versus the ~200ms validation it guards.  A missing/broken certifi yields a
    distinct signature so the guard always re-runs (and raises) for it.
    """
    parts: list = [(var, os.getenv(var) or "") for var in _CA_BUNDLE_ENV_VARS]
    try:
        import certifi

        ca = certifi.where()
        st = os.stat(ca)
        parts.append(("certifi", ca, st.st_size, st.st_mtime_ns))
    except Exception as exc:  # missing/unreadable certifi -> distinct signature
        parts.append(("certifi_error", repr(exc)))
    return tuple(parts)


def _reset_ca_bundle_cache() -> None:
    """Drop the memoised validation verdict (test hook / forced re-check)."""
    global _last_valid_fingerprint
    _last_valid_fingerprint = None


def _repair_hint() -> str:
    return (
        "Repair: python -m pip install --force-reinstall certifi openai httpx\n"
        "If you configured a custom corporate CA bundle, fix or unset the "
        "broken CA bundle environment variable."
    )


def _ssl_err(message: str) -> SSLConfigurationError:
    """Create a consistent, user-actionable SSL configuration error."""
    return SSLConfigurationError(f"{message}\n{_repair_hint()}")


def _validate_bundle_path(label: str, value: str, *, require_substantial: bool = False) -> None:
    path = Path(value).expanduser()
    if not path.exists():
        raise _ssl_err(f"{label} points to a missing CA bundle: {value}")
    if not path.is_file():
        raise _ssl_err(f"{label} does not point to a CA bundle file: {value}")
    if require_substantial and path.stat().st_size < 1024:
        raise _ssl_err(f"{label} at {value} appears corrupted (too small)")
    try:
        ctx = ssl.create_default_context(cafile=str(path))
    except Exception as exc:
        raise _ssl_err(f"{label} CA bundle at {value} cannot be loaded: {exc}") from exc
    if not ctx.get_ca_certs():
        raise _ssl_err(f"{label} CA bundle at {value} did not load any certificates")


def verify_ca_bundle() -> None:
    """Verify configured and bundled CA certificates are present and loadable.

    Raises:
        SSLConfigurationError: If an explicit CA-bundle environment variable
            points at a bad path, or if certifi's bundled ``cacert.pem`` is
            missing/corrupt.
    """
    global _last_valid_fingerprint
    if _skip_ssl_guard_enabled():
        logger.debug("SSL CA bundle guard skipped via HERMES_SKIP_SSL_GUARD")
        return

    fingerprint = _ca_bundle_fingerprint()
    if fingerprint == _last_valid_fingerprint:
        # Same CA configuration already validated in this process; the bundle is
        # immutable process state, so skip the expensive context re-load.
        return

    for env_var in _CA_BUNDLE_ENV_VARS:
        value = os.getenv(env_var)
        if value:
            _validate_bundle_path(env_var, value)

    try:
        import certifi
    except Exception as exc:
        raise _ssl_err(f"certifi is not importable: {exc}") from exc

    ca_bundle = str(certifi.where())
    _validate_bundle_path("certifi", ca_bundle, require_substantial=True)

    # Only reached when every bundle validated cleanly — cache the verdict.
    _last_valid_fingerprint = fingerprint


def verify_ca_bundle_with_fallback() -> None:
    """Backward-compatible wrapper for older call sites.

    The old PR name mentioned a platform fallback, but allowing startup with a
    broken certifi bundle still leaves httpx/OpenAI and requests call sites
    failing later. Keep the wrapper name but enforce the same check.
    """
    verify_ca_bundle()
