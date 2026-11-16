"""Shell-mode surface of the terminal tool (P-073).

The terminal tool offers two shell modes and runs them through
:mod:`tools.environments.shell_modes`.  This sibling owns everything that turns
that availability into the model-facing surface:

* :func:`detect_shell_for_description` — the shell an UNDECIDED command runs (the
  configured mode when the host can run it, else git-bash → pwsh → 5.1).  With both
  shells runnable it is only the fallback: ``auto`` reads each command first (P-075).
* :func:`build_dynamic_description` — the schema override registered as the
  terminal tool's ``dynamic_schema_overrides``: the platform sentence, the
  PowerShell-adapted command examples, and a ``shell`` enum narrowed to the modes
  this host can actually run.
* :func:`resolve_shell_mode` — validation of the ``shell=`` argument: unknown
  spelling, a mode the host cannot run, or a non-local backend are all refused
  with an actionable error instead of quietly running the other shell.  ``auto``
  (or no argument at all) is not an error and not a fixed mode: on a host that runs
  both shells it is resolved from the command itself
  (:func:`tools.environments.shell_modes.resolve_auto_shell`), and only that case
  earns a mention in the result (:func:`shell_result_fields`).
* :func:`interactive_shell_command` — the REPL a bare ``mode="interactive"`` call
  starts, in the mode the call asked for.

The terminal tool keeps the thin call sites and re-exports these names, so
``tools.terminal_tool.<name>`` stays the import/patch target (the facade's
long-standing contract for its ``terminal_tool_*`` companions).
"""

from __future__ import annotations

import copy
import sys
from dataclasses import dataclass
from typing import Optional

from tools.environments.shell_modes import (
    MODE_AUTO,
    MODES,
    SCHEMA_MODES,
    ShellModeUnavailable,
    available_modes,
    default_mode,
    default_probes,
    describe_modes,
    normalize_mode,
    resolve_auto_shell,
    resolve_shell_for_mode,
    unavailability_reason,
)


@dataclass(frozen=True)
class ShellChoice:
    """What one call's ``shell`` argument resolved to, and what the result says about it.

    ``mode`` is the mode to run (``None`` → the environment's own shell, which is what a
    non-local backend always gets).  ``shell_type`` names the binary family that will
    answer — ``pwsh`` and ``powershell`` are different binaries, and only the caller's
    report distinguishes them.  ``notice`` is the P-075 rule in one field: report the
    shell back only when ``auto`` had two shells to choose between, because a one-shell
    host has no choice to explain.
    """

    mode: Optional[str] = None
    shell_type: Optional[str] = None
    auto: bool = False
    detected: bool = False
    notice: bool = False

    def plan_fields(self) -> dict[str, Optional[str]]:
        """The ``_ExecPlan`` fields this choice contributes (see ``_plan_execution``)."""
        return {
            "shell_mode": self.mode,
            "shell_notice": self.shell_type if self.notice else None,
        }


def shell_result_fields(plan) -> dict[str, str]:
    """Result metadata naming the shell ``auto`` ran (empty unless this host has both).

    A command run under ``shell="auto"`` may have gone to bash or to PowerShell and the
    model cannot tell from the output alone, so the result carries
    ``{"shell": "<what ran>", "shell_mode": "auto"}``.  On a host with one shell, and for
    an explicitly requested mode, this returns nothing: there the schema already said
    which shell it would be, and restating it on every call is noise.
    """
    notice = getattr(plan, "shell_notice", None)
    return {"shell": notice, "shell_mode": MODE_AUTO} if notice else {}


def shell_probes():
    """The real shell probe set (importable seam for tests)."""
    return default_probes()


def detect_shell_for_description(probes=None) -> str:
    """The shell type (``bash`` / ``pwsh`` / ``powershell``) an UNDECIDED command runs.

    Resolution lives in :mod:`tools.environments.shell_modes` — one source of truth
    shared with the call validator and ``LocalEnvironment``, so the description can
    no longer promise a shell the terminal will not run:

    - the configured mode (``terminal.shell`` → ``HERMES_SHELL_TYPE``) when this
      host can run it;
    - otherwise the auto preference: git-bash (when a working install exists) →
      PowerShell 7 (``pwsh``) → Windows PowerShell 5.1.

    On a host without Git Bash the answer is therefore ``pwsh``/``powershell`` and
    the description says bash mode is disabled.  With both shells runnable the answer
    is the *fallback* — the shell ``auto`` uses for a command whose syntax does not
    name either one (P-075) — which is why the description then says both, not one.
    """
    mode = default_mode(probes)
    try:
        shell_type, _ = resolve_shell_for_mode(mode, probes)
    except Exception:  # health: allow BLE001 -- a schema override must never break registration
        # A description must never raise; the un-runnable mode's own name is still
        # the best answer for "which shell would a call use".
        return "bash" if mode == "bash" else "powershell"
    return shell_type


