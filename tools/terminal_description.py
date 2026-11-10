"""Shell-aware terminal tool descriptions."""
from __future__ import annotations

import functools
import os
import platform

@functools.lru_cache(maxsize=1)
def _detect_shell_for_description() -> str:
    """Detect shell type for description purposes.

    Returns ``"pwsh"``, ``"powershell"``, or ``"bash"``.

    Mirrors ``_resolve_shell()`` in ``tools/environments/local.py``:

    - Non-Windows: returns ``"bash"``.
    - ``HERMES_SHELL_TYPE=bash`` (explicit): returns ``"bash"`` only when
      a usable Git Bash was found (discovery + ``_bash_starts()`` smoke
      test) — otherwise the terminal falls back to PowerShell, so
      ``"powershell"`` is reported.
    - ``auto``/unset (default): git-bash if a working install exists
      (``_find_bash(raise_if_missing=False)`` — discovery + smoke test),
      else PowerShell 7 (``pwsh``), else Windows PowerShell 5.1.
    - ``pwsh`` / ``powershell`` (explicit): PowerShell only — never git-bash.
    - Unknown values: treated as ``auto`` (the description path must not
      raise; ``_resolve_shell()`` still raises for unknown values at
      execution time).

    Cached via ``@lru_cache`` so repeated calls are essentially free.
    """
    if platform.system() != "Windows":
        return "bash"

    shell_type = os.environ.get("HERMES_SHELL_TYPE", "auto").strip().lower() or "auto"

    if shell_type == "bash":
        # bash mode: describe the shell the terminal actually runs.  Git
        # Bash present and usable → "bash"; missing/broken → _resolve_shell()
        # falls back to PowerShell, so describe that instead.  The probe is
        # gated on the real OS flag (_IS_WINDOWS) so a mocked
        # platform.system() in tests never triggers an actual bash search.
        try:
            from tools.environments.local import _IS_WINDOWS, _bash_starts, _find_bash
            if _IS_WINDOWS:
                bash_path = _find_bash()
                if bash_path and _bash_starts(bash_path):
                    return "bash"
        except Exception:
            pass
        return "powershell"

    if shell_type in ("pwsh", "powershell"):
        # Explicit PowerShell: probe pwsh (PowerShell 7), never git-bash.
        try:
            from tools.environments.local import _find_pwsh

            if _find_pwsh():
                return "pwsh"
        except Exception:
            pass
        return "powershell"

    # auto / unset / unknown → git-bash-first default, mirroring
    # ``_resolve_shell()`` (bash → pwsh → powershell).
    try:
        from tools.environments.local import _find_bash, _find_pwsh

        if _find_bash(raise_if_missing=False):
            return "bash"
        if _find_pwsh():
            return "pwsh"
    except Exception:
        pass

    # Fallback to Windows PowerShell 5.1
    return "powershell"



def _build_dynamic_terminal_description() -> dict:
    """Return dynamic schema overrides reflecting the actual shell in use.

    Called lazily each time ``registry.get_definitions()`` assembles the
    terminal tool schema, so the description always matches the shell
    the agent will actually execute against.
    """
    from tools.terminal_tool import TERMINAL_TOOL_DESCRIPTION
    shell_type = _detect_shell_for_description()

    if shell_type == "pwsh":
        platform_env = "Execute powershell commands in a PowerShell 7 (pwsh) environment"
    elif shell_type == "powershell":
        platform_env = "Execute powershell commands on a Windows PowerShell environment"
    elif platform.system() == "Windows":
        # git-bash default on Windows — "Linux environment" would mislead the
        # model about where commands run.
        platform_env = "Execute shell commands in a bash (Git Bash / MSYS) environment on Windows"
    else:
        platform_env = "Execute shell commands on a Linux environment"

    # Replace the static platform sentence with the real platform/shell one.
    new_description = TERMINAL_TOOL_DESCRIPTION.replace(
        "Execute shell commands on a Linux environment.",
        platform_env + "."
    )

    # ------------------------------------------------------------------
    # For PowerShell (both pwsh and powershell.exe), also adapt
    # Linux/bash-specific command references so the agent sees cmdlets it
    # might actually be tempted to misuse. The core guidance ("use agent
    # tools instead of shell commands") stays the same; only the example
    # commands change.
    if shell_type in ("powershell", "pwsh"):
        new_description = new_description.replace(
            "Do NOT use cat/head/tail (use read_file)",
            "Do NOT use Get-Content/cat/type (use read_file)",
        )
        new_description = new_description.replace(
            "grep/rg/find/ls (use search_files)",
            "Select-String/findstr (use search_files)",
        )
        new_description = new_description.replace(
            "sed/awk (use patch)",
            "Set-Content/Out-File (use patch)",
        )
        new_description = new_description.replace(
            "echo/heredoc file creation (use write_file)",
            "echo/Set-Content/Out-File file creation (use write_file)",
        )
        new_description = new_description.replace(
            "Pipe git output to cat if it might page.",
            "Pipe git output to Out-Host -Paging if it might page.",
        )

    return {"description": new_description}
