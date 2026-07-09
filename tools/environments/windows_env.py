"""Refresh ``os.environ["PATH"]`` / ``os.environ["PATHEXT"]`` from the Windows
Registry so that tools installed since the current process started (WinGet, MSI,
etc.) are discoverable without a full restart.

This module mirrors the pattern from
``kimix/utils/windows_env.py`` / ``kimi-cli/src/kimi_cli/utils/environment.py``
in the upstream kimi-agent project.
"""

from __future__ import annotations

import os
import sys
import threading
import time

if sys.platform == "win32":
    import ctypes
    import winreg


def ps_with_utf8(command: str) -> str:
    """Wrap a PowerShell command with UTF-8 encoding directives.

    Prepends ``[Console]::OutputEncoding`` and ``$OutputEncoding`` settings
    so PowerShell emits UTF-8 to stdout regardless of the system code page.
    Works for both ``pwsh.exe`` (PowerShell 7) and ``powershell.exe``
    (Windows PowerShell 5.1). No-op on non-Windows.

    Usage::

        cmd = ps_with_utf8("Get-ChildItem")
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], stdin=subprocess.DEVNULL, ...)

    The helper is **idempotent** — if a preamble is already present it won't
    double-prepend.  This is safe to call even on strings that may have been
    wrapped by an earlier code path.
    """
    if sys.platform != "win32":
        return command
    preamble = (
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8;"
        "$OutputEncoding=[System.Text.Encoding]::UTF8;"
        "[Console]::TreatControlCAsInput=$true;"
    )
    if command.startswith(preamble):
        return command
    return preamble + command


def _expand_registry_string(value: str) -> str:
    """Expand a ``REG_EXPAND_SZ`` value using the Windows API.

    ``os.path.expandvars`` only expands against the current process
    environment, which may be stale.  The Windows API
    ``ExpandEnvironmentStringsW`` performs a fresh expansion against
    the *system* and *user* environment blocks, giving the correct
    result even for variables that were changed externally.
    """
    if "%" not in value:
        return value
    try:
        _ExpandEnvironmentStringsW = (
            ctypes.windll.kernel32.ExpandEnvironmentStringsW
        )
        nchars = _ExpandEnvironmentStringsW(value, None, 0)
        if nchars == 0:
            return value
        buf = ctypes.create_unicode_buffer(nchars)
        _ExpandEnvironmentStringsW(value, buf, nchars)
        return buf.value
    except Exception:
        return os.path.expandvars(value)


def _read_registry_value(
    hive: int, subkey: str, name: str
) -> tuple[str | None, int | None]:
    """Read a named value from the registry.

    Returns ``(value, reg_type)``.  *value* may be ``None`` when the
    value does not exist or cannot be read.  *reg_type* is the Windows
    registry type constant (e.g. ``winreg.REG_SZ``).
    """
    try:
        with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
            val, reg_type = winreg.QueryValueEx(key, name)
            if isinstance(val, str):
                return val, reg_type
            return None, None
    except (FileNotFoundError, OSError):
        return None, None


def _merge_dedup_paths(*sources: str) -> str:
    """Merge semicolon-separated *sources*, deduplicating case-insensitively."""
    seen: set[str] = set()
    merged: list[str] = []
    for src in sources:
        for part in src.split(";"):
            part = part.strip()
            if part and part.lower() not in seen:
                seen.add(part.lower())
                merged.append(part)
    return ";".join(merged)


# ---------------------------------------------------------------------------
# Registry-refresh caching (P-042)
# ---------------------------------------------------------------------------
#
# ``refresh_env_from_registry()`` was called before EVERY PowerShell subprocess
# spawn (P-020) plus at several CLI spawn sites.  Each call re-read HKLM+HKCU
# ``Path``/``PATHEXT``, ``%``-expanded every ``REG_EXPAND_SZ`` value through a
# ctypes Win32 call, and merged/deduped the result — pure I/O the terminal tool
# paid on the hot path (root-cause-analysis.md, hotspot #6; ~5-15ms per call on
# cold machines).
#
# The Windows *Environment* registry keys change rarely — only when the user (or
# an installer the agent ran) edits PATH.  So instead of a blind time-TTL (which
# would risk missing a just-installed tool, defeating P-020's whole point), we
# key the cache on the two Environment keys' last-write timestamps
# (``QueryInfoKey``).  When the signature is unchanged since the last successful
# apply, ``os.environ`` already reflects the registry and the expensive value
# read + expand + merge is skipped; when a tool install bumps a key's mtime the
# next call refreshes immediately.  Reading the signature (~0.03ms) is ~4x
# cheaper than a full refresh (~0.15ms) and detects installs with zero staleness
# window, which a TTL cannot.
_REGISTRY_ENV_LOCK = threading.Lock()
_REGISTRY_ENV_CACHE: dict = {"applied": False, "signature": None, "last_refresh": 0.0}
# Belt-and-suspenders: force a full re-read at least this often even when the
# signature looks unchanged, so a missed change can never wedge a stale PATH for
# the life of a long-running gateway.  The signature check is the real
# mechanism; this only bounds the worst case (and still costs ~0.15ms).
_REGISTRY_ENV_MAX_AGE = 30.0