def build_dynamic_description(probes=None) -> dict:
    """Schema overrides for the terminal tool: description + ``shell`` enum."""
    from tools.terminal_tool import TERMINAL_SCHEMA, TERMINAL_TOOL_DESCRIPTION

    probes = probes or shell_probes()
    shell_type = detect_shell_for_description(probes)
    available = available_modes(probes)

    if len(available) > 1:
        # Both shells run here, so the leading sentence may not promise either one:
        # `auto` reads each command (P-075).
        platform_env = (
            "Execute shell commands in bash (Git Bash / MSYS) or PowerShell on Windows — "
            '`shell="auto"` (the default) picks one per command'
            if probes.is_windows
            else "Execute shell commands in bash or PowerShell — "
            '`shell="auto"` (the default) picks one per command'
        )
    elif shell_type == "pwsh":
        platform_env = (
            "Execute powershell commands in a PowerShell 7 (pwsh) environment"
        )
    elif shell_type == "powershell":
        platform_env = "Execute powershell commands on a Windows PowerShell environment"
    elif probes.is_windows:
        # git-bash default on Windows — "Linux environment" would mislead the
        # model about where commands run.
        platform_env = (
            "Execute shell commands in a bash (Git Bash / MSYS) environment on Windows"
        )
    else:
        platform_env = "Execute shell commands on a Linux environment"

    description = TERMINAL_TOOL_DESCRIPTION.replace(
        "Execute shell commands on a Linux environment.", platform_env + "."
    )
    # Name the available modes, the default, and — when one is missing — that
    # asking for it is an error rather than a silent switch.
    description = description.replace(
        "The host OS, shell, and terminal backend are stated in your environment section",
        describe_modes(probes)
        + " The host OS, shell, and terminal backend are stated in your environment section",
    )
    if shell_type in ("powershell", "pwsh"):
        description = _powershell_references(description)

    # The `shell` enum only offers modes this host can run: a model that copies an
    # unavailable spelling out of an old transcript hits the error path, but the
    # schema stops advertising it in the first place.
    parameters = copy.deepcopy(TERMINAL_SCHEMA["parameters"])
    _narrow_shell_enum(parameters["properties"]["shell"], available)
    return {"description": description, "parameters": parameters}


def _powershell_references(description: str) -> str:
    """Swap the bash-only command examples for the cmdlets a model misuses here."""
    for old, new in (
        (
            "Do NOT use cat/head/tail (use read_file)",
            "Do NOT use Get-Content/cat/type (use read_file)",
        ),
        (
            "grep/rg/find/ls (use search_files)",
            "Select-String/findstr (use search_files)",
        ),
        ("sed/awk (use patch)", "Set-Content/Out-File (use patch)"),
        (
            "echo/heredoc file creation (use write_file)",
            "echo/Set-Content/Out-File file creation (use write_file)",
        ),
        (
            "Pipe git output to cat if it might page.",
            "Pipe git output to Out-Host -Paging if it might page.",
        ),
    ):
        description = description.replace(old, new)
    return description


def _narrow_shell_enum(shell_spec: dict, available) -> None:
    """Restrict the schema's ``shell`` enum to ``auto`` plus the modes this host can run.

    ``auto`` is always offered: it can never be unavailable, because on a one-shell host
    it *is* that shell (P-075).  A model that copies a disabled mode out of an old
    transcript hits the error path, but the schema stops advertising it in the first place.
    """
    runnable = [mode for mode in MODES if mode in available] or list(MODES)
    shell_spec["enum"] = [MODE_AUTO, *runnable]
    missing = [mode for mode in MODES if mode not in available]
    if not missing:
        return
    shell_spec["description"] = (
        f"Shell for THIS command. 'auto' (the default) always runs {runnable[0]} here — "
        f"this host cannot run {' and '.join(missing)}, so nothing is detected and "
        f"{' and '.join(missing)} returns an error instead of running another shell. "
        f"Available: {', '.join(shell_spec['enum'])}."
    )


