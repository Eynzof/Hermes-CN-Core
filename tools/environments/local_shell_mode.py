"""Per-call shell mode for the local backend (P-073).

``LocalEnvironment`` resolves one shell for its lifetime; a terminal call may ask
for the OTHER one (``terminal(shell="bash" | "pwsh")``).  This sibling owns that
decision so the environment module stays about process handling:

* :func:`effective_shell` — the ``(shell_type, shell_path)`` the command in flight
  must run under.  No override in scope → the environment's own shell.  An
  override resolves strictly (a host without Git Bash raises
  :class:`~tools.environments.shell_modes.ShellModeUnavailable` for ``bash``; it
  never falls back to the other shell) and is cached per mode, so the second
  ``shell="bash"`` call reuses the same Git Bash binary.
* :func:`execute` — ``LocalEnvironment.execute``'s body: apply the override as a
  ContextVar scope, then run the fast-path ladder.  Both opt-in fast paths
  (persistent PowerShell session, ``cmd.exe``) are bound to the binary the
  environment started with, so an override runs through the spawn path.

``run_bounded_sync`` copies the calling thread's context into its deadline
worker, so the scope set here is visible to the spawn performed there.
"""

from __future__ import annotations

import logging
from typing import Any, Optional, Tuple

from tools.environments.shell_modes import (
    current_shell_mode,
    resolve_shell_for_mode,
    shell_mode_scope,
)

logger = logging.getLogger(__name__)


def effective_shell(env: Any) -> Tuple[str, str]:
    """``(shell_type, shell_path)`` for the command being executed (see module doc)."""
    mode = current_shell_mode()
    if not mode or mode == getattr(env, "shell_mode", None):
        return env._shell_type, env._shell_path
    profile = env._shell_profiles.get(mode)
    if profile is None:
        profile = resolve_shell_for_mode(mode)
        env._shell_profiles[mode] = profile
    return profile


def effective_shell_type(env: Any) -> str:
    """The shell type (``bash`` / ``pwsh`` / ``powershell``) in effect now."""
    return effective_shell(env)[0]


def execute(
    env: Any,
    command: str,
    cwd: str = "",
    *,
    timeout: Optional[int] = None,
    stdin_data: Optional[str] = None,
    rewrite_compound_background: bool = True,
    bounded_capture: bool = False,
    output_callback=None,
    wait_for_pattern: Optional[str] = None,
    promote_callback=None,
    yield_handler=None,
    shell_mode: Optional[str] = None,
) -> dict:
    """``LocalEnvironment.execute``: apply *shell_mode*, then run the command."""
    kwargs = dict(
        timeout=timeout,
        stdin_data=stdin_data,
        rewrite_compound_background=rewrite_compound_background,
        bounded_capture=bounded_capture,
        output_callback=output_callback,
        wait_for_pattern=wait_for_pattern,
        promote_callback=promote_callback,
        yield_handler=yield_handler,
    )
    if shell_mode is None:
        return _execute_in_mode(env, command, cwd, **kwargs)
    # An unavailable mode raises on the first shell lookup, still on this thread
    # and before any spawn.
    with shell_mode_scope(shell_mode):
        return _execute_in_mode(env, command, cwd, **kwargs)


def _execute_in_mode(env: Any, command: str, cwd: str, **kwargs) -> dict:
    """The fast-path ladder, run under whatever shell mode is in scope."""
    from tools.environments.base import BaseEnvironment

    if current_shell_mode() in (None, getattr(env, "shell_mode", None)):
        served = _fast_path(env, command, cwd, **kwargs)
        if served is not None:
            return served
    return BaseEnvironment.execute(env, command, cwd, **kwargs)


def _fast_path(
    env: Any,
    command: str,
    cwd: str,
    *,
    timeout,
    stdin_data,
    rewrite_compound_background,
    bounded_capture,
    output_callback,
    wait_for_pattern,
    promote_callback,
    yield_handler,
) -> Optional[dict]:
    """The opt-in PowerShell session / cmd.exe paths, or None to spawn.

    Each declines (returns None or raises :class:`_SessionFallback`) on anything it
    cannot serve; a bug inside one must never wedge the tool, so a broader failure
    is logged and the spawn path takes over.
    """
    from tools.environments.local import _SessionFallback, _cmd_fast_path_eligible

    if yield_handler is not None or stdin_data is not None or bounded_capture:
        return None
    if effective_shell_type(env) not in ("powershell", "pwsh"):
        return None
    if env._pwsh_session_reuse:
        try:
            return env._execute_via_session(
                command,
                cwd,
                timeout=timeout,
                rewrite_compound_background=rewrite_compound_background,
            )
        except _SessionFallback as exc:
            logger.info("PowerShell session fast path declined (%s); using spawn.", exc)
        except Exception as exc:  # noqa: BLE001 - the spawn path is the safety net
            logger.warning(
                "PowerShell session execute failed (%s); using spawn.",
                exc,
                exc_info=True,
            )
    if env._cmd_fast_path and _cmd_fast_path_eligible(command):
        try:
            return env._execute_via_cmd(command, cwd, timeout=timeout)
        except _SessionFallback as exc:
            logger.info("cmd fast path declined (%s); using spawn.", exc)
        except Exception as exc:  # noqa: BLE001 - the spawn path is the safety net
            logger.warning(
                "cmd fast path failed (%s); using spawn.", exc, exc_info=True
            )
    return None
