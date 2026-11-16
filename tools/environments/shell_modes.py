"""Shell modes for the terminal tool: ``bash`` vs ``pwsh``, and what this host can run.

The terminal tool runs commands through one of two shell *modes*:

``bash``
    Windows: Git Bash (MSYS) — native POSIX syntax is translated to
    Git-Bash-compatible form by :func:`tools.environments.bash_fix.fix_bash_command`.
    POSIX hosts: the system bash, no translation needed.
``pwsh``
    PowerShell.  PowerShell 7 (``pwsh``) is preferred; when it is not installed
    the host's Windows PowerShell 5.1 (``powershell.exe``) runs the command and
    :func:`tools.environments.pwsh_fix.pwsh_transform` down-levels PS7 syntax
    first.

One module answers the three questions the consumers kept answering differently:

1. **What can this host run?** (:func:`availability`) — the tool schema must only
   advertise modes the host actually has, and must SAY which one is missing.
2. **Which binary implements a mode?** (:func:`resolve_shell_for_mode`) —
   ``LocalEnvironment`` needs ``(shell_type, shell_path)``; an explicit request
   for a missing mode raises :class:`ShellModeUnavailable` instead of silently
   running the other shell under the caller's syntax.
3. **What does the model see?** (:func:`describe_modes`) — the schema sentence
   naming the available modes, the default, and what happens if a disabled mode
   is requested anyway.

Host facts arrive as a :class:`ShellProbes` value rather than from
``platform.system()``, so the resolution rules are testable with fake binaries
instead of a faked OS.
"""

from __future__ import annotations

import contextlib
import contextvars
from dataclasses import dataclass
from typing import Callable, Dict, Iterator, Optional, Tuple

MODE_AUTO = "auto"
MODE_BASH = "bash"
MODE_PWSH = "pwsh"

#: Modes the tool can actually execute, in description order.
MODES: Tuple[str, ...] = (MODE_BASH, MODE_PWSH)

#: LLM-friendly spellings mapped onto the two real modes.  Models emit whatever
#: the surrounding docs used (``powershell``, ``ps7``, ``git-bash`` …); a synonym
#: must never cost a turn, so it is normalized instead of refused.
_ALIASES = {
    "bash": MODE_BASH,
    "git-bash": MODE_BASH,
    "git_bash": MODE_BASH,
    "gitbash": MODE_BASH,
    "git bash": MODE_BASH,
    "sh": MODE_BASH,
    "shell": MODE_BASH,
    "posix": MODE_BASH,
    "pwsh": MODE_PWSH,
    "powershell": MODE_PWSH,
    "powershell5": MODE_PWSH,
    "powershell_5": MODE_PWSH,
    "powershell-5": MODE_PWSH,
    "windows-powershell": MODE_PWSH,
    "ps": MODE_PWSH,
    "ps5": MODE_PWSH,
    "ps7": MODE_PWSH,
}

_MODE_LABELS = {
    MODE_BASH: "bash (Git Bash on Windows)",
    MODE_PWSH: "pwsh (PowerShell 7, or Windows PowerShell 5.1 when 7 is absent)",
}


class UnknownShellMode(ValueError):
    """The caller asked for a shell mode that is not one of :data:`MODES`."""

    def __init__(self, raw: str):
        self.raw = raw
        self.valid = MODES
        super().__init__(
            f"Unknown shell mode {raw!r}. Use 'bash' or 'pwsh' "
            "(or omit `shell` to use the configured default)."
        )


class ShellModeUnavailable(RuntimeError):
    """An explicitly requested shell mode cannot run on this host.

    Carries the reason and the modes that *are* available, so the terminal tool
    can hand the model an actionable error (install Git for Windows / use the
    other mode) rather than a bare failure.
    """

    def __init__(self, mode: str, reason: str, available: Tuple[str, ...]):
        self.mode = mode
        self.reason = reason
        self.available = available
        choices = ", ".join(repr(m) for m in available) or "none"
        super().__init__(
            f"shell mode {mode!r} is unavailable on this host: {reason}. "
            f"Available mode(s): {choices}."
        )


@dataclass(frozen=True)
class ShellProbes:
    """Host facts and binary finders, injectable for tests.

    Every callable mirrors the one in :mod:`tools.environments.local`; the
    defaults are resolved lazily (:func:`default_probes`) so importing this
    module never drags in the local environment's import chain.
    """

    is_windows: bool
    find_bash: Callable[..., Optional[str]]
    find_bash_posix: Callable[[], str]
    find_pwsh: Callable[[], Optional[str]]
    find_powershell: Callable[[], str]


def default_probes() -> ShellProbes:
    """The real finders from :mod:`tools.environments.local`."""
    from tools.environments.local import (
        _IS_WINDOWS,
        _find_bash,
        _find_bash_posix,
        _find_powershell,
        _find_pwsh,
    )

    return ShellProbes(
        is_windows=_IS_WINDOWS,
        find_bash=_find_bash,
        find_bash_posix=_find_bash_posix,
        find_pwsh=_find_pwsh,
        find_powershell=_find_powershell,
    )


