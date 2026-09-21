"""Lazy dependency bootstrapper for non-Python runtime deps. Detection and prompting live here (cross-platform,
instant, Python-controlled UX); install.sh / install.ps1 remain the *installation* backend."""
from __future__ import annotations

import os

from platform_utils import is_windows

import shutil
import subprocess
import sys
from pathlib import Path

from tools.environments.windows_env import refresh_env_from_registry

from tools.rtk_provision import _find_rtk

from hermes_constants import agent_browser_runnable, find_node_executable, get_managed_tools_dir

from tools.environments.local import hermes_subprocess_env

_IS_WINDOWS = is_windows()

_DEP_CHECKS = {
    # find_node_executable() rather than a bare which(): $HERMES_HOME/node is not on PATH, so
    # which() would report Node missing on a managed install and trigger a redundant re-install.
    "node": lambda: find_node_executable("node") is not None,
    "browser": lambda: (agent_browser_runnable(shutil.which("agent-browser")) or _has_system_browser()
                        or _has_hermes_agent_browser() or _has_npx_agent_browser()),
    "ripgrep": lambda: _find_rg() is not None and _has_ripgrepy(),
    "rtk": lambda: _find_rtk() is not None,
    "ffmpeg": lambda: shutil.which("ffmpeg") is not None,
    "coreutils": lambda: _check_coreutils(),
}

_DEP_DESCRIPTIONS = {"node": "Node.js (required for browser tools and TUI)",
                     "browser": "Browser engine (Chromium, for web browsing tools)",
                     "ripgrep": "ripgrep + ripgrepy (fast file search)",
                     "rtk": "rtk reasoning toolkit (token reduction for terminal output)", "ffmpeg": "ffmpeg (TTS voice messages)", "coreutils": "Microsoft Coreutils (POSIX CLI tools on Windows)"}


def _has_system_browser() -> bool:
    names = ("chrome", "msedge", "chromium") if _IS_WINDOWS else (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "chrome")
    return any(shutil.which(name) for name in names)


def _has_npx_agent_browser() -> bool:
    """agent-browser resolves lazily via npx on the default install, invisible to the PATH/managed-dir
    probes above. Mirror tools.browser_tool_install.check_browser_requirements's Termux carve-out so this
    check can't diverge from what browser tools actually find.

    See #43564.
    """
    try:
        from tools.browser_tool_install import _find_agent_browser, _is_npx_agent_browser_sentinel, _requires_real_termux_browser_install
        browser_cmd = _find_agent_browser(validate=False)
    except Exception:
        return False
    return _is_npx_agent_browser_sentinel(browser_cmd) and not _requires_real_termux_browser_install(browser_cmd)


def _has_ripgrepy() -> bool:
    try:
        import ripgrepy
        return True
    except Exception:
        return False


def _find_rg() -> str | None:
    """Return a usable rg executable path.

    Prefer the Hermes-managed copy in ``get_managed_tools_dir()`` so a broken
    global PATH shim/symlink is never selected.  On all platforms, verify that
    the candidate actually runs ``rg --version`` successfully.  The managed
    directory is checked first; only when it is missing or unusable do we fall
    back to PATH.
    """
    binary_name = "rg.exe" if _IS_WINDOWS else "rg"
    managed = get_managed_tools_dir() / binary_name
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

    # Legacy fallback: older installs kept rg in HERMES_HOME/bin.
    from hermes_constants import get_hermes_home

    legacy = get_hermes_home() / "bin" / binary_name
    if legacy.exists() and legacy != managed:
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

    path_rg = shutil.which("rg")
    if path_rg:
        try:
            subprocess.run(
                [path_rg, "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )
            return path_rg
        except Exception:
            pass

    return None


def _has_hermes_agent_browser() -> bool:
    from hermes_constants import get_hermes_home  # late import: tests patch hermes_constants.get_hermes_home
    home = get_hermes_home()
    if _IS_WINDOWS:  # npm -g --prefix puts .cmd shims directly in the prefix dir
        return (home / "node" / "agent-browser.cmd").is_file()
    # install.sh installs globally into $HERMES_HOME/node/bin/ via npm -g --prefix
    # Also check legacy node_modules/.bin/ path for git-clone installs.
    return (
        (home / "node" / "bin" / "agent-browser").is_file()
        or (home / "node_modules" / ".bin" / "agent-browser").is_file()
    )


def _check_coreutils() -> bool:
    """Check if coreutils (cat.exe) is available.

    On Windows, checks the Hermes-managed install directory first,
    then falls back to PATH. On Linux/macOS, cat is always present
    as part of the system coreutils package; also accept gcat
    (macOS with GNU coreutils via Homebrew).
    """
    if _IS_WINDOWS:
        managed_cat = get_managed_tools_dir() / "coreutils" / "bin" / "cat.exe"
        if managed_cat.exists():
            return True
        # Legacy fallback for existing installs.
        from hermes_constants import get_hermes_home

        legacy_cat = get_hermes_home() / "coreutils" / "bin" / "cat.exe"
        if legacy_cat.exists():
            return True
        return shutil.which("cat.exe") is not None
    # Linux/macOS: system cat is always available; also check for
    # GNU coreutils prefix (gcat) on macOS via Homebrew.
    return shutil.which("cat") is not None or shutil.which("gcat") is not None


def _find_install_script(package_dir: Path | None = None, repo_root: Path | None = None) -> tuple[Path | None, str | None]:
    """Locate the install script — bundled in wheel or in git checkout."""
    package_dir = package_dir or Path(__file__).parent
    repo_root = repo_root or package_dir.parent
    candidates = [("install.sh", "bash"), ("install.ps1", "powershell")]
    if _IS_WINDOWS:
        candidates.reverse()
    for script_name, shell in candidates:
        for base in (package_dir, repo_root):
            if (script := base / "scripts" / script_name).is_file():
                return script, shell
    return None, None


def ensure_dependency(dep: str, interactive: bool = True) -> bool:
    """Ensure a non-Python dependency is available. Returns True if available."""
    check = _DEP_CHECKS.get(dep)
    if check is None or check():  # unknown dep — don't silently forward to install script
        return check is not None
    script, shell = _find_install_script()
    desc = _DEP_DESCRIPTIONS.get(dep, dep)
    if script is None:
        if interactive:
            print(f"  {desc} is not installed and no install script was found.")
            print(f"  Install {dep} manually and try again.")
        return False
    if interactive and sys.stdin.isatty():
        try:
            reply = input(f"{desc} is not installed. Install now? [Y/n] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        if reply not in ("", "y", "yes"):
            return False
    if shell == "powershell":
        from hermes_constants import get_hermes_home
        refresh_env_from_registry()
        ps_bin = shutil.which("powershell") or shutil.which("pwsh")
        if not ps_bin:
            if interactive:
                print("  PowerShell not found. Install PowerShell or run install.ps1 manually.")
            return False
        cmd = [ps_bin, "-ExecutionPolicy", "Bypass", "-File", str(script), "-Ensure", dep, "-HermesHome", str(get_hermes_home())]
    else:
        cmd = ["bash", str(script), "--ensure", dep]
    run_env = {**hermes_subprocess_env(inherit_credentials=False), "IS_INTERACTIVE": "false"}
    return subprocess.run(cmd, env=run_env).returncode == 0 and check()
