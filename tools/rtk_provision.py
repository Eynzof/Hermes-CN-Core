"""Runtime detection and path resolution for the rtk (CLI proxy) binary.

Mirrors the ``_find_rg()`` pattern from ``hermes_cli/dep_ensure.py``. The
``rtk`` binary is a command-line tool that natively collapses repeated output
lines, reducing token usage when commands produce long repetitive output.

Usage::

    from tools.rtk_provision import _rtk_available, _find_rtk

    if _rtk_available():
        rtk_path = _find_rtk()
        # use rtk_path to rewrite shell commands
"""

from __future__ import annotations

import functools
import platform
import shutil
import subprocess
from pathlib import Path

from hermes_constants import get_hermes_home, get_managed_tools_dir

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

RTK_VERSION = "0.43.0"
"""The rtk version we ship / look for. Keep in sync with install scripts."""

RTK_REPO = "rtk-ai/rtk"
"""GitHub org/repo for rtk releases."""


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

_IS_WINDOWS = platform.system() == "Windows"


def _rtk_binary_name() -> str:
    """Return the rtk executable name for the current platform."""
    return "rtk.exe" if _IS_WINDOWS else "rtk"


def _rtk_managed_dir() -> Path:
    """Return the Hermes-managed external tools directory.

    Mirrors ripgrep's managed directory.  New installs download rtk here so a
    broken global PATH copy is never selected.
    """
    return get_managed_tools_dir()


def _rtk_managed_path() -> Path:
    """Return the expected managed rtk binary path."""
    return _rtk_managed_dir() / _rtk_binary_name()


def _rtk_legacy_path() -> Path | None:
    """Return the legacy managed path in ``$HERMES_HOME/bin``, if different."""
    legacy = get_hermes_home() / "bin" / _rtk_binary_name()
    if legacy != _rtk_managed_path():
        return legacy
    return None


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


def _find_rtk() -> str | None:
    """Return a usable ``rtk`` executable path, or ``None``.

    Mirrors ``_find_rg()`` exactly:

    1. Prefer the Hermes-managed copy in ``get_managed_tools_dir()`` first.
    2. Fall back to the legacy ``$HERMES_HOME/bin`` location for existing installs.
    3. Fall back to ``PATH`` via ``shutil.which``.
    4. Always verify by running ``rtk --version``.
    """
    binary = _rtk_binary_name()

    managed = _rtk_managed_path()
    if managed.exists():
        try:
            subprocess.run(
                [str(managed), "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            return str(managed)
        except Exception:
            pass

    legacy = _rtk_legacy_path()
    if legacy and legacy.exists():
        try:
            subprocess.run(
                [str(legacy), "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            return str(legacy)
        except Exception:
            pass

    path_rtk = shutil.which(binary)
    if path_rtk:
        try:
            subprocess.run(
                [path_rtk, "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            return path_rtk
        except Exception:
            pass

    return None


@functools.lru_cache(maxsize=1)
def _rtk_available() -> bool:
    """Return ``True`` when ``rtk`` is installed and runnable.

    Result is cached for the process lifetime (``functools.lru_cache``).
    """
    return _find_rtk() is not None