def normalize_mode(raw: object, *, default: str = MODE_AUTO) -> str:
    """Normalize a caller-supplied shell mode to ``bash``/``pwsh``/``auto``.

    ``None``, an empty string and whitespace all mean "unset" and fall back to
    *default* (``auto`` unless the caller says otherwise).  Case, surrounding
    whitespace and the well-known synonyms are folded; anything else raises
    :class:`UnknownShellMode` so a typo is a clear error instead of a silent
    switch to the default shell.
    """
    if raw is None:
        return default
    text = str(raw).strip().lower()
    if not text:
        return default
    if text == MODE_AUTO:
        return MODE_AUTO
    mode = _ALIASES.get(text)
    if mode is None:
        raise UnknownShellMode(str(raw))
    return mode


def shell_type_mode(shell_type: str) -> str:
    """Map a resolved shell type (``bash`` / ``pwsh`` / ``powershell``) to a mode."""
    return MODE_BASH if shell_type == "bash" else MODE_PWSH


#: In-flight mode for the current tool call.  A ContextVar (not an instance
#: attribute) because one ``LocalEnvironment`` serves concurrent calls from
#: different chats in a gateway process, and because ``run_bounded_sync`` copies
#: the context into its deadline worker — the spawn path reads the mode there.
_CURRENT_MODE: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "hermes_shell_mode", default=None
)


def current_shell_mode() -> Optional[str]:
    """The mode the running call asked for, or ``None`` (use the env default)."""
    return _CURRENT_MODE.get()


@contextlib.contextmanager
def shell_mode_scope(mode: object) -> Iterator[None]:
    """Apply *mode* to every shell decision made inside this block."""
    token = _CURRENT_MODE.set(normalize_mode(mode))
    try:
        yield
    finally:
        _CURRENT_MODE.reset(token)


def configured_mode() -> str:
    """The configured shell mode (``terminal.shell`` → ``HERMES_SHELL_TYPE``).

    Unknown values fall back to ``auto``: the description path must never raise,
    while execution keeps its own validation.
    """
    import os

    try:
        return normalize_mode(os.environ.get("HERMES_SHELL_TYPE", MODE_AUTO))
    except UnknownShellMode:
        return MODE_AUTO


_AVAILABILITY_CACHE: Dict[str, Tuple[str, bool, str]] = {}


def clear_cache() -> None:
    """Drop the cached probe results (tests, and a PATH change within a process)."""
    _AVAILABILITY_CACHE.clear()


def _locate(probes: ShellProbes, mode: str) -> Tuple[str, str, str]:
    """``(shell_type, path, reason)`` for *mode* on this host.

    ``path`` is ``""`` when the mode cannot run and ``reason`` then says why.
    The shell type names the binary family that answered — ``pwsh`` vs
    ``powershell`` decides whether PS7 syntax needs down-levelling.
    """
    if mode == MODE_BASH:
        if probes.is_windows:
            path = probes.find_bash(raise_if_missing=False)
            if path:
                return "bash", path, ""
            return (
                "",
                "",
                (
                    "Git Bash (git-bash) is not installed, or the installed bash "
                    "cannot start; install Git for Windows to enable bash mode"
                ),
            )
        return "bash", probes.find_bash_posix(), ""
    # MODE_PWSH — PowerShell 7 when present, else the Windows PowerShell 5.1
    # that ships with every Windows 10/11 system.
    path = probes.find_pwsh()
    if path:
        return "pwsh", path, ""
    if probes.is_windows:
        return "powershell", probes.find_powershell(), ""
    return (
        "",
        "",
        "PowerShell is not installed (pwsh is not on PATH)",
    )


def shell_for_spawn(mode: Optional[str]) -> Optional[Tuple[str, str]]:
    """``(shell_type, shell_path)`` for a background spawn's mode, or None for the default.

    [CN-fork P-073] ``terminal(shell="bash"/"pwsh", background=true)`` names the shell
    for THAT spawn; without a mode the process registry's own resolver decides.  An
    unavailable mode raises :class:`ShellModeUnavailable` — the terminal tool
    validates before reaching here, so this is the last-line guard.
    """
    if not mode or mode == MODE_AUTO:
        return None
    return resolve_shell_for_mode(mode)


def _probe(probes: Optional[ShellProbes] = None) -> ShellProbes:
    return probes or default_probes()


def _resolve(mode: str, probes: Optional[ShellProbes] = None) -> Tuple[str, str, str]:
    """Cached :func:`_locate` for the real probe set (probe spawning is expensive)."""
    real = probes is None
    probes = _probe(probes)
    if real and mode in _AVAILABILITY_CACHE:
        return _AVAILABILITY_CACHE[mode]
    result = _locate(probes, mode)
    if real:
        _AVAILABILITY_CACHE[mode] = result
    return result