def _registry_env_signature() -> tuple | None:
    """Return a cheap change-signature for the two Environment registry keys.

    The signature is ``(hklm_last_write, hkcu_last_write)`` from
    ``winreg.QueryInfoKey`` — the keys' last-write FILETIMEs, which bump whenever
    any value in the key (e.g. ``Path``) is added/changed/removed.  Returns
    ``None`` when the timestamps can't be read (non-Windows, ``winreg`` missing,
    or a permission error), which makes the caller fall through to a full,
    uncached refresh — never a silent skip.
    """
    if sys.platform != "win32":
        return None
    sig: list = []
    for hive, subkey in (
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        ),
        (winreg.HKEY_CURRENT_USER, r"Environment"),
    ):
        try:
            with winreg.OpenKey(hive, subkey, 0, winreg.KEY_READ) as key:
                # QueryInfoKey -> (num_subkeys, num_values, last_write_filetime)
                sig.append(winreg.QueryInfoKey(key)[2])
        except Exception:
            sig.append(None)
    return tuple(sig)


def _reset_registry_env_cache() -> None:
    """Clear the registry-refresh cache (test hook / forced-cold entry point)."""
    with _REGISTRY_ENV_LOCK:
        _REGISTRY_ENV_CACHE.update(
            {"applied": False, "signature": None, "last_refresh": 0.0}
        )


def refresh_env_from_registry(force: bool = False) -> None:
    """Refresh ``os.environ["PATH"]`` and ``os.environ["PATHEXT"]``
    from the Windows registry.

    Reads both the system (HKLM) and user (HKCU) values,
    expands ``REG_EXPAND_SZ`` entries via the Windows API, and
    merges them into the current process environment.

    After calling this function, ``shutil.which`` and
    ``subprocess.Popen`` can locate binaries installed by
    external package managers (WinGet, MSI, etc.) without
    restarting the process.

    The read is cached on the Environment keys' last-write signature
    (see the module comment): when nothing has changed since the last apply the
    call returns without touching the registry values, so the terminal tool
    doesn't pay the full read on every spawn.  Pass ``force=True`` to bypass the
    cache (e.g. immediately after the agent installs a tool and must see it now,
    though a signature bump already covers that case).

    This is a no-op on non-Windows platforms.
    """
    if sys.platform != "win32":
        return

    now = time.monotonic()
    with _REGISTRY_ENV_LOCK:
        signature = _registry_env_signature()
        cache = _REGISTRY_ENV_CACHE
        fresh_enough = (now - cache["last_refresh"]) < _REGISTRY_ENV_MAX_AGE
        if (
            not force
            and cache["applied"]
            and fresh_enough
            and signature is not None
            and signature == cache["signature"]
        ):
            # Registry unchanged since the last apply — os.environ already holds
            # the merged PATH/PATHEXT, so skip the read + expand + merge.
            return
        _do_refresh_env_from_registry()
        cache["applied"] = True
        cache["signature"] = signature
        cache["last_refresh"] = now


def _do_refresh_env_from_registry() -> None:
    """Uncached body of :func:`refresh_env_from_registry` (the real read).

    Split out so the cache wrapper can skip it on a hit and tests can count how
    often the underlying registry read actually runs.
    """
    if sys.platform != "win32":
        return

    # --- PATH ---
    sys_val, sys_type = _read_registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        "Path",
    )
    usr_val, usr_type = _read_registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        "Path",
    )

    path_parts: list[str] = []
    if sys_val:
        if sys_type == winreg.REG_EXPAND_SZ:
            sys_val = _expand_registry_string(sys_val)
        path_parts.append(sys_val)
    if usr_val:
        if usr_type == winreg.REG_EXPAND_SZ:
            usr_val = _expand_registry_string(usr_val)
        path_parts.append(usr_val)

    if path_parts:
        os.environ["PATH"] = _merge_dedup_paths(*path_parts)

    # --- PATHEXT ---
    sys_val, sys_type = _read_registry_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment",
        "PATHEXT",
    )
    usr_val, usr_type = _read_registry_value(
        winreg.HKEY_CURRENT_USER,
        r"Environment",
        "PATHEXT",
    )

    pathext_parts: list[str] = []
    if sys_val:
        if sys_type == winreg.REG_EXPAND_SZ:
            sys_val = _expand_registry_string(sys_val)
        pathext_parts.append(sys_val)
    if usr_val:
        if usr_type == winreg.REG_EXPAND_SZ:
            usr_val = _expand_registry_string(usr_val)
        pathext_parts.append(usr_val)

    if pathext_parts:
        os.environ["PATHEXT"] = _merge_dedup_paths(*pathext_parts)


