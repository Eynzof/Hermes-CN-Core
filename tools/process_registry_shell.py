"""The shell argv a background worker runs under (P-058 / P-073).

``ProcessRegistry._scope_argv`` has to answer "which shell, and with which argv?"
for a background spawn:

* git-bash → ``<bash> -lc <bash wrapper>`` (the wrapper cd's, runs the command,
  publishes the final cwd to a file);
* PowerShell 7 / Windows PowerShell 5.1 → ``-NoProfile -NonInteractive
  -ExecutionPolicy Bypass -Command <ps wrapper>`` (the 5.1 wrapper has the PS7→5.1
  down-levelling applied, so a background command behaves like a foreground one).

The shell comes from the caller's per-call ``terminal(shell=...)`` mode when one is
given (P-073), else from the session resolver.  Kept out of ``process_registry``'s
own module: the registry is about process lifecycles, not shell argv spellings.
"""

from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from tools.environments.local import (
    _build_bash_background_script,
    _build_powershell_background_script,
    _resolve_shell,
    _windows_to_msys_path,
)


def windows_spawn_argv(
    shell: Optional[Tuple[str, str]],
    session: Any,
    safe_command: str,
    cwd_file: Optional[str] = None,
    *,
    resolve: Optional[Callable[[], Tuple[str, str]]] = None,
) -> list[str]:
    """Windows argv for *safe_command* in *shell* (``(shell_type, shell_path)``).

    ``shell`` is the per-call mode override; ``None`` uses the session's resolved
    shell, so a background spawn never depends on git-bash being installed.
    ``resolve`` is the session resolver, injected by the caller that owns it
    (``tools.process_registry``) so monkeypatching there still reaches production.
    """
    shell_type, shell_path = shell or (resolve or _resolve_shell)()
    if shell_type == "bash":
        script = _build_bash_background_script(
            command=safe_command,
            cwd=_windows_to_msys_path(session.cwd),
            cwd_file=_windows_to_msys_path(cwd_file) if cwd_file else None,
        )
        return [shell_path, "-lc", script]
    script = _build_powershell_background_script(
        command=safe_command,
        cwd=session.cwd,
        shell_type=shell_type,
        cwd_file=cwd_file,
    )
    return [
        shell_path,
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-Command",
        script,
    ]