def availability(probes: Optional[ShellProbes] = None) -> Dict[str, Tuple[bool, str]]:
    """``{mode: (available, reason)}`` for this host; reason is empty when available."""
    result: Dict[str, Tuple[bool, str]] = {}
    for mode in MODES:
        _shell_type, path, reason = _resolve(mode, probes)
        result[mode] = (bool(path), reason)
    return result


def available_modes(probes: Optional[ShellProbes] = None) -> Tuple[str, ...]:
    """Modes this host can run, in :data:`MODES` order."""
    return tuple(mode for mode in MODES if _resolve(mode, probes)[1])


def is_available(mode: str, probes: Optional[ShellProbes] = None) -> bool:
    return bool(_resolve(mode, probes)[1])


def unavailability_reason(mode: str, probes: Optional[ShellProbes] = None) -> str:
    """Why *mode* cannot run here (empty string when it can)."""
    return _resolve(mode, probes)[2]


def default_mode(probes: Optional[ShellProbes] = None) -> str:
    """The mode a call WITHOUT ``shell=`` runs under.

    Mirrors ``_resolve_shell()``: the configured mode when the host can run it,
    else Git Bash when usable, else the PowerShell chain.  Bash is the fallback
    of last resort on a POSIX host too, exactly as ``_resolve_shell()`` ends.
    """
    available = available_modes(probes)
    configured = configured_mode()
    if configured != MODE_AUTO and configured in available:
        return configured
    # `auto` (or a configured mode this host cannot run): git-bash first on both
    # host families — that is what `_resolve_shell()` picks, and on POSIX it is
    # the only shell unless the user installed PowerShell themselves.
    if MODE_BASH in available:
        return MODE_BASH
    if available:
        return available[0]
    return MODE_BASH


def resolve_shell_for_mode(
    mode: str, probes: Optional[ShellProbes] = None
) -> Tuple[str, str]:
    """``(shell_type, shell_path)`` that implements *mode* on this host.

    ``shell_type`` is ``"bash"``, ``"pwsh"`` or ``"powershell"`` (the 5.1
    fallback stays distinguishable so ``pwsh_transform`` runs for it).  Raises
    :class:`ShellModeUnavailable` when the mode cannot run — never returns a
    different shell than the one asked for.
    """
    normalized = normalize_mode(mode)
    if normalized == MODE_AUTO:
        from tools.environments.local import _resolve_shell

        return _resolve_shell()
    shell_type, path, reason = _resolve(normalized, probes)
    if not path:
        raise ShellModeUnavailable(normalized, reason, available_modes(probes))
    return shell_type, path


def describe_modes(probes: Optional[ShellProbes] = None) -> str:
    """One schema sentence: the available modes, the default, and the disabled one.

    The model has to know three things before it writes a command: which mode it
    gets by default, which modes it may ask for, and — when one is missing — that
    asking for it is a hard error rather than a silent switch.
    """
    probes = probes or default_probes()
    found = availability(probes)
    available = [mode for mode in MODES if found[mode][0]]
    default = default_mode(probes)
    pieces: list[str] = []

    if len(available) == len(MODES):
        pieces.append(
            'Both modes work here: `shell="bash"` (POSIX via Git Bash, native commands '
            'auto-translated) and `shell="pwsh"` (PowerShell 7, else 5.1 with 7-syntax '
            "auto-down-levelled)."
        )
    elif available == [MODE_BASH]:
        pieces.append(
            'Only `shell="bash"` (POSIX) works on this host: '
            f'{found[MODE_PWSH][1]}. PowerShell mode is disabled — `shell="pwsh"` returns '
            'an error instead of running your command, so write POSIX bash syntax.'
        )
    elif available == [MODE_PWSH]:
        pieces.append(
            'Only `shell="pwsh"` (PowerShell) works on this host — bash mode is disabled: '
            f'{found[MODE_BASH][1]}. `shell="bash"` returns an error instead of running '
            'your command, so write PowerShell syntax.'
        )
    else:  # pragma: no cover - no host has zero shells; the guard keeps the text total
        pieces.append(
            "No shell mode could be resolved on this host; terminal calls will fail "
            "until bash or PowerShell is installed."
        )

    if available:
        label = _MODE_LABELS.get(default, default)
        pieces.append(f"Default (no `shell` argument): {label}.")

    return " ".join(pieces)


__all__ = [
    "MODE_AUTO",
    "MODE_BASH",
    "MODE_PWSH",
    "MODES",
    "ShellModeUnavailable",
    "ShellProbes",
    "UnknownShellMode",
    "availability",
    "available_modes",
    "clear_cache",
    "configured_mode",
    "current_shell_mode",
    "default_mode",
    "default_probes",
    "describe_modes",
    "is_available",
    "normalize_mode",
    "resolve_shell_for_mode",
    "shell_mode_scope",
    "shell_for_spawn",
    "shell_type_mode",
    "unavailability_reason",
]