# ---------------------------------------------------------------------------
# FILE_ATTRIBUTE_TEMPORARY hint (P-042 #5)
# ---------------------------------------------------------------------------
#
# Short-lived scratch files (the ``.hermes-tmp`` staging file of an atomic
# write, on-disk lock files) are created, written, and unlinked/renamed in
# quick succession.  Tagging them ``FILE_ATTRIBUTE_TEMPORARY`` asks Windows to
# keep the data in the cache manager and *avoid flushing it to disk unless
# memory pressure forces it* — and real-time AV commonly deprioritises files
# carrying the bit — so the round trip through the filesystem is cheaper.
#
# The bit is a plain file attribute, so it survives an ``os.replace`` rename;
# marking a scratch file that is about to become a *permanent* file would leave
# the wrong hint on real user data.  Callers that rename a temp into place must
# therefore clear it first (``set_file_temporary(path, False)``) — exactly what
# ``_local_atomic_write`` does when the opt-in is enabled.  No-op off Windows.

_FILE_ATTRIBUTE_TEMPORARY = 0x100
_INVALID_FILE_ATTRIBUTES = 0xFFFFFFFF


def set_file_temporary(path, temporary: bool = True) -> bool:
    """Add or remove ``FILE_ATTRIBUTE_TEMPORARY`` on *path*.

    Preserves the file's other attributes (reads the current mask, flips only
    the temporary bit).  Returns ``True`` when the attribute ends up in the
    requested state, ``False`` on any failure or on non-Windows.  Never raises
    — an attribute hint must never break the write it is trying to speed up.
    """
    if sys.platform != "win32":
        return False
    p = str(path)
    try:
        kernel32 = ctypes.windll.kernel32
        current = kernel32.GetFileAttributesW(p)
        if current == _INVALID_FILE_ATTRIBUTES:
            # File not found / inaccessible.  When *setting*, a bare attribute
            # write is still worth a try; when *clearing*, nothing to do.
            if temporary:
                return bool(
                    kernel32.SetFileAttributesW(p, _FILE_ATTRIBUTE_TEMPORARY)
                )
            return False
        if temporary:
            new = current | _FILE_ATTRIBUTE_TEMPORARY
        else:
            new = current & ~_FILE_ATTRIBUTE_TEMPORARY
        if new == current:
            return True
        return bool(kernel32.SetFileAttributesW(p, new))
    except Exception:
        return False


def mark_as_temporary(path) -> bool:
    """Tag *path* ``FILE_ATTRIBUTE_TEMPORARY`` (no-op off Windows)."""
    return set_file_temporary(path, True)


# ---------------------------------------------------------------------------
# Windows Defender exclusion hint (P-042 #5 companion)
# ---------------------------------------------------------------------------
#
# Real-time Defender scanning of the many small files Hermes touches on every
# start — ``__pycache__/*.pyc`` on first import, cache/lock/temp files under
# ``HERMES_HOME`` — adds per-file open latency that shows up as ``_io.open_code``
# in the agent-init flame graph.  Excluding the Hermes home directory from
# real-time scanning removes that tax.  We never touch Defender settings
# ourselves (that needs elevation and is the user's call); this only surfaces an
# actionable suggestion for ``hermes doctor`` / setup.


def suggest_defender_exclusion(home=None) -> "str | None":
    """Return a one-line Defender-exclusion perf hint, or ``None``.

    ``None`` off Windows (or when the Hermes home can't be resolved) so callers
    can treat a falsy result as "nothing to suggest".  Purely informational —
    it does not modify any Defender configuration.
    """
    if sys.platform != "win32":
        return None
    try:
        if home is None:
            from hermes_constants import get_hermes_home

            home = get_hermes_home()
        return (
            f"Performance tip: add {home} to Windows Defender exclusions to reduce "
            "file-I/O overhead during imports and tool writes (Settings > Virus & "
            "threat protection > Manage settings > Exclusions > Add a folder)."
        )
    except Exception:
        return None
