"""WMI-free, cached host/OS detection helpers.

On Python 3.12+ the stdlib :mod:`platform` module answers ``system()`` /
``release()`` / ``machine()`` by building :func:`platform.uname`, which on
Windows queries Windows Management Instrumentation through the ``_wmi`` builtin
(``platform.win32_ver`` and ``platform._get_machine_win32`` each issue an
``_wmi.exec_query``).  A single *cold* ``platform.system()`` call therefore
costs ~40-90ms and shows up as the ``_wmi.exec_query`` agent-init hotspot — and
because dozens of modules define ``_IS_WINDOWS = platform.system() == "Windows"``
at *module scope*, that WMI probe is paid during the import cascade, before the
agent has done any real work.

These helpers answer the same questions without ever touching WMI:

* OS-family checks (:func:`is_windows`) use ``sys.platform`` — a build-time
  constant, so there is no ``uname()`` / WMI round trip at all.
* The Windows *release* label (:func:`windows_release`) is derived from
  ``sys.getwindowsversion()`` using the same version→name table CPython's
  ``platform`` module uses, so the string matches ``platform.release()``
  (e.g. ``"11"``) while skipping the ``win32_ver`` WMI ``exec_query``.

The module is import-cheap by design: only ``functools`` / ``sys`` at load
time and no project imports, so the earliest-loaded modules
(``hermes_cli/config.py`` and friends) can use it without dragging in heavy
dependencies or re-introducing the WMI probe.
"""

from __future__ import annotations

import sys


def is_windows() -> bool:
    """Return ``True`` on native Windows.

    Equivalent to ``platform.system() == "Windows"`` for Hermes's purposes
    (choosing Windows subprocess flags, PowerShell, ``winreg`` paths, …) but
    resolved from the ``sys.platform`` constant, so it never triggers the
    ``platform.uname()`` WMI probe that the ``platform``-based idiom pays on
    Python 3.12+.
    """
    return sys.platform == "win32"


def is_macos() -> bool:
    """Return ``True`` on macOS (WMI-free; ``sys.platform`` constant)."""
    return sys.platform == "darwin"


def is_linux() -> bool:
    """Return ``True`` on Linux (WMI-free; ``sys.platform`` constant)."""
    return sys.platform.startswith("linux")


# Version → client-release name, mirroring CPython's
# ``platform._WIN32_CLIENT_RELEASES`` (ordered high→low for a first-match
# lookup).  We only need the ``release`` string, which is a pure function of the
# ``(major, minor, build)`` triple from ``sys.getwindowsversion()`` — the WMI
# probe in ``platform.win32_ver`` is only used to refine the *product type*
# (server vs. client), which none of our callers need.
_WIN32_CLIENT_RELEASES = (
    ((10, 1, 0), "post11"),
    ((10, 0, 22000), "11"),
    ((6, 4, 0), "10"),
    ((6, 3, 0), "8.1"),
    ((6, 2, 0), "8"),
    ((6, 1, 0), "7"),
    ((6, 0, 0), "Vista"),
    ((5, 2, 0), "XP64"),
    ((5, 1, 0), "XP"),
    ((5, 0, 0), "2000"),
)


def windows_release() -> str:
    """Return the Windows release label (e.g. ``"10"`` / ``"11"``) without WMI.

    Matches ``platform.release()`` for supported Windows versions but derives
    the answer from ``sys.getwindowsversion()`` (the build number), so it never
    issues the ``platform.win32_ver()`` WMI ``exec_query``.  Returns ``""`` off
    Windows or when the version cannot be read — callers should treat the empty
    string as "unknown release" and omit it.

    Deliberately un-cached: ``sys.getwindowsversion()`` is a cheap in-memory
    lookup (no WMI, no I/O), and skipping the cache keeps the helper correct
    under tests that monkeypatch ``sys.platform``.
    """
    if not is_windows():
        return ""
    try:
        wv = sys.getwindowsversion()
        ver = (wv.major, wv.minor, wv.build)
    except Exception:
        return ""
    for min_ver, name in _WIN32_CLIENT_RELEASES:
        if ver >= min_ver:
            return name
    return ""


def host_os_label() -> str:
    """Return a short ``"<OS> (<release>)"`` host label, WMI-free on Windows.

    Used for prompt/diagnostic host lines.  On Windows the release comes from
    :func:`windows_release` (no WMI); elsewhere it falls back to the stdlib
    ``platform`` module, which does not use WMI on non-Windows platforms.
    """
    if is_windows():
        rel = windows_release()
        return f"Windows ({rel})" if rel else "Windows"
    import platform  # non-Windows: no WMI cost, imported lazily to stay light

    if is_macos():
        mac_ver = platform.mac_ver()[0]
        return f"macOS ({mac_ver or platform.release()})"
    return f"{platform.system()} ({platform.release()})"