def resolve_shell_mode(
    shell: Optional[str], env_type: str, command: object = None
) -> ShellChoice:
    """Validate a per-call ``shell=`` and resolve ``auto`` from *command*.

    ``None``, a blank string and ``"auto"`` all mean the same thing: let this module
    pick.  On a one-shell host that is trivially that shell; on a two-shell host it is
    the command's own syntax (:func:`tools.environments.shell_modes.resolve_auto_shell`),
    which is the whole point of ``auto`` being the default (P-075).  An EXPLICIT mode
    stays strict — a mode this host cannot run is an error, never a silent switch.
    """
    from tools.registry import tool_error
    from tools.terminal_tool import _Rejected

    try:
        mode = normalize_mode(shell)
    except ValueError as exc:
        raise _Rejected(tool_error(str(exc))) from exc
    if env_type != "local":
        if mode != MODE_AUTO:
            raise _Rejected(
                tool_error(
                    f"shell='{mode}' applies to the local terminal backend only; this session "
                    f"runs commands in the '{env_type}' sandbox, whose shell is fixed by that "
                    "environment. Drop the `shell` argument (or switch terminal.backend to "
                    "local) to run the command."
                )
            )
        # The sandbox owns its shell: there is no host shell to choose and nothing to report.
        return ShellChoice(None, None, auto=True)
    if mode == MODE_AUTO:
        resolution = resolve_auto_shell(command)
        return ShellChoice(
            resolution.mode,
            resolution.shell_type,
            auto=True,
            detected=resolution.detected,
            notice=resolution.dual,
        )
    try:
        shell_type, _ = resolve_shell_for_mode(mode)
    except ShellModeUnavailable:
        reason = unavailability_reason(mode) or "the shell could not be started"
        raise _Rejected(
            _shell_unavailable_error(mode, reason, available_modes())
        ) from None
    return ShellChoice(mode, shell_type)


def _shell_unavailable_error(mode: str, reason: str, available) -> str:
    """The envelope for "this host cannot run that shell mode".

    Names the missing shell, why, and every way forward — install it, or use one of
    the modes that DO work.  A silent switch to another shell is not an option: the
    command in hand is written for the mode the caller asked for.
    """
    from tools.registry import tool_error

    install = (
        "Install Git for Windows (git-bash) to enable bash mode"
        if mode == "bash"
        else "Install PowerShell 7 (pwsh) to enable PowerShell mode"
    )
    choices = ", ".join(f'"{m}"' for m in available)
    retry = f' (e.g. shell="{available[0]}")' if available else ""
    return tool_error(
        f"Shell mode '{mode}' is not available on this host: {reason}. "
        f"{install}, or retry with an available mode{retry}: {choices or 'none'}. "
        "The command was not run — it is written for the shell you asked for."
    )


def interactive_shell_command(shell_mode: Optional[str] = None) -> str:
    """The REPL command for a bare ``mode="interactive"`` call, in *shell_mode*.

    [CN-fork P-073] the mode the call asked for picks the REPL, so an interactive
    bash session is Git Bash even when the environment resolved PowerShell.
    """
    shell_type = "bash"
    if shell_mode:
        try:
            shell_type, _ = resolve_shell_for_mode(shell_mode)
        except Exception:  # health: allow BLE001 -- an interactive call must still get a REPL
            shell_type = "powershell"
    elif sys.platform == "win32":
        try:
            from tools.environments.local import _resolve_shell

            shell_type, _ = _resolve_shell()
        except Exception:  # health: allow BLE001 -- an interactive call must still get a REPL
            shell_type = "powershell"
    if shell_type == "bash":
        return "bash -i"
    if shell_type == "pwsh":
        return "pwsh -NoLogo -NoExit"
    return "powershell -NoLogo -NoExit"


#: The schema property advertised for ``shell``; the enum is narrowed per host.
SHELL_PROPERTY = {
    "type": "string",
    "enum": list(SCHEMA_MODES),
    "default": MODE_AUTO,
    "description": (
        "Shell for THIS command (local backend). 'auto' (default) picks bash or PowerShell "
        "from the command's syntax when this host runs both; 'bash' (POSIX/Git Bash) and "
        "'pwsh' (PowerShell 7, else 5.1 with 7-syntax down-levelled) force one. A mode "
        "this host cannot run errors instead of running the other shell."
    ),
}
