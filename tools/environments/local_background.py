"""Command wrappers for detached Bash and PowerShell execution."""
from __future__ import annotations

def _quote_bash_path(path: str) -> str:
    """Quote *path* for safe interpolation into a Git Bash script on Windows."""
    from tools.environments.local import _bash_safe_path
    import shlex
    return shlex.quote(_bash_safe_path(path))



def _build_powershell_background_script(
    command: str,
    cwd: str,
    shell_type: str,
    cwd_file: str | None = None,
) -> str:
    """Build a PowerShell wrapper suitable for detached background processes.

    Mirrors ``LocalEnvironment._wrap_command_powershell`` but omits the stdout
    CWD marker (background output goes to the process-registry buffer) and only
    persists the final CWD to *cwd_file* when one is provided.

    Args:
        command: Raw user command (will be down-levelled for PS5.1 unless
            *shell_type* is ``pwsh``).
        cwd: Working directory to switch to before running *command*.
        shell_type: ``pwsh`` or ``powershell``.
        cwd_file: Optional path to write the final working directory to.
            When provided, the wrapper writes ``(Get-Location).Path`` to this
            file so ``LocalEnvironment._update_cwd`` can pick it up.

    Returns:
        A multi-line PowerShell script ready for ``-Command``.
    """
    from tools.environments.local import fix_pwsh_command, pwsh_transform
    # Same PowerShell-aware quoting repair as the foreground wrapper, and like
    # there it runs FIRST, before any PS7→5.1 transform: fix unclosed
    # strings/here-strings/comments so the background script stays legal.
    # (No warning channel here — background output goes to the
    # process-registry buffer.)
    if command.strip():
        fix = fix_pwsh_command(command)
        if fix is not None and fix.changed:
            command = fix.command
    if shell_type != "pwsh":
        command, _ = pwsh_transform(command)

    escaped = command.replace("'", "''")
    quoted_cwd = cwd.replace("'", "''")

    parts = [
        # Force UTF-8 output encoding for stdout/stderr.
        "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8",
        "$OutputEncoding=[System.Text.Encoding]::UTF8",
        # Set native command argument passing to Windows (backward-compat) mode.
        # PS 7.3+ defaults to 'Standard' which can break legacy tools.
        "if (Get-Command -Name pwsh -ErrorAction SilentlyContinue) { $PSNativeCommandArgumentPassing = 'Windows' }",
        "$ErrorActionPreference = 'Continue'",
        f"Set-Location -LiteralPath '{quoted_cwd}' -ErrorAction SilentlyContinue",
        f"if ($?) {{ Set-Location -LiteralPath '{quoted_cwd}' }} else {{ exit 126 }}",
        # Run the command and flush PowerShell's formatting pipeline.
        # ``-Stream`` emits each object's rendered text as it arrives instead
        # of accumulating everything until the pipeline completes — without
        # it, output produced before a timeout kill is lost entirely.
        # ``try/catch`` protects against malformed user commands so the exit
        # code and cwd file are always written.  Parity with spawn path.
        f"try {{ Invoke-Expression '{escaped}' | Out-String -Width 4096 -Stream | Write-Output }} catch {{ $_ | Out-String -Width 4096 -Stream | Write-Output }}",
        # ``$Error.Count`` catches non-terminating errors that don't set
        # ``$LASTEXITCODE`` (e.g. ``Get-ChildItem`` on a missing path).
        "$hermes_ec = $LASTEXITCODE; if ($null -eq $hermes_ec -and $Error.Count -gt 0) { $hermes_ec = 1 }",
    ]

    if cwd_file:
        quoted_cwd_file = cwd_file.replace("'", "''")
        parts.append(
            f"(Get-Location).Path | Out-File -Encoding utf8 -FilePath '{quoted_cwd_file}'"
        )

    parts.append("exit $hermes_ec")
    return "\n".join(parts)



def _build_bash_background_script(
    command: str,
    cwd: str,
    cwd_file: str | None = None,
) -> str:
    """Build a bash wrapper suitable for detached background processes.

    Mirrors ``_build_powershell_background_script`` for the bash-on-Windows
    background path (used by ``process_registry`` when the resolved shell is
    git-bash). The wrapper cd's to *cwd* (guarded — exits 126 when the
    directory can't be entered), runs *command*, persists the final working
    directory to *cwd_file* when one is provided, and exits with the
    command's exit code.

    The persisted ``pwd`` is in MSYS form (``/c/Users/x``) on Windows; the
    consumer (``LocalEnvironment._update_cwd``) translates it via
    ``_msys_to_windows_path`` for bash sessions.

    Args:
        command: Raw user command (bash syntax).
        cwd: Working directory to switch to before running *command*.
        cwd_file: Optional path to write the final working directory to.

    Returns:
        A multi-line bash script ready for ``bash -lc``.
    """
    escaped = command.replace("'", "'\\''")
    quoted_cwd = _quote_bash_path(cwd)

    parts = [
        f"builtin cd -- {quoted_cwd} || exit 126",
        f"eval '{escaped}'",
        "__hermes_ec=$?",
    ]

    if cwd_file:
        quoted_cwd_file = _quote_bash_path(cwd_file)
        parts.append(f"pwd > {quoted_cwd_file} 2>/dev/null || true")

    parts.append("exit $__hermes_ec")
    return "\n".join(parts)
