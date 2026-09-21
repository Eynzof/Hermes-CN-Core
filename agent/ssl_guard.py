"""Preventive SSL CA certificate checks — catch broken CA bundle paths before
OpenAI/httpx turns them into an opaque ``FileNotFoundError``."""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

from agent.errors import SSLConfigurationError
from utils import is_truthy_value

logger = logging.getLogger(__name__)

_CA_BUNDLE_ENV_VARS = ("HERMES_CA_BUNDLE", "SSL_CERT_FILE", "REQUESTS_CA_BUNDLE", "CURL_CA_BUNDLE")
_REPAIR_HINT = (
    "Repair: run `hermes doctor --fix` (auto-reinstalls certifi), or "
    "manually: python -m pip install --force-reinstall certifi openai httpx\n"
    "If you configured a custom corporate CA bundle, fix or unset the broken CA bundle environment variable."
)

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


def _ssl_err(message: str) -> SSLConfigurationError:
    """Create a consistent, user-actionable SSL configuration error."""
    return SSLConfigurationError(f"{message}\n{_REPAIR_HINT}")


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
    try:
        loaded_certs = ctx.get_ca_certs()
    except NotImplementedError:  # truststore-backed SSLContext (Windows) lacks get_ca_certs(); loading validated it
        return
    if not loaded_certs:
        raise _ssl_err(f"{label} CA bundle at {value} did not load any certificates")


def verify_ca_bundle() -> None:
    """Raise SSLConfigurationError when a CA-bundle env var points at a bad path or certifi's ``cacert.pem``
    is missing/corrupt."""
    global _last_valid_fingerprint
    if is_truthy_value(os.getenv("HERMES_SKIP_SSL_GUARD", "")):
        logger.debug("SSL CA bundle guard skipped via HERMES_SKIP_SSL_GUARD")
        return

    fingerprint = _ca_bundle_fingerprint()
    if fingerprint == _last_valid_fingerprint:
        # Same CA configuration already validated in this process; the bundle is
        # immutable process state, so skip the expensive context re-load.
        return

    for env_var in _CA_BUNDLE_ENV_VARS:
        if value := os.getenv(env_var):
            _validate_bundle_path(env_var, value)
    try:
        import certifi
    except Exception as exc:
        raise _ssl_err(f"certifi is not importable: {exc}") from exc
    _validate_bundle_path("certifi", str(certifi.where()), require_substantial=True)

    # Only reached when every bundle validated cleanly — cache the verdict.
    _last_valid_fingerprint = fingerprint


# ---- BEGIN PLUGIN-COMPAT (revert-scheduled; see COMPAT_MANIFEST.md) ----
# Names external plugins imported from this module before the Sep 2026 decomposition.
# Internal code MUST NOT use these (scripts/check_compat_pointers.py fails CI if it does).
# The whole block is removed by reverting the commit that added it.

def verify_ca_bundle_with_fallback() -> None:
    """Backward-compatible wrapper for older call sites.

    The old PR name mentioned a platform fallback, but allowing startup with a
    broken certifi bundle still leaves httpx/OpenAI and requests call sites
    failing later. Keep the wrapper name but enforce the same check.
    """
    verify_ca_bundle()
# ---- END PLUGIN-COMPAT ----
