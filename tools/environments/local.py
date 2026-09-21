"""Local execution environment — spawn-per-call with session snapshot."""

import base64
import contextlib
import gzip
import logging
import ntpath
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Mapping
from pathlib import Path

from hermes_constants import get_process_hermes_home
from platform_utils import is_windows
from hermes_cli._subprocess_compat import windows_hide_flags
from tools.environments._process_bash_command import _prepare_bash_cmd
from tools.environments.base import BaseEnvironment
from tools.environments.base_output import _pipe_stdin
from tools.environments.bash_fix import fix_bash_command
from tools.environments.local_env_policy import (
    _ALWAYS_STRIP_KEYS, _HERMES_PROVIDER_ENV_BLOCKLIST, _HERMES_PROVIDER_ENV_FORCE_PREFIX,
    _is_hermes_internal_secret, _is_terminal_first_party_env,
    _matches_terminal_first_party_prefix, _plugin_terminal_env_strip_keys, strip_profile_gate_env)
from tools.environments.local_gitbash_probe import (
    _bash_probe_details_cache, _bash_starts, _git_bash_aslr_help,
    _looks_like_msys_spawn_failure, _mandatory_aslr_enabled)
from tools.environments.local_pythonpath import (
    _build_hermes_repo_root_aliases, _strip_hermes_owned_pythonpath_and_runtime_markers)
from tools.environments.process_pwsh import pwsh_transform
from tools.environments.pwsh_fix import fix_pwsh_command
from tools.environments.windows_env import refresh_env_from_registry

# is_windows(), never platform.system(): the platform-based idiom drives
# platform.uname() into its WMI-backed path on Python 3.12+, costing two WMI
# queries at import time (test_wmi_ssl_windows_overhead).
_IS_WINDOWS = is_windows()

logger = logging.getLogger(__name__)

# --- Terminal temp-cache pruning ---
# get_temp_dir() defaults to HERMES_HOME/cache/terminal (real storage, not tmpfs), so
# stale artifacts don't vanish on reboot: the gateway housekeeping loop prunes hourly
# and a once-per-process sweep covers CLI-only installs.
TERMINAL_TEMP_MAX_AGE_HOURS = 72
_terminal_temp_prune_lock = threading.Lock()
_terminal_temp_pruned_once = False
# Background artifacts come in triplets (hermes_bg_<id>.log/.pid/.exit). A live
# server's .pid never changes mtime while its .log does, so age is judged per
# GROUP (newest mtime sharing a stem) to keep pid/exit files of live sessions.
_BG_GROUP_RE = re.compile(r"^(hermes_bg_[A-Za-z0-9_-]+)\.(log|pid|exit)$")


def _default_terminal_temp_dir() -> "Path | None":
    """Return HERMES_HOME/cache/terminal, or None if unresolvable."""
    try:
        from hermes_constants import get_hermes_home
        return get_hermes_home() / "cache" / "terminal"
    except Exception:
        return None


def cleanup_terminal_temp_cache(max_age_hours: int = TERMINAL_TEMP_MAX_AGE_HOURS) -> int:
    """Delete session temp artifacts older than *max_age_hours*; return count.
    Only the managed default dir is pruned — never a user-pointed ``terminal.temp_dir``."""
    root = _default_terminal_temp_dir()
    if root is None:
        return 0
    cutoff = time.time() - (max_age_hours * 3600)
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0

    mtimes: dict[Path, float] = {}
    group_newest: dict[str, float] = {}
    for f in entries:
        try:
            mtimes[f] = mt = f.stat().st_mtime
        except OSError:
            continue
        if m := _BG_GROUP_RE.match(f.name):
            group_newest[m.group(1)] = max(group_newest.get(m.group(1), 0.0), mt)

    removed = 0
    for f, mt in mtimes.items():
        m = _BG_GROUP_RE.match(f.name)
        if (group_newest[m.group(1)] if m else mt) >= cutoff:
            continue
        try:
            shutil.rmtree(f, ignore_errors=True) if f.is_dir() else f.unlink()
            removed += 1
        except OSError:
            continue
    return removed


def _prune_terminal_temp_once() -> None:
    """Best-effort prune, at most once per process (CLI-only installs)."""
    global _terminal_temp_pruned_once
    with _terminal_temp_prune_lock:
        if _terminal_temp_pruned_once:
            return
        _terminal_temp_pruned_once = True
    try:
        cleanup_terminal_temp_cache()
    except Exception as exc:
        logger.debug("Terminal temp prune failed: %s", exc)


# --- Windows / MSYS path translation ---
def _msys_to_windows_path(cwd: str) -> str:
    """``/c/Users/x`` / ``/cygdrive/c/..`` / ``/mnt/c/..`` -> native ``C:\\Users\\x`` so
    ``isdir``/``Popen(cwd=)`` find it. No-op off Windows, for empty input and for
    multi-segment POSIX paths like ``/home/x``; idempotent on native paths."""
    m = _IS_WINDOWS and cwd and re.match(r'^/(?:(?:cygdrive|mnt)/)?([a-zA-Z])(/.*)?$', cwd)
    if not m:
        return cwd
    tail = (m.group(2) or "").replace('/', '\\')
    return f"{m.group(1).upper()}:{tail or chr(92)}"  # chr(92) = backslash


def _resolve_local_initial_cwd(cwd: str) -> str:
    """Resolve the initial cwd to an absolute host path. A relative ``TERMINAL_CWD``
    naming the launch directory would otherwise make the wrapper ``cd`` *inside*
    the project; anchor it once so ``Popen(cwd=)`` and the in-shell ``cd`` agree."""
    expanded = os.path.expanduser(cwd) if cwd else os.getcwd()
    if _IS_WINDOWS:
        expanded = _msys_to_windows_path(expanded)
        # ntpath explicitly: with _IS_WINDOWS patched on a POSIX host,
        # os.path.isabs would reject ``C:\Users\x`` and mangle it below.
        if ntpath.isabs(expanded):
            return expanded
    if os.path.isabs(expanded):
        return expanded
    candidate = os.path.abspath(expanded)
    current = os.getcwd()
    # Relative name matching the tail of the current dir: use the current dir.
    if not os.path.isdir(candidate):
        wanted, have = Path(expanded).parts, Path(current).parts
        if wanted and len(wanted) <= len(have) and have[-len(wanted):] == wanted:
            return current
    return candidate


def _windows_to_msys_path(cwd: str) -> str:
    """Native ``C:\\Users\\x`` -> Git Bash ``/c/Users/x`` so ``builtin cd`` resolves
    it. No-op off Windows / for non-drive paths."""
    m = _IS_WINDOWS and cwd and re.match(r'^([a-zA-Z]):[\\/]*(.*)$', cwd)
    if not m:
        return cwd
    tail = (m.group(2) or "").replace('\\', '/').lstrip('/')
    return f"/{m.group(1).lower()}/{tail}"


def _bash_safe_path(path: str) -> str:
    """*path* safe to embed in a Git Bash script: ``C:\\Users\\x`` / ``C:/Users/x``
    become ``/c/Users/x`` (MSYS argument conversion mangles ``C:/`` forms) and
    leftover backslashes are normalized so bash does not eat ``\\U``. No-op off Windows."""
    return _windows_to_msys_path(path).replace("\\", "/") if _IS_WINDOWS and path else path


def _quote_bash_path(path: str) -> str:
    """Quote *path* for safe interpolation into a Git Bash script on Windows."""
    import shlex
    return shlex.quote(_bash_safe_path(path))


def _cwd_usable(path: str) -> bool:
    """True when *path* is a directory this process can actually chdir into
    (``isdir`` alone passes ``/root`` for a non-root user; ``Popen(cwd=)`` then dies)."""
    return os.path.isdir(path) and os.access(path, os.X_OK)


def _resolve_safe_cwd(cwd: str) -> str:
    """``cwd`` if enterable, else the nearest usable ancestor, else
    ``tempfile.gettempdir()``. MSYS paths are normalized first on Windows so a valid
    ``pwd -P`` result is not rejected. Lets ``_run_bash`` recover from a deleted or
    inaccessible cwd instead of ``Popen`` raising and wedging every later call.

    Used by ``_run_bash`` to recover when the configured cwd is gone — most commonly because a previous tool
    call deleted its own working directory (issue #17558) — or inaccessible to this user, e.g. ``/root``
    leaking from a root-launched CLI session into a non-root gateway's cron jobs (issue #65583). Without
    this guard, ``subprocess.Popen(..., cwd=...)`` raises ``FileNotFoundError``/``PermissionError`` before
    bash starts, wedging every subsequent terminal call until the gateway restarts.
    """
    cwd = _msys_to_windows_path(cwd)
    if cwd and _cwd_usable(cwd):
        return cwd
    if cwd and os.path.isdir(cwd):
        logger.warning(
            "Configured terminal cwd %r exists but is not accessible to "
            "this user (uid=%s) — falling back to the nearest usable "
            "directory. If this is a gateway/cron process, check for "
            "root-owned paths leaking into terminal.cwd / TERMINAL_CWD "
            "(#65583).",
            cwd, getattr(os, "getuid", lambda: "?")())
    parent = os.path.dirname(cwd) if cwd else ""
    while parent and not _cwd_usable(parent):
        next_parent = os.path.dirname(parent)
        if next_parent == parent:
            return tempfile.gettempdir()  # filesystem root itself is unusable
        parent = next_parent
    return parent or tempfile.gettempdir()


# --- Child-process environment construction ---
def _apply_profile_home(env: dict) -> None:
    """Bridge the context-local HERMES_HOME override, then the subprocess HOME contract."""
    from hermes_constants import apply_subprocess_home_env, get_hermes_home_override
    try:
        if value := get_hermes_home_override():
            env["HERMES_HOME"] = value
    except Exception:
        pass
    apply_subprocess_home_env(env)


def _inject_session_context_env(env: dict) -> None:
    """Bridge gateway session ContextVars (HERMES_SESSION_*) into a child env.
    Cross-session leak guard: the vars' last-writer-wins ``os.environ`` mirror may
    belong to another turn on a concurrent multi-session host, so once the session
    context is engaged ContextVars are authoritative — a bound value (incl. "") wins
    and an _UNSET var is STRIPPED, not inherited. An unengaged CLI keeps the mirror."""
    try:
        from gateway.session_context import _UNSET, _VAR_MAP, session_context_engaged
    except Exception:
        return
    _engaged = session_context_engaged()
    for var_name, var in _VAR_MAP.items():
        value = var.get()
        if value is not _UNSET:
            env[var_name] = "" if value is None else str(value)
        elif _engaged:
            env.pop(var_name, None)


def _filter_secret_env(
    items: Mapping[str, str], out: dict, *, unwrap_force: bool,
    plugin_strip: frozenset = frozenset()) -> None:
    """Copy *items* into *out*, dropping Hermes-managed secrets. ``_HERMES_FORCE_<NAME>``
    unwraps to ``NAME`` when ``unwrap_force`` (caller extras / terminal env), else is
    dropped. Blocklisted names survive only via env_passthrough registration or as
    context-entitled first-party ``BUZZ_*`` vars; the latter are used directly, never
    scope-resolved (UnscopedSecretError under multiplex)."""
    try:
        from tools.env_passthrough import is_env_passthrough, resolve_passthrough_value
    except Exception:
        is_env_passthrough, resolve_passthrough_value = (lambda _: False), (lambda _n, fb: fb)
    for key, value in items.items():
        if key.startswith(_HERMES_PROVIDER_ENV_FORCE_PREFIX):
            if not unwrap_force:
                continue
            key = key[len(_HERMES_PROVIDER_ENV_FORCE_PREFIX):]
            if not _is_hermes_internal_secret(key):
                out[key] = value
            continue
        if _is_hermes_internal_secret(key) or key in plugin_strip:
            continue
        first_party = _is_terminal_first_party_env(key)
        passthrough = is_env_passthrough(key)
        if key in _HERMES_PROVIDER_ENV_BLOCKLIST and not (passthrough or first_party):
            continue
        if passthrough and not first_party:
            value = resolve_passthrough_value(key, value)
        if value is not None:
            out[key] = value


def _finalize_child_env(env: dict) -> dict:
    """Guards shared by every spawn surface: profile-home propagation, session-context
    bridging, Hermes-owned PYTHONPATH + venv-marker strip, MSYS defaults, delegate_task
    Kanban scrub. Returns the (possibly new) dict."""
    _apply_profile_home(env)
    _inject_session_context_env(env)
    _strip_hermes_owned_pythonpath_and_runtime_markers(env)
    _apply_windows_msys_bash_env_defaults(env)
    from agent.delegation_context import delegated_child_subprocess_env
    return delegated_child_subprocess_env(env)


def _scrubbed_env(parts, plugin_strip: frozenset, fix_path) -> dict:
    """Filter each ``(items, unwrap_force)`` in *parts* into one env, rewrite PATH via
    *fix_path* (always prepending the hermes install dir so bare ``hermes`` resolves
    for children of a systemd/cron-launched gateway), then apply the shared guards."""
    out: dict[str, str] = {}
    for items, unwrap_force in parts:
        _filter_secret_env(items, out, unwrap_force=unwrap_force, plugin_strip=plugin_strip)
    # Declared names the bound profile scope holds but the process env never did (a routed
    # profile's own .env / sources) — the filter above can only see names already present.
    # Unguarded on purpose: a scope/config failure here must be loud, not silently drop the
    # declared secret again (#114209); _scrub_child_env calls it the same way.
    from tools.env_passthrough import scoped_passthrough_additions
    out.update((k, v) for k, v in scoped_passthrough_additions(out).items() if k not in plugin_strip)
    path_key = _path_env_key(out)
    # Keep bare ``hermes`` invocations available to child jobs even when the gateway was launched by a
    # service manager or cron without the console script's directory on PATH. The terminal environment
    # already applies this invariant; Cron scripts use this sanitizer directly (#92998).
    if path_key is not None:
        out[path_key] = _prepend_hermes_bin_dir(fix_path(out.get(path_key, "")))
    return _finalize_child_env(out)


def _sanitize_subprocess_env(base_env: dict | None, extra_env: dict | None = None) -> dict:
    """Filter Hermes-managed secrets from a subprocess environment (background/PTY
    spawn path, search workers, computer-use driver, user-script runners)."""
    return _scrubbed_env([(base_env or {}, False), (extra_env or {}, True)],
                         _plugin_terminal_env_strip_keys(), lambda p: p)


def hermes_subprocess_env(*, inherit_credentials: bool = False) -> dict[str, str]:
    """Sanitized env for the **non-terminal** spawn surface (browser, ACP/CLI executors,
    computer-use driver, TUI Node host). Tier 1 (``_ALWAYS_STRIP_KEYS``, plugin keys,
    force-prefixed hints, dynamic internal secrets) is always removed; Tier 2 (the
    provider/tool blocklist) unless ``inherit_credentials`` — pass that **only** for
    children that legitimately need LLM credentials (user-blessed claude/codex/gemini
    CLI, TUI Node host). Terminal/execute_code use ``_sanitize_subprocess_env``."""
    env = _scrub_credentials(os.environ.copy(), inherit_credentials=inherit_credentials)
    env.setdefault("PYTHONUTF8", "1")  # Windows UTF-8 safety for spawned processes
    return _finalize_child_env(env)


def _scrub_credentials(env: dict, *, inherit_credentials: bool) -> dict:
    """Tier 1 (always) and, unless ``inherit_credentials``, Tier 2 provider/tool credentials, in place."""
    strip = _ALWAYS_STRIP_KEYS | _plugin_terminal_env_strip_keys()
    if not inherit_credentials:
        strip |= _HERMES_PROVIDER_ENV_BLOCKLIST
    for key in list(env):
        if (key in strip or key.startswith(_HERMES_PROVIDER_ENV_FORCE_PREFIX)
                or _is_hermes_internal_secret(key)):
            del env[key]
    return env


def build_subprocess_env(
    base: "Mapping[str, str] | None" = None, *, inherit_profile_home: bool = True,
    scrub_secrets: bool = True, extra: "Mapping[str, str] | None" = None,
    strip_launch_profile: bool = False) -> dict[str, str]:
    """Single factory for child-process envs. ``base=None`` snapshots ``os.environ``.
    ``scrub_secrets=True`` -> :func:`_sanitize_subprocess_env` (profile home inherent,
    ``inherit_profile_home`` ignored). ``scrub_secrets=False`` keeps the base
    byte-for-byte (git credential flows, ``bws``/``op``); ``inherit_profile_home``
    bridges HERMES_HOME + HOME and ``extra`` is applied last so caller overrides win.
    ``strip_launch_profile`` drops the LAUNCH profile's ``.env`` residue from the base first
    (:func:`strip_launch_profile_env`; a no-op unless a routed home is active) so a child that
    acts for a routed profile sees only that profile's declared names, never the launch profile's."""
    env: dict[str, str] = dict(base) if base is not None else os.environ.copy()
    if strip_launch_profile:
        strip_launch_profile_env(env)
    if scrub_secrets:
        return _sanitize_subprocess_env(env, dict(extra) if extra else None)
    if inherit_profile_home:
        _apply_profile_home(env)
    if extra:
        env.update(extra)
    from agent.delegation_context import delegated_child_subprocess_env
    return delegated_child_subprocess_env(env)


def served_profile_child_env(
    base: "Mapping[str, str] | None" = None, *, target_home: "str | Path | None" = None,
    inherit_credentials: bool = False,
) -> dict[str, str]:
    """Child env for a process that acts FOR the active (possibly served) profile: ``hermes -p X``
    workers, ``key_cmd`` helpers, browser drivers. The process env is the LAUNCH profile's. When the
    target is a ROUTED home (not the launch profile's — under multiplex or a Desktop/dashboard backend
    serving ``?profile=`` with the flag off) the launch ``.env`` residue and bridged ``TERMINAL_*`` are
    dropped (``strip_launch_profile_env``) AND every provider/tool credential is scrubbed from the base
    regardless of provenance: a key systemd / Compose / the shell injected into the launch process was
    never recorded in ``.env`` or a source snapshot, so a name-based strip cannot see it and the target
    overlay cannot remove it. ``inherit_credentials=True`` is for children that legitimately run with
    the profile's credentials (they run the agent or mint its token): the target profile's own secrets
    (its ``.env`` + hydrated sources, what a standalone ``hermes -p X`` loads itself) are overlaid — never
    a sibling profile's. Under multiplex with neither a target nor a bound scope the call raises
    (``get_secret``'s fail-closed contract): minting with the launch environ would sign in as the wrong
    profile. ``False`` keeps the provider scrub; the caller re-adds the few keys the child needs via
    ``get_secret``. ``target_home`` defaults to the active override; ``base`` replaces the
    ``hermes_subprocess_env`` snapshot."""
    from agent.secret_scope import (
        UnscopedSecretError, build_profile_secret_scope, current_secret_scope, is_multiplex_active)
    from hermes_constants import apply_scratch_tmp_env, get_hermes_home_override
    env = dict(base) if base is not None else hermes_subprocess_env(inherit_credentials=inherit_credentials)
    target = str(target_home or get_hermes_home_override() or "")
    if target:
        env["HERMES_HOME"] = target
        apply_scratch_tmp_env(env)  # TMPDIR follows the served home, like HOME does
        if _is_routed_home(target):
            strip_launch_profile_env(env, target)
            _scrub_credentials(env, inherit_credentials=False)
    if inherit_credentials:
        if target:
            secrets = build_profile_secret_scope(Path(target))
        else:
            secrets = current_secret_scope()
            if secrets is None and is_multiplex_active():
                raise UnscopedSecretError(
                    "", "served_profile_child_env(inherit_credentials=True) called with no target home and "
                    "no profile secret scope bound while multiplexing is on; the child would inherit the "
                    "launch profile's credentials. Bind the profile scope (or pass target_home) at the spawn site.")
        env.update((k, v) for k, v in (secrets or {}).items() if v is not None)
    return env


def _is_routed_home(target_home: "str | Path") -> bool:
    """True when ``target_home`` is not the process's own (launch) home."""
    from hermes_constants import get_process_hermes_home
    try:
        return Path(target_home).resolve() != get_process_hermes_home().resolve()
    except OSError:
        return True


def strip_launch_profile_env(env: dict, target_home: "str | Path | None" = None) -> dict:
    """Drop the LAUNCH profile's residue from a child env built for another served profile.
    ``os.environ`` holds the default profile's ``.env`` and its bridged ``TERMINAL_*`` settings;
    the secret scrub removes credentials but not settings (``HERMES_MODEL``, ``TERMINAL_ENV``,
    ``HERMES_LANGUAGE``...), so a standalone ``hermes -p X`` worker and a served one saw different
    envs. The child re-loads X's own ``.env`` and bridges X's config itself. ``target_home``
    defaults to the active home override; no-op when there is no target or the target IS the
    launch profile. The authority test is "does this task serve a routed home", not "is the
    gateway-wide multiplex flag on": the Desktop/dashboard backend serves ``?profile=B`` by
    installing a HERMES_HOME override without that flag."""
    from agent.secret_scope import _is_global_env, load_env_file
    from hermes_constants import get_hermes_home_override, get_process_hermes_home
    target = target_home or get_hermes_home_override()
    if not target or not _is_routed_home(target):
        return env
    launch_home = get_process_hermes_home()
    from hermes_cli.config import TERMINAL_CONFIG_ENV_MAP
    for key in set(load_env_file(launch_home / ".env")) | set(TERMINAL_CONFIG_ENV_MAP.values()):
        if not _is_global_env(key) or key.startswith("TERMINAL_"):
            env.pop(key, None)
    # Authorization gates are the one residue a name list cannot see: a unit-file ``Environment=``
    # or an operator export never appears in the launch ``.env``, the secret scrub ignores
    # non-credentials, and the target's own ``.env`` rarely defines the key to overwrite it (#113270).
    return strip_profile_gate_env(env)


# --- Shell discovery ---
def _windows_bash_candidates(custom: "str | None") -> list[str]:
    """Ordered bash.exe candidates on Windows: HERMES_GIT_BASH_PATH, our portable Git
    under %LOCALAPPDATA%\\hermes\\git (PortableGit ``bin`` and MinGit ``usr\\bin``),
    known Git-for-Windows dirs, then PATH last — ``shutil.which`` may return WSL's
    bash, which fails silently on Windows paths."""
    getenv = os.environ.get
    lad = getenv("LOCALAPPDATA", "")
    roots = [
        lad and os.path.join(lad, "hermes", "git", "bin"),
        lad and os.path.join(lad, "hermes", "git", "usr", "bin"),
        os.path.join(getenv("ProgramFiles", r"C:\Program Files"), "Git", "bin"),
        os.path.join(getenv("ProgramFiles(x86)", r"C:\Program Files (x86)"), "Git", "bin"),
        lad and os.path.join(lad, "Programs", "Git", "bin"),
    ]
    raw = [custom or "", *(os.path.join(r, "bash.exe") for r in roots if r)]
    candidates = list(dict.fromkeys(c for c in raw if c and os.path.isfile(c)))
    found = shutil.which("bash")
    if found and found not in candidates:
        candidates.append(found)
    return candidates


def _find_bash(raise_if_missing: bool = True) -> str | None:
    """Find a usable bash, including Git Bash on Windows.

    Candidates are probed with ``_bash_starts()`` (external-program smoke
    test) so broken/WSL/WindowsApps-stub bash is never returned as usable.

    Windows candidate order mirrors kimix ``bash_tool._find_git_bash_windows``:
    explicit override → managed portable Git (Hermes-specific) → ``where.exe
    git``/``git --exec-path`` derivation → well-known Program Files locations →
    plain ``bash`` on PATH (last resort, filtered for WSL launchers and
    WindowsApps Store stubs).

    ``raise_if_missing`` (default ``True``) preserves the legacy contract for
    callers that require bash (explicit ``HERMES_SHELL_TYPE=bash``): when no
    candidate passes the smoke test a helpful ``RuntimeError`` is raised. The
    default/auto resolution path passes ``raise_if_missing=False`` and treats
    a ``None`` return as "no working bash" so the resolver can fall back to
    PowerShell.
    """
    if not _IS_WINDOWS:
        return _find_bash_posix()

    candidates: list[str] = []
    custom = os.environ.get("HERMES_GIT_BASH_PATH")
    if custom and os.path.isfile(custom):
        candidates.append(custom)

    # Prefer the Hermes-managed portable Git before system installations. A
    # stale custom path or partially removed system Git must not brick the
    # explicitly selected bash path while the managed copy is healthy.
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    portable_git = (
        ntpath.join(local_appdata, "hermes", "git") if local_appdata else ""
    )
    if portable_git:
        for candidate in (
            ntpath.join(portable_git, "bin", "bash.exe"),
            ntpath.join(portable_git, "usr", "bin", "bash.exe"),
        ):
            if os.path.isfile(candidate) and candidate not in candidates:
                candidates.append(candidate)

    # git.exe-based discovery (ported from kimix ``bash_tool._find_git_bash_windows``):
    # ``where.exe git`` -> <gitDir>/../bin/bash.exe, then ``git --exec-path``
    # -> Git for Windows install root -> bin/bash.exe.  Catches per-user /
    # chocolatey / scoop / side-by-side Git installs that live outside the
    # well-known Program Files locations.  Mirrors kimix ordering: this runs
    # BEFORE the well-known locations and the PATH fallback, so a real Git
    # Bash install always wins over ambiguous PATH entries.
    for git_path in _where_git_executables():
        candidate = _git_bash_candidate_from_git_path(git_path)
        if os.path.isfile(str(candidate)) and str(candidate) not in candidates:
            candidates.append(str(candidate))
        git_exec_path = _git_exec_path(git_path)
        if git_exec_path:
            for candidate in _git_bash_candidates_from_exec_path(git_exec_path):
                if os.path.isfile(str(candidate)) and str(candidate) not in candidates:
                    candidates.append(str(candidate))

    # Well-known Git-for-Windows install locations.
    for candidate in (
        ntpath.join(
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            "Git",
            "bin",
            "bash.exe",
        ),
        ntpath.join(
            os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
            "Git",
            "bin",
            "bash.exe",
        ),
        (
            ntpath.join(local_appdata, "Programs", "Git", "bin", "bash.exe")
            if local_appdata
            else ""
        ),
    ):
        if candidate and os.path.isfile(candidate) and candidate not in candidates:
            candidates.append(candidate)

    # PATH ``bash`` is the LAST resort (mirrors kimix ``bash_tool``): on
    # Windows a plain ``bash`` on PATH is the least trustworthy source — it may
    # be the WSL launcher (C:\Windows\System32\bash.exe) which boots a Linux
    # distro (drives at /mnt/c) and cannot consume native /c/... paths, or a
    # WindowsApps App Execution Alias stub that only offers to install WSL.
    # Reject both so discovery never selects them; when no real Git Bash
    # exists the resolver degrades to PowerShell.
    found = _safe_which("bash")
    if found and found not in candidates:
        if _is_wsl_bash_launcher(found) or _is_windows_apps_stub(found):
            logger.warning(
                "Ignoring non-Git-Bash bash at %s (WSL launcher / WindowsApps "
                "stub); preferring Git Bash",
                found,
            )
        else:
            candidates.append(found)

    for candidate in candidates:
        # Defense in depth: never probe/select WSL launchers or WindowsApps
        # Store stubs even when they slipped in through another source
        # (e.g. HERMES_GIT_BASH_PATH).
        if _is_wsl_bash_launcher(candidate) or _is_windows_apps_stub(candidate):
            logger.debug("Skipping non-Git-Bash candidate %s", candidate)
            continue
        if _bash_starts(candidate):
            if candidate != custom and custom and os.path.isfile(custom):
                logger.warning(
                    "HERMES_GIT_BASH_PATH=%s fails to start; using %s instead",
                    custom,
                    candidate,
                )
            return candidate

    if candidates:
        probe_details = "\n".join(
            detail
            for candidate in candidates
            if (detail := _bash_probe_details_cache.get(candidate))
        )
        if _mandatory_aslr_enabled() is True or _looks_like_msys_spawn_failure(
            probe_details
        ):
            if not raise_if_missing:
                return None
            raise RuntimeError(_git_bash_aslr_help(candidates[0], probe_details))

        if raise_if_missing:
            # Preserve the underlying launch error for failures outside the known
            # MSYS/ASLR class instead of replacing it with a misleading not-found.
            return candidates[0]
        # Non-raising probe: the smoke test failed, so the auto path must not
        # select a broken bash — fall through to PowerShell instead.
        return None

    if raise_if_missing:
        raise RuntimeError(
            "Git Bash is not found on this system. It was explicitly selected via "
            "HERMES_SHELL_TYPE=bash; install Git for Windows or use PowerShell."
        )
    return None


_git_bash_bin_dirs_cache: "list[str] | None" = None


def _git_bash_bin_dirs() -> list[str]:
    """Git Bash's coreutils dirs in ``/etc/profile`` order (mingw first so coreutils
    beat System32 lookalikes); ``[]`` off Windows. A non-login ``bash -c`` (fallback
    when ``bash -l`` is broken) never sources ``/etc/profile``, so without these
    ``cat``/``mktemp``/``mv`` are missing and commands exit 127."""
    global _git_bash_bin_dirs_cache
    if _git_bash_bin_dirs_cache is None:
        _git_bash_bin_dirs_cache = _compute_git_bash_bin_dirs() if _IS_WINDOWS else []
    return _git_bash_bin_dirs_cache


def _compute_git_bash_bin_dirs() -> list[str]:
    try:
        bash = _find_bash()
    except Exception:
        return []
    parent = os.path.dirname(os.path.dirname(bash))  # bash in <root>\bin or <root>\usr\bin (MinGit)
    root = os.path.dirname(parent) if os.path.basename(parent).lower() == "usr" else parent
    subs = ("mingw64/bin", "mingw32/bin", "usr/local/bin", "usr/bin", "bin")
    dirs = (os.path.join(root, *sub.split("/")) for sub in subs)
    return list(dict.fromkeys(d for d in dirs if os.path.isdir(d)))


def _prepend_missing_path_entries(existing_path: str, dirs: list[str]) -> str:
    """Prepend *dirs* missing from *existing_path* (``os.pathsep``); an already-listed
    dir keeps its position; unchanged input when nothing is missing."""
    entries = [e for e in existing_path.split(os.pathsep) if e]
    missing = [d for d in dirs if d not in entries]
    return os.pathsep.join([*missing, *entries]) if missing else existing_path


def _prepend_git_bash_dirs(existing_path: str) -> str:
    """Prepend Git Bash's binary dirs if missing (no-op off Windows), so the
    non-login ``bash -c`` fallback can find coreutils."""
    return _prepend_missing_path_entries(existing_path, _git_bash_bin_dirs())


# POSIX-sh-family shells that understand spawn_local's ``[shell, "-lic", "set +m; …"]``
# invocation; fish, csh/tcsh, nushell, elvish, xonsh would error, so _find_shell
# falls back to bash for them.
# (#42203)
_SPAWN_COMPATIBLE_SHELLS = frozenset({"bash", "zsh", "sh", "dash", "ksh", "mksh"})


def _find_shell() -> str:
    """Find the user's login shell for background process spawning.

    (process_registry.py imports this name.)

    Unlike ``_find_bash_posix`` (which always returns a bash binary for callers
    that explicitly need bash), this function prefers the user's configured
    ``$SHELL`` on POSIX so that ``spawn_local`` uses the shell the user
    actually logs in with.

    On macOS Catalina+ the default login shell is zsh, but
    ``shutil.which("bash")`` still finds the system ``/bin/bash`` (GNU bash
    3.2).  When bash 3.2 is invoked with ``-l`` (login) and stdin is
    ``/dev/null``, it sources ``~/.bash_profile`` which on many macOS setups
    contains ``exec /bin/zsh -l``.  That ``exec`` replaces bash with zsh but
    drops the ``-c`` argument, so the background command never runs — the
    subprocess exits 0 with no output and no side effects.

    Preferring ``$SHELL`` (when it is a POSIX-``sh``-family shell) avoids this
    because zsh/bash/sh/dash/ksh handle ``-lic`` correctly even with
    redirected stdin.

    Only POSIX-sh-family shells are honoured: ``spawn_local`` invokes the
    shell as ``[shell, \"-lic\", \"set +m; <cmd>\"]``, and that ``-lic`` bundle +
    ``set +m`` job-control syntax is NOT understood by fish, csh/tcsh,
    nushell, elvish, xonsh, etc.  Returning such a ``$SHELL`` would trade the
    bash-3.2 swallow for a parse error on every background command, so for any
    non-allowlisted shell we fall back to ``_find_bash_posix`` (the prior
    behaviour).

    On Windows, ``process_registry.spawn_local`` uses ``_resolve_shell``
    (git-bash when available, else PowerShell 7 / Windows PowerShell 5.1)
    instead of this function, so this function is intentionally POSIX-only
    on Windows.
    """

    if not _IS_WINDOWS:
        user_shell = os.environ.get("SHELL")
        if (
            user_shell
            and os.path.isfile(user_shell)
            and os.access(user_shell, os.X_OK)
            and Path(user_shell).name in _SPAWN_COMPATIBLE_SHELLS
        ):
            return user_shell
    return _find_bash_posix()


# --- PATH completion for the terminal subshell ---

# Standard PATH entries for environments with minimal PATH.
_SANE_PATH = ("/opt/homebrew/bin:/opt/homebrew/sbin:"
              "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin")

# Cached directory containing the ``hermes`` console-script.
# ``_SENTINEL`` distinguishes "not resolved yet" from a resolved ``None``.
_SENTINEL = object()
_HERMES_BIN_DIR: "str | None | object" = _SENTINEL


def _resolve_hermes_bin_dir() -> str | None:
    """Directory holding the ``hermes`` console-script, or None (cached). A gateway
    launched by systemd/cron/a desktop launcher lacks the install dir on PATH and bare
    ``hermes`` exits 127. Order: ``which``; absolute ``sys.argv[0]`` naming a real
    hermes executable; ``sys.executable``'s dir if it holds the shim."""
    global _HERMES_BIN_DIR
    if _HERMES_BIN_DIR is not _SENTINEL:
        return _HERMES_BIN_DIR  # type: ignore[return-value]
    which = shutil.which("hermes")
    argv0 = sys.argv[0] if sys.argv else ""
    base = os.path.basename(argv0).lower()
    exe_dir = os.path.dirname(sys.executable) if sys.executable else ""
    shim = "hermes.exe" if _IS_WINDOWS else "hermes"
    if which:
        candidate = os.path.dirname(which)
    elif (os.path.isabs(argv0) and (base == "hermes" or base.startswith("hermes."))
            and os.path.isfile(argv0)):
        candidate = os.path.dirname(argv0)
    else:
        candidate = exe_dir if exe_dir and os.path.isfile(os.path.join(exe_dir, shim)) else None
    _HERMES_BIN_DIR = candidate if candidate and os.path.isdir(candidate) else None
    return _HERMES_BIN_DIR


def _prepend_hermes_bin_dir(existing_path: str) -> str:
    """Prepend the hermes install dir to ``existing_path`` if missing."""
    bin_dir = _resolve_hermes_bin_dir()
    return _prepend_missing_path_entries(existing_path, [bin_dir] if bin_dir else [])


def _managed_runtime_path_entries() -> list[str]:
    """Existing Hermes-managed runtime dirs: ``$HERMES_HOME/node`` (+``/bin``) and
    ``$HERMES_HOME/bin`` (managed ``uv``). Per call, not cached: home is
    profile-scoped and a managed tree can appear mid-process."""
    try:
        from hermes_constants import get_hermes_home, iter_hermes_node_dirs
        return [str(d) for d in (*iter_hermes_node_dirs(), get_hermes_home() / "bin") if d.is_dir()]
    except Exception:
        return []


def _user_local_bin_entries() -> list[str]:
    """``~/.local/bin`` when it exists — the pip --user / pipx / uv-tool install
    target. A backend launched by a non-interactive SSH session, systemd or a GUI
    launcher inherits a PATH without it (only the login shell adds it), so CLIs
    installed there were ``command not found`` from the terminal tool (#111778)."""
    local_bin = Path.home() / ".local" / "bin"
    try:
        return [str(local_bin)] if local_bin.is_dir() else []
    except OSError:
        # HOME can point at a directory this process may not traverse (CI runs
        # with HOME=/root as an unprivileged user); such a home has no usable
        # ~/.local/bin either.
        return []


def _append_missing_sane_path_entries(existing_path: str) -> str:
    """Normalised POSIX PATH with missing sane entries appended: empty entries
    dropped (shells read them as cwd), duplicates collapsed (first wins), then
    missing ``_SANE_PATH`` / managed-runtime / ``~/.local/bin`` dirs appended so
    user entries keep precedence. Windows is a no-op passthrough (native ``;``
    PATH untouched)."""
    if _IS_WINDOWS:
        return existing_path
    # dict preserves first-occurrence order; empty entries dropped.
    ordered = dict.fromkeys(entry for entry in existing_path.split(":") if entry)
    ordered.update(dict.fromkeys([*_SANE_PATH.split(":"), *_managed_runtime_path_entries(),
                                  *_user_local_bin_entries()]))
    return ":".join(ordered)


def _apply_windows_msys_bash_env_defaults(env: dict) -> None:
    """Disable MSYS argument path conversion (``/FO`` -> ``C:/.../git/FO`` breaks
    tasklist/schtasks/wmic/``cmd /c``). Git for Windows honors ``MSYS_NO_PATHCONV``;
    MSYS2/Cygwin bash honor ``MSYS2_ARG_CONV_EXCL`` — set both; users can override.

    Git Bash rewrites arguments that look like Unix paths (``/FO``, ``/TN``, ``/Create``) into
    ``C:/.../git/FO``-style paths, which breaks native Windows commands such as ``tasklist``, ``schtasks``,
    and ``wmic``. Hermes runs terminal commands through bash on Windows, so set the standard MSYS opt-out by
    default. Refs #56700.
    MSYS2-proper and Cygwin bash (which ``_find_bash`` can still return via the final ``shutil.which``
    fallback) ignore it and honor ``MSYS2_ARG_CONV_EXCL`` instead, so set both. ``*`` disables all argv
    conversion — the semantic equivalent of ``MSYS_NO_PATHCONV=1``. Also fixes ``cmd /c`` mangling (#56147).
    """
    if _IS_WINDOWS:
        env.setdefault("MSYS_NO_PATHCONV", "1")
        env.setdefault("MSYS2_ARG_CONV_EXCL", "*")


def _path_env_key(run_env: dict) -> str | None:
    """PATH env key to update without altering Windows casing (``Path`` vs ``PATH``);
    None when a Windows env has no PATH key at all."""
    return next((k for k in run_env if k.upper() == "PATH"), None) if _IS_WINDOWS else "PATH"


def _make_run_env(env: dict) -> dict:
    """Build a run environment with a sane PATH and provider-var stripping. The process env is
    the LAUNCH profile's; under a routed home override its ``.env`` residue is dropped first
    (``strip_launch_profile_env``, a no-op for the launch profile) so the backend's own ``env``
    and the served profile's declared passthrough names are what the child sees."""
    return _scrubbed_env([(dict(strip_launch_profile_env(os.environ.copy()) | env), True)], frozenset(),
                         lambda p: _prepend_git_bash_dirs(_append_missing_sane_path_entries(p)))


# --- Hermes venv / repo-root detection (module-level, computed once) ---
# Owned here; read lazily by tools.environments.local_pythonpath (tests patch here).
# The Electron app prepends the repo root to PYTHONPATH so the backend can ``import
# tools``; other subprocesses must not inherit it. Aliases: launchers may emit other
# spellings — the Windows gateway launcher renders Hermes-owned paths under the
# configured HERMES_HOME spelling (possibly a junction to another drive).
_hermes_repo_root: Path = Path(__file__).resolve().parents[2]
_hermes_repo_root_aliases: tuple[Path, ...] = _build_hermes_repo_root_aliases(
    _hermes_repo_root, Path(__file__).absolute().parents[2], get_process_hermes_home())
_in_venv: bool = (getattr(sys, "base_prefix", sys.prefix) != sys.prefix
                  or hasattr(sys, "real_prefix"))  # real_prefix: virtualenv<20
_hermes_site_packages: list[Path] | None = None  # lazily cached by local_pythonpath


# --- Login-shell init files ---
def _read_terminal_shell_init_config() -> tuple[list[str], bool]:
    """(shell_init_files, auto_source_bashrc) from config.yaml; defaults on any
    failure so terminal execution never breaks."""
    try:
        from hermes_cli.config import load_config
        terminal_cfg = (load_config() or {}).get("terminal") or {}
        files = terminal_cfg.get("shell_init_files") or []
        if not isinstance(files, list):
            files = []
        return [str(f) for f in files if f], bool(terminal_cfg.get("auto_source_bashrc", True))
    except Exception:
        return [], True


def _resolve_shell_init_files() -> list[str]:
    """Files to source before the login-shell snapshot (``~``/``${VAR}`` expanded,
    missing dropped). ``auto_source_bashrc`` applies only without an explicit list:
    ~/.profile and ~/.bash_profile first (no interactivity guard; where
    n/nvm/asdf/pyenv add PATH), ~/.bashrc last (Debian's returns early when
    non-interactive, but guard-less bashrcs keep working)."""
    explicit, auto_bashrc = _read_terminal_shell_init_config()
    candidates = explicit or (["~/.profile", "~/.bash_profile", "~/.bashrc"]
                              if auto_bashrc and not _IS_WINDOWS else [])
    resolved: list[str] = []
    for raw in candidates:
        try:
            path = os.path.expandvars(os.path.expanduser(raw))
            if path and os.path.isfile(path):
                resolved.append(path)
        except Exception:
            continue
    return resolved


def _prepend_shell_init(cmd_string: str, files: list[str]) -> str:
    """Prepend guarded, silent ``source <file>`` lines: ``set +e`` keeps going on
    errors, ``2>/dev/null`` hides noisy prompts, ``|| true`` neutralises the status."""
    if not files:
        return cmd_string
    safe = [p.replace("'", "'\\''") for p in files]
    prelude = ["set +e", *(f"[ -r '{p}' ] && . '{p}' 2>/dev/null || true" for p in safe)]
    return "\n".join(prelude) + "\n" + cmd_string


# --- Process-group teardown (POSIX) ---
def _wait_for_group_exit(proc, pgid: int, timeout: float) -> bool:
    """Wait until the process group is gone, reaping the wrapper as we go (a dead
    but unreaped group leader still makes ``killpg(pgid, 0)`` succeed).
    POSIX-only; callers are behind the _IS_WINDOWS gate."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            proc.poll()
        except Exception:
            pass
        try:
            os.killpg(pgid, 0)  # windows-footgun: ok — POSIX process-group alive probe
        except ProcessLookupError:
            return True
        except PermissionError:
            pass  # exists, even if we cannot signal it
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def _sweep_escaped_descendants(descendants: list, pgid: int) -> None:
    """SIGKILL snapshotted survivors that escaped the process group via ``setsid``
    — after TERM→KILL so in-group members keep their grace; psutil's identity-aware
    Process skips recycled PIDs. POSIX-only (see _IS_WINDOWS gate in caller)."""
    for child in descendants:
        try:
            if not child.is_running():
                continue
            try:
                if os.getpgid(child.pid) == pgid:
                    continue  # group-kill already covers it
            except OSError:  # ProcessLookupError / PermissionError included
                pass
            child.kill()
        except Exception:
            continue


def _kill_process_group_posix(proc) -> None:
    """TERM the group, wait, KILL, then sweep setsid escapees. Descendants are
    snapshotted BEFORE the first signal — once the wrapper dies they reparent to
    init — and we wait on the group, not the wrapper, which can exit before
    grandchildren under load. POSIX-only (_IS_WINDOWS handled by the caller)."""
    try:
        pgid = os.getpgid(proc.pid)
    except ProcessLookupError:
        if (pgid := getattr(proc, "_hermes_pgid", None)) is None:
            raise
    try:  # psutil children snapshot; empty on any failure (must never break the kill)
        import psutil
        descendants = psutil.Process(proc.pid).children(recursive=True)
    except Exception:
        descendants = []
    try:
        os.killpg(pgid, signal.SIGTERM)  # windows-footgun: ok — POSIX only (see _IS_WINDOWS gate in caller)
        if not _wait_for_group_exit(proc, pgid, 1.0):
            os.killpg(pgid, signal.SIGKILL)  # windows-footgun: ok — POSIX only (see _IS_WINDOWS gate in caller)
            _wait_for_group_exit(proc, pgid, 2.0)
            with contextlib.suppress(subprocess.TimeoutExpired, OSError):
                proc.wait(timeout=0.2)
    except ProcessLookupError:
        pass
    _sweep_escaped_descendants(descendants, pgid)


def _kill_process_windows(proc) -> None:
    """Identity-checked terminate (start time guards against PID reuse), else kill."""
    try:
        from gateway.status import get_process_start_time, terminate_pid
        terminate_pid(proc.pid, force=True, expected_start_time=get_process_start_time(proc.pid))
    except Exception:
        proc.kill()
    with contextlib.suppress(subprocess.TimeoutExpired, OSError):
        proc.wait(timeout=2.0)


# ---------------------------------------------------------------------------
# [CN-fork] Windows shell machinery
#
# P-016/P-019/P-XXX: PowerShell (pwsh -> powershell 5.1) dispatch, the persistent
# PowerShell session + cmd.exe fast paths (P-042), and the pwsh_transform /
# pwsh_fix wiring.
# P-050/P-052/P-058: Git Bash as an explicit opt-in and as the Windows *default*
# shell when a working install exists (`_resolve_shell`), the kimi `bash_tool`
# discovery chain (env override -> managed portable Git -> known locations ->
# PATH -> git.exe chain), MSYSTEM neutralization and the bash_fix wiring.
# ---------------------------------------------------------------------------
def _safe_which(cmd: str) -> str | None:
    try:
        return shutil.which(cmd)
    except AttributeError:
        return None

def _inject_context_hermes_home(env: dict) -> None:
    """Bridge the context-local Hermes home override into subprocess env."""
    try:
        from hermes_constants import get_hermes_home_override

        value = get_hermes_home_override()
        if value:
            env["HERMES_HOME"] = value
    except Exception:
        pass

def _scrub_delegated_child_kanban_env(env: dict[str, str]) -> dict[str, str]:
    """Strip dispatcher-owned Kanban env from delegate_task child subprocesses."""
    try:
        from agent.delegation_context import (
            is_delegated_child_process_context,
            scrub_kanban_env,
        )

        if is_delegated_child_process_context():
            return scrub_kanban_env(env)
    except Exception:
        pass
    return env

def _resolve_pwsh_session_reuse(shell_type: str) -> bool:
    """Return True when the persistent-PowerShell-session fast path is enabled.

    [CN-fork P-042] Windows + PowerShell only.  Canonical setting is
    ``terminal.powershell_session_reuse`` in config.yaml; the internal
    ``HERMES_PWSH_SESSION_REUSE`` env var bridges it (and lets tests/subprocess
    children flip it) and takes precedence when set.  Default OFF — a persistent
    session carries shell state between commands, which is a deliberate opt-in.
    """
    if not _IS_WINDOWS or shell_type not in ("powershell", "pwsh"):
        return False
    override = os.environ.get("HERMES_PWSH_SESSION_REUSE")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        return bool((cfg.get("terminal") or {}).get("powershell_session_reuse", False))
    except Exception:
        return False

class _SessionFallback(Exception):
    """Raised inside a fast path to punt a command to the spawn path."""

_SIMPLE_COMMAND_PATTERNS = (
    "dir", "echo", "type", "copy", "move", "del", "erase",
    "mkdir", "md", "rmdir", "rd", "cd", "chdir", "cls",
    "ver", "whoami", "hostname", "where",
)

_SIMPLE_COMMAND_RE = re.compile(
    r"^(?:" + "|".join(_SIMPLE_COMMAND_PATTERNS) + r")\b",
    re.IGNORECASE,
)

_CMD_METACHAR_RE = re.compile(r"""[|&<>;`$%()'\"^!*?\[\]{}\n\r]""")

_CMD_STATEFUL_RE = re.compile(
    r"^(?:cd|chdir|pushd|popd|set|setx|start|call|exit)\b", re.IGNORECASE
)

def is_simple_command(command: str) -> bool:
    """True when *command* looks like a bare cmd.exe-compatible builtin.

    Coarse classifier from the P-042 plan: matches on the leading verb only
    (``dir``/``echo``/``type``/``copy``/``move``/``del``/``mkdir``/``rmdir``/
    ``cd`` …).  Callers that actually *route* to cmd.exe must additionally pass
    :func:`_cmd_fast_path_eligible`, which rejects metacharacters and stateful
    builtins so behaviour can't diverge from PowerShell.
    """
    return bool(command and _SIMPLE_COMMAND_RE.match(command.strip()))

def _cmd_fast_path_eligible(command: str) -> bool:
    """Strict gate: *command* is a bare builtin AND safe to run via cmd.exe.

    Requires :func:`is_simple_command`, no shell metacharacters (so quoting /
    redirection / chaining / globbing can't be reinterpreted), and no cwd/env-
    mutating builtin (a one-shot ``cmd /c`` couldn't persist the change the
    tracked session expects).  Everything it rejects simply falls through to the
    unchanged PowerShell path — safety over coverage.
    """
    if not command:
        return False
    stripped = command.strip()
    if not is_simple_command(stripped):
        return False
    if _CMD_METACHAR_RE.search(stripped):
        return False
    if _CMD_STATEFUL_RE.match(stripped):
        return False
    return True

def _resolve_cmd_fast_path(shell_type: str) -> bool:
    """True when the opt-in cmd.exe fast path is enabled (Windows + PowerShell).

    Canonical setting ``terminal.cmd_fast_path`` in config.yaml; the internal
    ``HERMES_CMD_FAST_PATH`` env var bridges it and wins when set.  Default OFF
    — the persistent-session fast path is faster and preserves PowerShell
    semantics, so cmd.exe routing is only for deployments that opt in.
    """
    if not _IS_WINDOWS or shell_type not in ("powershell", "pwsh"):
        return False
    override = os.environ.get("HERMES_CMD_FAST_PATH")
    if override is not None:
        return override.strip().lower() in ("1", "true", "yes", "on")
    try:
        from hermes_cli.config import load_config

        cfg = load_config() or {}
        return bool((cfg.get("terminal") or {}).get("cmd_fast_path", False))
    except Exception:
        return False

def _decode_cmd_output(data: bytes) -> str:
    """Decode cmd.exe output: UTF-8 first, then the system ANSI code page.

    cmd builtins emit text in the console/OEM code page (cp936/GBK on a Chinese
    Windows), so a hard UTF-8 decode would mojibake CJK.  Mirrors the file-read
    fallback in file_operations (``_decode_file_bytes``).
    """
    if not data:
        return ""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if _IS_WINDOWS:
        try:
            return data.decode("mbcs", errors="replace")
        except (LookupError, ValueError):
            pass
    return data.decode("utf-8", errors="replace")

def _encode_startup_script(script: str) -> str:
    """Encode a multi-line startup script as a self-decoding one-liner.

    Long multi-line scripts passed through the Windows command line to MSYS2
    bash get corrupted (argv quoting heuristics).  A single-line base64+gzip
    payload contains only safe ASCII characters and sidesteps every quoting
    issue; the receiving shell decodes and evals it.

    Ported from kimix ``bash_tool._encode_startup_script`` (stdlib
    ``base64``/``gzip`` replace kimix's ``pybase64`` — identical output).
    This is one third of the interactive-Git-Bash bootstrap set, together
    with :func:`bash_compatibility_prelude` (bash_fix.py) and
    :func:`_with_msystem_neutralized`.
    """
    payload = base64.b64encode(gzip.compress(script.encode("utf-8"))).decode("ascii")
    return "eval \"$(printf '%s' '" + payload + "' | base64 -d | gzip -d)\""

def _where_git_executables() -> list[str]:
    """Return candidate git.exe paths reported by ``where.exe git``.

    Ported from kimix ``bash_tool._where_git_executables``.  Windows-only by
    nature (``where.exe`` is a cmd.exe builtin); returns ``[]`` on any error
    or when ``where.exe`` is unavailable.
    """
    try:
        result = subprocess.run(
            ["where.exe", "git"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            creationflags=windows_hide_flags() if _IS_WINDOWS else 0,
        )
    except OSError:
        return []
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]

def _git_bash_candidate_from_git_path(git_path: str) -> Path:
    """Derive ``<gitRoot>/bin/bash.exe`` from the path to ``git.exe``."""
    if "/" in git_path and "\\" not in git_path:
        return Path(git_path).parent.parent / "bin" / "bash.exe"
    normalized = ntpath.normpath(
        ntpath.join(ntpath.dirname(git_path), "..", "bin", "bash.exe")
    )
    return Path(normalized)

def _git_exec_path(git_path: str) -> str | None:
    """Run ``git --exec-path`` and return the first non-empty line."""
    try:
        result = subprocess.run(
            [git_path, "--exec-path"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
            creationflags=windows_hide_flags() if _IS_WINDOWS else 0,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        exec_path = line.strip()
        if exec_path:
            return exec_path
    return None

def _git_install_root_from_exec_path(exec_path: str) -> str | None:
    """Return the Git for Windows install root given a ``mingw*/libexec/git-core`` path."""
    if "/" in exec_path and "\\" not in exec_path:
        current_path = Path(exec_path)
        for parent in (current_path, *current_path.parents):
            if parent.name.casefold() in {"mingw32", "mingw64"}:
                return str(parent.parent)
        return None
    current = ntpath.normpath(exec_path)
    while True:
        parent, name = ntpath.split(current)
        if name.casefold() in {"mingw32", "mingw64"}:
            return parent
        if parent == current:
            return None
        current = parent

def _git_bash_candidates_from_exec_path(exec_path: str) -> list[Path]:
    """Return candidate ``bash.exe`` paths derived from ``git --exec-path``."""
    if "/" in exec_path and "\\" not in exec_path:
        normalized_exec_path = Path(exec_path)
        install_root = _git_install_root_from_exec_path(exec_path)
        if install_root is not None:
            return [Path(install_root) / "bin" / "bash.exe"]
        return [normalized_exec_path.parent.parent / "bin" / "bash.exe"]
    normalized_exec_path = ntpath.normpath(exec_path)
    install_root = _git_install_root_from_exec_path(normalized_exec_path)
    if install_root is not None:
        return [Path(ntpath.join(install_root, "bin", "bash.exe"))]
    return [
        Path(
            ntpath.normpath(
                ntpath.join(normalized_exec_path, "..", "..", "bin", "bash.exe")
            )
        )
    ]

def _bash_candidates_macos() -> list[Path]:
    """Well-known bash locations for macOS (Homebrew/MacPorts).

    Ported from kimix ``bash_tool._bash_candidates_macos``: newer Homebrew /
    MacPorts bash builds are preferred over the aging system bash 3.2.
    """
    return [
        Path("/opt/homebrew/bin/bash"),
        Path("/usr/local/bin/bash"),
        Path("/opt/local/bin/bash"),
    ]

def _bash_candidates_system() -> list[Path]:
    """Standard system bash locations (Linux and macOS)."""
    return [Path("/bin/bash"), Path("/usr/bin/bash")]

def _git_bash_for_macos() -> str | None:
    """Return bash bundled with the official Git installer for macOS, if any.

    Ported from kimix ``bash_tool._git_bash_for_macos``: the official macOS
    Git installer ships a bash under ``<gitRoot>/bin/bash`` or
    ``<gitRoot>/usr/bin/bash``.
    """
    git_path = _safe_which("git")
    if not git_path:
        return None
    git_exe = Path(git_path).resolve()
    if git_exe.parent.name.lower() == "bin":
        git_root = git_exe.parent.parent
    else:
        git_root = git_exe.parent
    for subpath in ("bin/bash", "usr/bin/bash"):
        candidate = git_root / subpath
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate.resolve())
    return None

def _find_bash_posix() -> str:
    """Find bash on non-Windows systems.

    On macOS, newer Homebrew/MacPorts bash and the bash bundled with the
    official Git installer are preferred over the aging system bash 3.2
    (mirrors kimix ``find_bash``); the ordering for Linux is unchanged.
    """
    if sys.platform == "darwin":
        for candidate in _bash_candidates_macos():
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate.resolve())
        git_bash = _git_bash_for_macos()
        if git_bash:
            return git_bash
    bash_on_path = _safe_which("bash")
    return (
        bash_on_path
        or ("/usr/bin/bash" if os.path.isfile("/usr/bin/bash") else None)
        or ("/bin/bash" if os.path.isfile("/bin/bash") else None)
        or os.environ.get("SHELL")
        or "/bin/sh"
    )

def _is_windows_apps_stub(bash_path: str) -> bool:
    """Return True when *bash_path* points into the WindowsApps directory.

    Windows ships ``bash.exe`` under ``%LOCALAPPDATA%\\Microsoft\\WindowsApps``
    as an App Execution Alias (Microsoft Store stub) that only offers to
    install WSL; it is not a real bash and must never be treated as one.
    Mirrors kimix ``bash_tool._is_windows_apps_stub`` (``_find_pwsh`` applies
    the same WindowsApps exclusion inline).
    """
    if not bash_path:
        return False
    normalized = ntpath.normpath(bash_path)
    return "windowsapps" in {part.lower() for part in normalized.split("\\")}

_wsl_bash_launcher_cache: "dict[str, bool]" = {}

def _is_wsl_bash_launcher(bash_path: str) -> bool:
    """Return True when *bash_path* is the Windows WSL bash launcher.

    The WSL launcher lives at ``%WINDIR%\\System32\\bash.exe`` (or the
    SysWOW64 twin) and boots a Linux distro whose filesystem mounts Windows
    drives at ``/mnt/c`` — it cannot consume the native ``/c/...`` paths the
    terminal wrapper emits, so it must never be selected as Git Bash.

    The cheap path check covers the standard launcher; a cached ``uname -sr``
    probe catches WSL bash reached through other shims (e.g. a distro bash
    exported into PATH), where the kernel string is ``Linux ... microsoft``
    instead of the MSYS/MINGW ``uname`` marker Git Bash reports.
    """
    cached = _wsl_bash_launcher_cache.get(bash_path)
    if cached is not None:
        return cached

    result = False
    resolved = ntpath.normpath(bash_path) if _IS_WINDOWS else os.path.realpath(bash_path)

    if ntpath.basename(resolved).lower() == "bash.exe":
        windir = os.environ.get("WINDIR", r"C:\Windows")
        lower = ntpath.normcase(resolved)
        for sysdir in (
            ntpath.join(windir, "System32"),
            ntpath.join(windir, "SysWOW64"),
        ):
            sysdir_lower = ntpath.normcase(sysdir)
            if lower.startswith(sysdir_lower + "\\") or lower == sysdir_lower:
                result = True
                break

    if not result:
        try:
            # `uname -sr` on WSL2 reports "Linux 6.x.y-microsoft-standard-WSL2"
            # (the microsoft marker lives in the release, not `uname -s`);
            # Git Bash / MSYS reports "MINGW64_NT-..." / "MSYS_NT-...".
            probe = subprocess.run(
                [bash_path, "--noprofile", "--norc", "-c", "uname -sr"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                creationflags=windows_hide_flags() if _IS_WINDOWS else 0,
            )
            kernel = (probe.stdout or "").strip().lower()
            result = kernel.startswith("linux") and "microsoft" in kernel
        except Exception:
            result = False

    _wsl_bash_launcher_cache[bash_path] = result
    return result

def _is_git_bash_install(bash_path: str) -> bool:
    """Return True when *bash_path* is a bash of a Git for Windows install.

    Both layouts are accepted: the native launcher ``<root>/bin/bash.exe``
    and the real MSYS2 bash ``<root>/usr/bin/bash.exe``.  Git for Windows
    always ships a ``<root>/cmd/git.exe`` marker; MSYS2 (which also ships
    ``usr/bin/bash.exe``) has no such marker, so ``MSYSTEM`` neutralization
    stays limited to Git Bash and never affects real MSYS2 shells.

    Ported from kimix ``bash_tool._is_git_bash_install``.  The marker probe
    is drive-anchored (``C:\\...``, never the drive-relative ``C:...``), so
    the check is CWD-independent.
    """
    if not bash_path:
        return False
    if "/" in bash_path and "\\" not in bash_path:
        path = Path(bash_path)
        if path.name.casefold() != "bash.exe" or path.parent.name.casefold() != "bin":
            return False
        if path.parent.parent.name.casefold() == "usr":
            root_path = path.parent.parent.parent
        else:
            root_path = path.parent.parent
        return (root_path / "cmd" / "git.exe").is_file()
    text = ntpath.normpath(bash_path)
    drive, tail = ntpath.splitdrive(text)
    parts = [p.lower() for p in tail.split("\\") if p]
    # expect either ...\usr\bin\bash.exe or ...\bin\bash.exe
    if len(parts) < 3 or parts[-1] != "bash.exe" or parts[-2] != "bin":
        return False
    if parts[-3] == "usr":
        root = "\\".join(parts[:-3])
    else:
        root = "\\".join(parts[:-2])
    root_path = (drive + "\\" if drive else "") + root
    return os.path.isfile(ntpath.join(root_path, "cmd", "git.exe"))

_MSYSTEM_NEUTRALIZE_PREFIX = "export MSYSTEM=; "

def _with_msystem_neutralized(cmd: str, bash_path: str | None) -> str:
    """Prepend an ``MSYSTEM``-neutralizing statement to *cmd* on Git Bash.

    Git Bash's ``bin/bash.exe`` launcher unconditionally injects
    ``MSYSTEM=MINGW64`` into the shell (setting ``MSYSTEM`` in the parent
    environment is useless), and the MSYS2 runtime re-injects the variable
    into children when it is *absent* (``unset`` does not stick).  Exporting
    an *empty* value at the start of the command makes xmake — a child
    process — see an empty ``MSYSTEM`` and default to the ``windows``/MSVC
    platform, while the launcher's PATH setup stays intact.  Limited to Git
    for Windows bash on Windows; all other platforms and shells run the
    command unchanged.

    Ported from kimix ``bash_tool._with_msystem_neutralized``.  In Hermes the
    prefix is prepended per command inside ``LocalEnvironment._wrap_command``
    (it runs AFTER the snapshot source inside the eval, so children spawned
    by the user command still see the empty value).
    """
    if sys.platform == "win32" and _is_git_bash_install(bash_path or ""):
        return _MSYSTEM_NEUTRALIZE_PREFIX + cmd
    return cmd

def _find_powershell() -> str:
    r"""Return ``powershell.exe`` path on Windows.

    Windows PowerShell 5.1 ships with every Windows 10/11 system at
    ``C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe``
    and is always on PATH.  No probing needed — just return the first
    ``powershell.exe`` found via ``shutil.which``.
    """
    return _safe_which("powershell.exe") or "powershell.exe"

def _find_pwsh() -> str | None:
    """Detect PowerShell 7 (pwsh) using multiple strategies.

    Returns the full path to pwsh.exe, or None if not found.
    """
    # Strategy 1: PATH search — skip Windows App Execution Aliases (stubs)
    # that live under ``%LOCALAPPDATA%\Microsoft\WindowsApps``.  These stubs
    # are reparse points that can fail in non-interactive / service contexts
    # (``CreateProcessAsUserW`` error 1312).  Prefer real PE binaries from
    # subsequent strategies.
    path = _safe_which("pwsh") or _safe_which("pwsh.exe")
    if path and "WindowsApps" not in path:
        return path

    # Strategy 2: Common install location via %%ProgramFiles%%
    program_files = os.environ.get("ProgramFiles", r"C:\Program Files")
    candidate = ntpath.join(program_files, "PowerShell", "7", "pwsh.exe")
    if os.path.isfile(candidate):
        return candidate

    # Strategy 3: Registry App Paths
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\pwsh.exe",
            0, winreg.KEY_READ
        ) as key:
            reg_path, _ = winreg.QueryValueEx(key, "")
            if reg_path and os.path.isfile(reg_path):
                return reg_path
    except (OSError, FileNotFoundError, ImportError):
        pass

    # Strategy 4: LocalAppData (Microsoft Store / winget install) — last resort.
    # Verify the file is a real PE binary (size > 10KB, starts with "MZ")
    # because Windows Store stubs are near-empty reparse points that behave
    # differently under service-managed processes.
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    if local_app_data:
        candidate = ntpath.join(
            local_app_data, "Microsoft", "WindowsApps", "pwsh.exe"
        )
        if os.path.isfile(candidate) and os.path.getsize(candidate) > 10240:
            return candidate

    return None

def _resolve_shell() -> tuple[str, str]:
    """Determine which shell to use for local command execution.

    On Windows with ``HERMES_SHELL_TYPE`` unset or ``"auto"`` (the default):
    prefers **git-bash** when a working install exists (``_find_bash`` with
    ``raise_if_missing=False`` — discovery + ``_bash_starts()`` smoke test),
    then PowerShell 7 (``pwsh``), falling back to Windows PowerShell 5.1
    (``powershell.exe``) which ships with every Windows 10/11 system.

    On non-Windows: always uses bash.

    Env overrides (respected, never git-bash for the explicit PowerShell
    values):
      ``HERMES_SHELL_TYPE`` — ``"powershell"``, ``"pwsh"``, ``"bash"``,
      or ``"auto"`` (default: ``"auto"`` on Windows, ``"bash"`` otherwise).
      ``HERMES_SHELL_TYPE=bash`` on Windows selects pre-installed Git Bash
      via ``_find_bash()`` (no auto-install); when Git Bash is missing or
      cannot start, it degrades gracefully to the PowerShell chain
      (``pwsh`` → ``powershell.exe``) instead of failing startup.
      ``HERMES_SHELL_TYPE=pwsh`` / ``powershell`` select PowerShell only
      (pwsh → 5.1 fallback) and never probe for git-bash.

    Returns ``(shell_type, shell_path)`` where *shell_type* is
    ``"pwsh"``, ``"powershell"``, or ``"bash"``.
    """
    shell_type = os.environ.get("HERMES_SHELL_TYPE", "auto").strip().lower() or "auto"

    if _IS_WINDOWS:
        if shell_type == "auto":
            # Default preference: git-bash (if a working one exists) →
            # PowerShell 7 → Windows PowerShell 5.1.
            bash_path = _find_bash(raise_if_missing=False)
            if bash_path:
                logger.info("Selected shell: bash at %s", bash_path)
                return ("bash", bash_path)
            pwsh_path = _find_pwsh()
            if pwsh_path:
                logger.info("Selected shell: pwsh at %s", pwsh_path)
                return ("pwsh", pwsh_path)
            ps_path = _find_powershell()
            logger.info("Selected shell: powershell at %s", ps_path)
            return ("powershell", ps_path)
        if shell_type in ("pwsh", "powershell"):
            # Explicit PowerShell: prefer pwsh (PowerShell 7) when available;
            # never git-bash.
            pwsh_path = _find_pwsh()
            if pwsh_path:
                logger.info("Selected shell: pwsh at %s", pwsh_path)
                return ("pwsh", pwsh_path)
            ps_path = _find_powershell()
            logger.info("Selected shell: powershell at %s", ps_path)
            return ("powershell", ps_path)
        if shell_type == "bash":
            bash_path = None
            bash_reason = ""
            try:
                bash_path = _find_bash()
            except RuntimeError as exc:
                # _find_bash raises when Git Bash is not installed at all, or
                # when every discovered candidate fails its start probe (the
                # Mandatory-ASLR failure class carries its own remediation
                # text).  None of these should brick the terminal tool —
                # degrade to PowerShell instead.
                bash_reason = f" ({exc})"
            if bash_path and not _bash_starts(bash_path):
                # _find_bash preserves the first candidate's launch error for
                # failures outside the known MSYS/ASLR class instead of
                # raising; treat a probe-failed bash as unavailable too.
                bash_reason = f" (Git Bash at {bash_path} failed its start probe)"
                bash_path = None
            if bash_path:
                logger.info("Selected shell: bash at %s", bash_path)
                return ("bash", bash_path)
            logger.warning(
                "HERMES_SHELL_TYPE=bash requested Git Bash on Windows but it is "
                "not usable; falling back to PowerShell.%s",
                bash_reason,
            )
            pwsh_path = _find_pwsh()
            if pwsh_path:
                logger.info("Selected shell: pwsh at %s (Git Bash fallback)", pwsh_path)
                return ("pwsh", pwsh_path)
            ps_path = _find_powershell()
            logger.info(
                "Selected shell: powershell at %s (Git Bash fallback)", ps_path
            )
            return ("powershell", ps_path)
        raise RuntimeError(
            f"Unknown HERMES_SHELL_TYPE={shell_type!r} on Windows. "
            "Supported values: 'auto' (default → bash/pwsh/powershell), 'pwsh', "
            "'powershell', 'bash' (requires pre-installed Git Bash)."
        )

    # Non-Windows: always bash
    bash_path = _find_bash_posix()
    if bash_path:
        logger.info("Selected shell: bash at %s", bash_path)
        return ("bash", bash_path)

    raise RuntimeError("No usable shell found.")

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

_bash_starts_cache: dict[str, bool] = {}

_mandatory_aslr_enabled_cache: "bool | None" = None

class LocalEnvironment(BaseEnvironment):
    """Run commands directly on the host: every execute() spawns a fresh bash;
    the session snapshot preserves env vars across calls; CWD persists via the
    stdout marker."""

    _sudo_nopasswd_probe_supported = True
    _profile_scoped_passthrough = True
    # Commands run on the Hermes host itself — controller-side platform behavior
    # (macOS TCC pruning, etc.) legitimately applies here.
    is_local = True

    def _additional_profile_scoped_passthrough_names(self) -> tuple[str, ...]:
        """First-party ``BUZZ_*`` names present in the env, excluded from the shared
        session snapshot. env_passthrough can never list them (it refuses blocklisted
        names), so under a multiplexed gateway profile A's BUZZ_PRIVATE_KEY would land
        in the snapshot and be sourced by profile B. Prefix-only and monotonic on
        purpose: conservative even when the context-gated carve-out is inactive."""
        merged = dict(os.environ | self.env)
        return tuple(sorted(
            name for name in merged
            if isinstance(name, str) and _matches_terminal_first_party_prefix(name)))

    def __init__(self, cwd: str = "", timeout: int = 60, env: dict = None):
        super().__init__(cwd=_resolve_local_initial_cwd(cwd), timeout=timeout, env=env)
        # [CN-fork P-058] Windows shell resolution: an explicit `terminal.shell`
        # wins, otherwise git-bash (when a working install exists) → pwsh →
        # Windows PowerShell 5.1. POSIX hosts always resolve bash.
        self._shell_type, self._shell_path = _resolve_shell()
        # [CN-fork P-042] persistent PowerShell session (opt-in, Windows only).
        # Bash sessions skip this fast path: the resolver functions below
        # return False unless shell_type is "powershell"/"pwsh".
        self._pwsh_session = None
        self._pwsh_session_lock = threading.Lock()
        self._pwsh_session_reuse = _resolve_pwsh_session_reuse(self._shell_type)
        # [CN-fork P-042 #3] cmd.exe fast path for trivial builtins (opt-in).
        # PowerShell-only by design — bash sessions skip it (False).
        self._cmd_fast_path = _resolve_cmd_fast_path(self._shell_type)
        self.init_session()

    def get_temp_dir(self) -> str:
        """Shell-safe writable temp dir. Precedence: ``TERMINAL_TEMP_DIR``, TMPDIR/TMP/TEMP
        (Termux has no system temp dir), ``HERMES_HOME/cache/terminal`` (real storage: a
        tmpfs system temp dir fills under Hermes load; pruned by ``cleanup_terminal_temp_cache``),
        ``tempfile.gettempdir()``; backend env before process env so terminal.env
        overrides work. Windows: ``%TEMP%`` often has spaces that break unquoted bash,
        so always the HERMES_HOME cache dir with forward slashes (bash- and Python-valid)."""
        if _IS_WINDOWS:
            cache_dir = (_default_terminal_temp_dir()
                         or Path(tempfile.gettempdir()) / "hermes_terminal")
            cache_dir.mkdir(parents=True, exist_ok=True)
            _prune_terminal_temp_once()
            return str(cache_dir).replace("\\", "/")
        def _posix(p: str) -> str:
            return p.rstrip("/") or "/"
        for env_var in ("TERMINAL_TEMP_DIR", "TMPDIR", "TMP", "TEMP"):
            candidate = self.env.get(env_var) or os.environ.get(env_var)
            if candidate and candidate.startswith("/") and (
                    env_var != "TERMINAL_TEMP_DIR" or os.path.isdir(candidate)):
                return _posix(candidate)
        try:
            cache_dir = _default_terminal_temp_dir()
            cache_dir.mkdir(parents=True, exist_ok=True)
            resolved = str(cache_dir)
            if resolved.startswith("/") and os.access(resolved, os.W_OK | os.X_OK):
                _prune_terminal_temp_once()
                return _posix(resolved)
        except Exception:
            pass
        # tempfile's own candidate walk already covers the system temp dir.
        fallback = tempfile.gettempdir()
        return _posix(fallback if fallback.startswith("/") else os.path.abspath(fallback))

    # --- [CN-fork P-016/P-019/P-XXX] Windows PowerShell shell dispatch, ---
    # --- persistent session + cmd.exe fast paths (P-042).                 ---

    def _run_powershell(self, cmd_string: str, *, login: bool = False,
                  timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """Spawn a PowerShell process to run *cmd_string*.

        Uses ``-NoProfile`` for speed (profile loading can be slow).
        Windows paths are handled natively — no backslash conversion needed.

        PS7→PS5.1 down-leveling (``pwsh_transform``) is **not** done here.  It
        runs in :meth:`_wrap_command_powershell` on the raw user command, before
        the command is embedded as a single-quoted ``Invoke-Expression`` literal
        (P-037).  Transforming the assembled wrapper at this point would be a
        no-op — the user's command sits inside a single-quoted string that the
        transform's region mask skips.
        """
        # Refresh PATH/PATHEXT from registry so newly installed tools are
        # discoverable (e.g. WinGet, MSI).  No-op on non-Windows.
        refresh_env_from_registry()

        # Force PowerShell to emit UTF-8 on stdout/stderr regardless of the
        # system code page.
        from tools.environments.windows_env import ps_with_utf8
        cmd_string = ps_with_utf8(cmd_string)

        _PS_MAX_CMDLINE = 30000
        _tmp_ps1 = None
        if len(cmd_string) > _PS_MAX_CMDLINE:
            fd, tmp_path = tempfile.mkstemp(suffix=".ps1", prefix="hermes_cmd_")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(cmd_string)
            _tmp_ps1 = tmp_path
            args = [self._shell_path, "-NoP", "-NonI", "-Exec", "Bypass", "-NoL", "-File", tmp_path]
        else:
            args = [self._shell_path, "-NoP", "-NonI", "-Exec", "Bypass", "-NoL", "-C", cmd_string]
        run_env = _make_run_env(self.env)
        safe_cwd = _resolve_safe_cwd(self.cwd)
        if safe_cwd != self.cwd:
            # On Windows, _resolve_safe_cwd calls os.path.normpath which
            # converts forward slashes to backslashes.  Compare normalized
            # forms so a benign slash-normalization doesn't trigger a warning.
            normalized_self = os.path.normpath(
                _msys_to_windows_path(self.cwd) if _IS_WINDOWS else self.cwd
            )
            if safe_cwd != normalized_self:
                logger.warning(
                    "LocalEnvironment cwd %r is missing on disk; "
                    "falling back to %r so terminal commands keep working.",
                    self.cwd,
                    safe_cwd,
                )
            self.cwd = safe_cwd

        _popen_kwargs = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)} if _IS_WINDOWS else {}

        proc = subprocess.Popen(
            args,
            text=True,
            env=run_env,
            encoding="utf-8",
            errors="replace",
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            cwd=safe_cwd,
            **_popen_kwargs,
        )

        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)

        if _tmp_ps1 is not None:
            # Attach the temp-file path so ``_wait_for_process``
            # (or the caller) can remove it after the child exits.
            proc._hermes_tmp_ps1 = _tmp_ps1

        return proc

    def _wrap_command_powershell(self, command: str, cwd: str) -> str:
        """Build a PowerShell script that cd's, runs the command, and emits
        CWD marker + exit code.

        PowerShell equivalents:
          ``cd``      → ``Set-Location`` (``cd`` also works as alias)
          ``$?``      → ``$LASTEXITCODE``
          ``pwd -P``  → ``Get-Location``
        """

        # [CN-fork] P-037 / P-XXX: down-level PS7+ syntax (``&&`` ``||`` ``??``
        # ``?:`` ``?.`` ``?[``) to PS5.1 on the RAW command *before* it is
        # embedded as a single-quoted ``Invoke-Expression`` literal below.
        # ``pwsh_transform`` builds a region mask that deliberately skips
        # single-quoted string contents, so transforming the *assembled*
        # wrapper (the old call site in ``_run_powershell``) never reached
        # the user's command — the compatibility bridge was a silent no-op
        # on the real exec path, and ``Invoke-Expression`` then re-parsed
        # the un-leveled command under 5.1 and raised a ParserError on ``&&``
        # etc.
        #
        # When running PowerShell 7 (``pwsh``) natively, skip the transform
        # entirely — PS7 supports all modern operators natively.
        # [CN-fork] bash_fix/pwsh_fix port: PowerShell-aware quoting repair runs
        # FIRST, before any PS7→5.1 transform — validate the command under real
        # PowerShell quoting rules and fix what is safely fixable: an unclosed
        # string/here-string/comment gets its missing closing token appended,
        # and a trailing line comment / ``--%`` / dangling continuation gets a
        # newline so the try/catch wrapper below is not swallowed.  Unrepairable
        # commands run as-is inside the wrapper's error guard, with a warning
        # surfaced to the LLM.
        pwsh_warnings: list[str] = []
        if command.strip():
            fix = fix_pwsh_command(command)
            if fix is None:
                pwsh_warnings.append(
                    "PowerShell command could not be validated or auto-repaired; "
                    "running it as-is inside the error-guarding try/catch wrapper."
                )
            elif fix.changed:
                command = fix.command
                pwsh_warnings.append(fix.warning)
        # [CN-fork] P-037: down-level PS7+ syntax (``&&`` ``||`` ``??`` ``?:``
        # ``?.`` ``?[``) to PS5.1 on the (repaired) RAW command *before* it is
        # embedded as a single-quoted ``Invoke-Expression`` literal below.
        # ``pwsh_transform`` builds a region mask that deliberately skips
        # single-quoted string contents, so transforming the *assembled*
        # wrapper (the old call site in ``_run_powershell``) never reached
        # the user's command.  When running PowerShell 7 (``pwsh``) natively,
        # skip the transform entirely — PS7 supports all modern operators
        # natively.
        if self._shell_type != "pwsh":
            command, transform_warnings = pwsh_transform(command)
            pwsh_warnings.extend(transform_warnings)
        self._pwsh_warnings = pwsh_warnings

        # Escape single quotes for PowerShell: double them
        escaped = command.replace("'", "''")
        quoted_cwd = cwd.replace("'", "''")
        quoted_cwd_file = self._cwd_file.replace("'", "''")

        marker = self._cwd_marker

        # Build a PowerShell script.  We use ``Invoke-Expression`` to run
        # the user's command (similar to bash ``eval``).  ``$LASTEXITCODE``
        # captures the exit code of the last external command.
        parts = [
            # Force UTF-8 output encoding for stdout/stderr.
            "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8",
            "$OutputEncoding=[System.Text.Encoding]::UTF8",
            # Set native command argument passing to Windows (backward-compat)
            # mode.  PS 7.3+ defaults to 'Standard' which can break legacy tools
            # that rely on the old argument passing behaviour.
            "if (Get-Command -Name pwsh -ErrorAction SilentlyContinue) { $PSNativeCommandArgumentPassing = 'Windows' }",
            "$ErrorActionPreference = 'Continue'",
            f"Set-Location -LiteralPath '{quoted_cwd}' -ErrorAction SilentlyContinue",
            f"if ($?) {{ Set-Location -LiteralPath '{quoted_cwd}' }} else {{ exit 126 }}",
            # Run the command and force PowerShell's formatting pipeline to
            # flush before the wrapper exits.  Without ``Out-String``,
            # object-producing commands such as ``pwd`` / ``Get-Location`` or
            # mixed statements like ``Get-Location; Test-Path ...`` can return
            # an empty stdout when followed by ``exit`` in non-interactive
            # PowerShell hosts.  Use a wide string formatter so long paths are
            # not ellipsized by the default table formatter.  ``-Stream``
            # emits each object's rendered text as it arrives instead of
            # accumulating everything until the pipeline completes — without
            # it, output produced before a timeout kill is lost entirely.
            # ``try/catch`` protects the session from malformed user commands
            # (unbalanced quotes, parse errors) so the CWD marker and exit code
            # are always emitted.  Parity with ``PowerShellSession.run_script``.
            f"try {{ Invoke-Expression '{escaped}' | Out-String -Width 4096 -Stream | Write-Output }} catch {{ $_ | Out-String -Width 4096 -Stream | Write-Output }}",
            # ``$Error.Count`` catches non-terminating errors that don't set
            # ``$LASTEXITCODE`` (e.g. ``Get-ChildItem`` on a missing path).
            "$hermes_ec = $LASTEXITCODE; if ($null -eq $hermes_ec -and $Error.Count -gt 0) { $hermes_ec = 1 }",
            # Write CWD to temp file
            f"(Get-Location).Path | Out-File -Encoding utf8 -FilePath '{quoted_cwd_file}'",
            # Emit CWD marker
            "$cwd = (Get-Location).Path",
            "Write-Output ''",
            f"Write-Output ('{marker}' + $cwd + '{marker}')",
            # Exit with captured code
            "exit $hermes_ec",
        ]

        return "\n".join(parts)

    def _wrap_command_powershell_session(self, command: str, cwd: str) -> str:
        """Build the per-command body for the reused PowerShell session.

        Same shape as :meth:`_wrap_command_powershell` (down-level PS7 syntax on
        the RAW command, cd, ``Invoke-Expression | Out-String``, persist cwd to
        the marker file) but WITHOUT the UTF-8 preamble (the session sets it once
        at start), WITHOUT a trailing ``exit`` (that would end the session), and
        WITHOUT the stdout cwd marker (the file is authoritative for the local
        backend's :meth:`_update_cwd`).
        """
        # Same PowerShell-aware quoting repair as _wrap_command_powershell, and
        # like there it runs FIRST, before any PS7→5.1 transform: fix unclosed
        # strings/here-strings/comments and protect the session body from
        # trailing comments / ``--%`` / dangling continuations.
        pwsh_warnings: list[str] = []
        if command.strip():
            fix = fix_pwsh_command(command)
            if fix is None:
                pwsh_warnings.append(
                    "PowerShell command could not be validated or auto-repaired; "
                    "running it as-is inside the error-guarding try/catch wrapper."
                )
            elif fix.changed:
                command = fix.command
                pwsh_warnings.append(fix.warning)
        if self._shell_type != "pwsh":
            command, transform_warnings = pwsh_transform(command)
            pwsh_warnings.extend(transform_warnings)
        self._pwsh_warnings = pwsh_warnings

        escaped = command.replace("'", "''")
        quoted_cwd = cwd.replace("'", "''")
        quoted_cwd_file = self._cwd_file.replace("'", "''")
        parts = [
            # Set native command argument passing to Windows (backward-compat) mode.
            # PS 7.3+ defaults to 'Standard' which can break legacy tools.
            "if (Get-Command -Name pwsh -ErrorAction SilentlyContinue) { $PSNativeCommandArgumentPassing = 'Windows' }",
            "$ErrorActionPreference = 'Continue'",
            f"Set-Location -LiteralPath '{quoted_cwd}' -ErrorAction SilentlyContinue",
            # ``-Stream``: keep partial output alive when the command is
            # killed mid-pipeline (timeout / interrupt).
            f"Invoke-Expression '{escaped}' | Out-String -Width 4096 -Stream | Write-Output",
            f"(Get-Location).Path | Out-File -Encoding utf8 -FilePath '{quoted_cwd_file}'",
        ]
        return "\n".join(parts)

    def _session_env_refresh_prefix(self, run_env: dict) -> str:
        """Return PS lines that re-assert PATH/PATHEXT into the live session.

        The session process captured its env at spawn, so tools installed since
        (P-020's whole point) wouldn't be on its PATH.  Re-assigning ``$env:PATH``
        / ``$env:PATHEXT`` from the freshly-refreshed ``run_env`` on each command
        keeps them discoverable without restarting the interpreter.
        """
        path_key = _path_env_key(run_env)
        lines: list[str] = []
        if path_key and run_env.get(path_key):
            lines.append(f"$env:PATH = '{run_env[path_key].replace(chr(39), chr(39) * 2)}'")
        pathext = run_env.get("PATHEXT")
        if pathext:
            lines.append(f"$env:PATHEXT = '{pathext.replace(chr(39), chr(39) * 2)}'")
        return ("\n".join(lines) + "\n") if lines else ""

    def _get_pwsh_session(self):
        """Return a live :class:`PowerShellSession`, creating/reviving as needed."""
        with self._pwsh_session_lock:
            if self._pwsh_session is not None and self._pwsh_session.is_alive():
                return self._pwsh_session
            if self._pwsh_session is not None:
                try:
                    self._pwsh_session.close()
                except Exception:
                    pass
            from tools.environments.powershell_session import PowerShellSession

            refresh_env_from_registry()
            run_env = _make_run_env(self.env)
            session = PowerShellSession(
                shell_path=self._shell_path,
                cwd=_resolve_safe_cwd(self.cwd),
                env=run_env,
                default_timeout=float(self.timeout),
            )
            session.start()
            self._pwsh_session = session
            return session

    def _execute_via_session(
        self,
        command: str,
        cwd: str,
        *,
        timeout: int | None,
        rewrite_compound_background: bool,
    ) -> dict:
        """Run *command* through the reused PowerShell session.

        Mirrors the prep in :meth:`BaseEnvironment.execute` (sudo transform,
        compound-background rewrite, cwd/timeout resolution, missing-cwd
        recovery, pwsh-warning + cwd propagation) but pipes the wrapped body to
        the warm interpreter instead of spawning a new process.  Raises
        :class:`_SessionFallback` when the command can't be served this way (it
        needs stdin, or the session vanished before output) so the caller drops
        to the proven spawn path.
        """
        self._before_execute()

        exec_command, sudo_stdin = self._prepare_command(command)
        if sudo_stdin is not None:
            # A sudo password needs stdin piping the shared session can't do.
            raise _SessionFallback("command needs stdin")
            if rewrite_compound_background:
                # [CN-fork] P-042 — the shell rewriter moved into upstream's
                # ``tools/terminal_tool_sudo`` sibling (same import as the spawn path in
                # ``tools/environments/base.py``); keep resolving it per call so a test
                # patch on the defining module wins.
                from tools.terminal_tool_sudo import _rewrite_compound_background

                exec_command = _rewrite_compound_background(exec_command)
        effective_timeout = timeout or self.timeout
        effective_cwd = cwd or self.cwd

        # Recover a cwd deleted out from under us, same as the spawn path.
        safe_cwd = _resolve_safe_cwd(effective_cwd)
        if safe_cwd != effective_cwd:
            normalized = os.path.normpath(
                _msys_to_windows_path(effective_cwd) if _IS_WINDOWS else effective_cwd
            )
            if safe_cwd != normalized:
                logger.warning(
                    "LocalEnvironment cwd %r is missing on disk; falling back to "
                    "%r so terminal commands keep working (session).",
                    effective_cwd,
                    safe_cwd,
                )
            self.cwd = safe_cwd
            effective_cwd = safe_cwd

        body = self._wrap_command_powershell_session(exec_command, effective_cwd)

        session = self._get_pwsh_session()

        refresh_env_from_registry()
        run_env = _make_run_env(self.env)
        script = self._session_env_refresh_prefix(run_env) + body

        _now = time.monotonic()
        from tools.environments.base import touch_activity_if_due

        _activity_state = {"last_touch": _now, "start": _now}

        def _activity() -> None:
            touch_activity_if_due(_activity_state, "terminal command running")

        res = session.run_script(
            script, timeout=effective_timeout, activity_cb=_activity
        )

        if res.session_died and not res.output.strip():
            # Interpreter died before producing anything; retry via spawn.
            raise _SessionFallback("session died before output")

        output = res.output
        if res.timed_out:
            suffix = f"\n[Command timed out after {effective_timeout}s]"
            output = (output + suffix) if output else suffix.lstrip()
        elif res.interrupted:
            output = output + "\n[Command interrupted]"

        result = {"output": output, "returncode": res.returncode}
        self._update_cwd(result)

        pwsh_warnings = getattr(self, "_pwsh_warnings", None)
        if pwsh_warnings:
            result["pwsh_warnings"] = pwsh_warnings
            self._pwsh_warnings = None
        return result

    def _execute_via_cmd(self, command: str, cwd: str, *, timeout: int | None) -> dict:
        """Run an eligible simple builtin through ``cmd.exe /c`` (P-042 #3).

        Only reached for :func:`_cmd_fast_path_eligible` commands (bare builtin,
        no metacharacters, no cwd/env mutation) when the opt-in flag is on and
        the persistent session isn't handling the call.  Because eligible
        commands can't change cwd, the tracked ``self.cwd`` is authoritative and
        is used as the child's working directory — no marker round-trip needed.
        Raises :class:`_SessionFallback` for anything it shouldn't serve so the
        caller drops to the proven spawn path.
        """
        self._before_execute()
        exec_command, sudo_stdin = self._prepare_command(command)
        if sudo_stdin is not None:
            raise _SessionFallback("command needs stdin")
        # Re-check after _prepare_command: a sudo/transform rewrite could have
        # introduced syntax that is no longer cmd-safe.
        if not _cmd_fast_path_eligible(exec_command):
            raise _SessionFallback("command not cmd-eligible after prepare")

        effective_timeout = timeout or self.timeout
        effective_cwd = _resolve_safe_cwd(cwd or self.cwd)
        self.cwd = effective_cwd

        refresh_env_from_registry()
        run_env = _make_run_env(self.env)
        popen_kwargs = {"creationflags": getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "CREATE_NO_WINDOW", 0)} if _IS_WINDOWS else {}
        # Eligibility guarantees no shell metacharacters, so the command is safe
        # to hand to cmd.exe as a single verbatim command line (no injection
        # surface) — building it ourselves avoids subprocess.list2cmdline
        # re-quoting a spaced builtin into an unrunnable "dir foo" program name.
        cmdline = f"cmd.exe /d /c {exec_command}"
        try:
            proc = subprocess.run(
                cmdline,
                cwd=effective_cwd if os.path.isdir(effective_cwd) else None,
                env=run_env,
                capture_output=True,
                timeout=effective_timeout,
                **popen_kwargs,
            )
        except subprocess.TimeoutExpired as exc:
            partial = _decode_cmd_output(exc.output or b"") + _decode_cmd_output(
                exc.stderr or b""
            )
            suffix = f"\n[Command timed out after {effective_timeout}s]"
            return {
                "output": (partial + suffix) if partial else suffix.lstrip(),
                "returncode": 124,
            }
        except (OSError, ValueError) as exc:
            raise _SessionFallback(f"cmd spawn failed: {exc}")

        output = _decode_cmd_output(proc.stdout) + _decode_cmd_output(proc.stderr)
        # Persist the (unchanged) cwd to the marker file so a later spawn/session
        # call's _update_cwd reads a consistent value.
        try:
            Path(self._cwd_file).parent.mkdir(parents=True, exist_ok=True)
            Path(self._cwd_file).write_text(effective_cwd, encoding="utf-8")
        except OSError:
            pass
        return {"output": output, "returncode": proc.returncode}

    def execute(
        self,
        command: str,
        cwd: str = "",
        *,
        timeout: int | None = None,
        stdin_data: str | None = None,
        rewrite_compound_background: bool = True,
        bounded_capture: bool = False,
        output_callback=None,
        wait_for_pattern: str | None = None,
        promote_callback=None,
        yield_handler=None,
    ) -> dict:
        """Execute a command via the fastest eligible path (P-042).

        Order: the persistent PowerShell session (opt-in, warm ~1-5ms) → the
        opt-in cmd.exe fast path for trivial builtins (spawn ~10-20ms) → the
        unchanged spawn-per-call path in :meth:`BaseEnvironment.execute`.  Both
        fast paths are OFF by default and fall through to spawn on anything they
        can't serve (stdin present, non-PowerShell shell, ineligible command).

        ``yield_handler`` (upstream's mid-command handoff: a user message arriving
        while a foreground command runs hands the live process to the handler
        instead of killing it) is served by the spawn path only, so either fast
        path declines when one is supplied — the fork's overrides must accept every
        kwarg ``terminal_tool`` passes, or the whole local backend raises TypeError.
        """
        if (
            self._pwsh_session_reuse
            and yield_handler is None
            and stdin_data is None
            and not bounded_capture
            and self._shell_type in ("powershell", "pwsh")
        ):
            try:
                return self._execute_via_session(
                    command,
                    cwd,
                    timeout=timeout,
                    rewrite_compound_background=rewrite_compound_background,
                )
            except _SessionFallback as exc:
                logger.info(
                    "PowerShell session fast path declined (%s); using spawn.", exc
                )
            except Exception as exc:  # noqa: BLE001 - never let the fast path
                # wedge the tool; the spawn path is the safety net.
                logger.warning(
                    "PowerShell session execute failed (%s); using spawn.",
                    exc,
                    exc_info=True,
                )
        # [CN-fork P-042 #3] cmd.exe fast path (opt-in, spawn-model) — considered
        # only when the session path didn't serve the call.  Narrow eligibility
        # keeps behaviour identical to PowerShell; anything else falls through.
        if (
            self._cmd_fast_path
            and yield_handler is None
            and stdin_data is None
            and not bounded_capture
            and self._shell_type in ("powershell", "pwsh")
            and _cmd_fast_path_eligible(command)
        ):
            try:
                return self._execute_via_cmd(command, cwd, timeout=timeout)
            except _SessionFallback as exc:
                logger.info("cmd fast path declined (%s); using spawn.", exc)
            except Exception as exc:  # noqa: BLE001 - safety net is the spawn path
                logger.warning(
                    "cmd fast path failed (%s); using spawn.", exc, exc_info=True
                )
        return super().execute(
            command,
            cwd,
            timeout=timeout,
            stdin_data=stdin_data,
            rewrite_compound_background=rewrite_compound_background,
            bounded_capture=bounded_capture,
            output_callback=output_callback,
            wait_for_pattern=wait_for_pattern,
            promote_callback=promote_callback,
            yield_handler=yield_handler,
        )

    def init_session(self):
        """Capture shell environment into a snapshot file.

        For **powershell**: skip the snapshot dance — Windows env vars
        persist through ``os.environ`` inheritance naturally.  Just write
        the initial CWD and mark snapshot as not-ready (commands run fresh
        with ``-NoProfile`` for speed).

        For **bash**: unchanged — captures env vars, functions, aliases
        into a snapshot file that subsequent commands source.
        """
        if getattr(self, "_shell_type", "bash") in ("powershell", "pwsh"):
            # Simple CWD marker write — no snapshot needed for powershell.
            self._snapshot_ready = False
            try:
                cwd_path = self.cwd
                if _IS_WINDOWS:
                    cwd_path = os.path.normpath(cwd_path)
                Path(self._cwd_file).parent.mkdir(parents=True, exist_ok=True)
                Path(self._cwd_file).write_text(cwd_path, encoding="utf-8")
            except Exception as exc:
                logger.warning(
                    "init_session (%s) failed to write CWD file: %s", self._shell_type, exc
                )
            logger.info(
                "%s session ready (session=%s, cwd=%s)", self._shell_type,
                self._session_id,
                self.cwd,
            )
            return

        # --- bash path ---
        if _IS_WINDOWS:
            native_snapshot_path = self._snapshot_path
            native_cwd_file = self._cwd_file
            self._snapshot_path = _windows_to_msys_path(native_snapshot_path)
            self._cwd_file = _windows_to_msys_path(native_cwd_file)
            try:
                return super().init_session()
            finally:
                self._snapshot_path = native_snapshot_path
                self._cwd_file = native_cwd_file
        return super().init_session()

    @staticmethod
    def _quote_cwd_for_cd(cwd: str) -> str:
        """Use native paths for Python, but Git Bash-friendly paths for cd."""
        return BaseEnvironment._quote_cwd_for_cd(_windows_to_msys_path(cwd))

    def _quote_shell_path(self, path: str) -> str:
        """Rewrite native/mixed Windows paths before quoting for Git Bash."""
        return _quote_bash_path(path)

    def _recover_cwd(self) -> None:
        """Swap ``self.cwd`` for a usable directory if it vanished or is inaccessible
        (e.g. a command ``rm -rf``'d its own cwd) — otherwise Popen raises before bash
        starts and every subsequent call fails. A benign MSYS→Windows normalization
        is not warned about."""
        # Recover when the cwd has been deleted out from under us — usually by a previous tool call that ran
        # ``rm -rf`` on its own working dir (issue #17558). On Windows, ``_resolve_safe_cwd`` also
        # normalises Git Bash-style POSIX paths (``/c/Users/...``) to native form so a perfectly valid ``pwd
        # -P`` result from bash isn't mistakenly treated as "missing" and spammed as a warning on every
        # command.
        safe_cwd = _resolve_safe_cwd(self.cwd)
        if safe_cwd == self.cwd:
            return
        if safe_cwd != _msys_to_windows_path(self.cwd):
            logger.warning(
                "LocalEnvironment cwd %r is missing on disk; "
                "falling back to %r so terminal commands keep working.",
                self.cwd, safe_cwd)
        self.cwd = safe_cwd

    def _run_bash(self, cmd_string: str, *, login: bool = False, timeout: int = 120,
                  stdin_data: str | None = None) -> subprocess.Popen:
        """Spawn a shell process to run *cmd_string*.

        Dispatches to ``_run_powershell()`` when the active shell is PowerShell,
        otherwise spawns the resolved bash. On Windows that is the Git Bash
        discovered at environment init (``self._shell_path``), so ``init_session``
        and ``execute`` agree on one binary; ``_find_bash_posix()`` stays the
        POSIX-only fallback.
        """
        if getattr(self, "_shell_type", "bash") in ("powershell", "pwsh"):
            return self._run_powershell(
                cmd_string, login=login, timeout=timeout, stdin_data=stdin_data
            )
        bash = self._shell_path or _find_bash_posix()
        # Login invocations (init_session's env snapshot) source the user's rc /
        # custom init files so nvm/asdf/pyenv land on PATH in the snapshot.
        if login:
            cmd_string = _prepend_shell_init(cmd_string, _resolve_shell_init_files())
        # [CN-fork P-052] Windows path/backslash preprocessing for Git Bash.
        safe_cmd = _prepare_bash_cmd(cmd_string)
        args = [bash, *(["-l"] if login else []), "-c", safe_cmd]
        self._recover_cwd()
        proc = subprocess.Popen(
            args, text=True, env=_make_run_env(self.env), encoding="utf-8", errors="replace",
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=subprocess.PIPE if stdin_data is not None else subprocess.DEVNULL,
            start_new_session=True, cwd=self.cwd,
            **({"creationflags": windows_hide_flags()} if _IS_WINDOWS else {}))
        if not _IS_WINDOWS:
            with contextlib.suppress(ProcessLookupError):
                proc._hermes_pgid = os.getpgid(proc.pid)
        if stdin_data is not None:
            _pipe_stdin(proc, stdin_data)
        return proc

    # --- [CN-fork P-052] bash_fix + MSYSTEM neutralization wiring. ---

    def _wrap_command(self, command: str, cwd: str) -> str:
        """Build the shell script wrapper for command execution.

        Dispatches to ``_wrap_command_powershell()`` when the active shell
        is powershell, otherwise uses the base bash wrapping.  On Windows Git
        Bash the raw command is first covered by :func:`fix_bash_command`
        (mirroring kimix ``bash_tool._prepare_command``): verified native
        POSIX command words (``open``, ``pbcopy``, ``rev``, ``gtimeout``, …)
        get bundled fallback definitions prepended, Windows backslash and Git
        Bash virtual paths (``/tmp/x``, ``/c/x``) are normalized, redundant
        ``bash``/``sh`` invocations are unwrapped, and ``> nul`` becomes
        ``> /dev/null``, so the wrapper embeds a Git Bash-safe command.
        Command names with no faithful Git Bash equivalent are left untouched
        and reported through ``bash_fix_warnings`` with the reason.
        The fix is gated explicitly on ``sys.platform == "win32"`` — it is a
        Windows-only rewrite that turns native POSIX commands into Git Bash
        compatible form, and on non-Windows hosts ``_wrap_command`` skips the
        fixer entirely (byte-for-byte no-op; the guard mirrors the one inside
        :func:`fix_bash_command`).  ``init_session`` never reaches this
        method (it probes ``_run_bash`` directly), so the fallback functions
        cannot leak into the env snapshot.
        """
        if self._shell_type in ("powershell", "pwsh"):
            return self._wrap_command_powershell(command, cwd)
        # [CN-fork P-052] bash_fix is win32-only by design: it rewrites native POSIX
        # command words (``open``, ``rev``, ``wget``, …) to Git Bash compatible
        # fallbacks.  Guarding the call here — not just inside
        # ``fix_bash_command`` — keeps the win32-only contract visible at the
        # call site and avoids any fixer overhead on POSIX hosts.
        if sys.platform == "win32":
            bash_fix_result = fix_bash_command(command)
            command = bash_fix_result.command
            # ``unsupported`` names are left byte-for-byte in the command (the
            # fixer only records them), so they are not part of ``changed``:
            # the wrapper still surfaces the reason + native alternative here
            # instead of letting Bash fail with a bare "command not found".
            if bash_fix_result.changed or bash_fix_result.unsupported:
                self._bash_fix_warnings = bash_fix_result.warning
            # [CN-fork P-052] MSYSTEM neutralization (ported from kimix
            # ``bash_tool._with_msystem_neutralized``): Git Bash's
            # ``bin/bash.exe`` launcher unconditionally injects
            # ``MSYSTEM=MINGW64``, and the MSYS2 runtime re-injects it into
            # children when absent, so build tools (xmake, meson,
            # cross-toolchains) spawned from a one-shot ``bash -c`` misdetect
            # the platform as ``MINGW64``/MSYS2 instead of ``windows``/MSVC.
            # Exporting an EMPTY ``MSYSTEM`` at the start of the eval'd
            # command fixes that while the launcher's PATH setup stays intact.
            # The ``<root>/cmd/git.exe`` marker guard (``_is_git_bash_install``)
            # keeps real MSYS2 installs untouched.  The prefix runs AFTER the
            # snapshot source inside the eval, so children still see the empty
            # value even if the snapshot exports MSYSTEM=MINGW64.
            command = _with_msystem_neutralized(command, self._shell_path)
        return super()._wrap_command(command, cwd)

    def _kill_process(self, proc):
        """Kill the entire process group (all children)."""

        def _group_alive(pgid: int) -> bool:
            try:
                # POSIX-only: _IS_WINDOWS is handled before this helper is used.
                os.killpg(pgid, 0)  # windows-footgun: ok — POSIX process-group alive probe
                return True
            except ProcessLookupError:
                return False
            except PermissionError:
                # The group exists, even if this process cannot signal it.
                return True

        def _wait_for_group_exit(pgid: int, timeout: float) -> bool:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                # Reap the wrapper promptly. A dead but unreaped group leader
                # still makes killpg(pgid, 0) report the group as alive.
                try:
                    proc.poll()
                except Exception:
                    pass
                if not _group_alive(pgid):
                    return True
                time.sleep(0.05)
            try:
                proc.poll()
            except Exception:
                pass
            return not _group_alive(pgid)

        try:
            if _IS_WINDOWS:
                try:
                    from gateway.status import terminate_pid

                    terminate_pid(proc.pid, force=True)
                except Exception:
                    proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except (subprocess.TimeoutExpired, OSError):
                    pass
            else:
                try:
                    pgid = os.getpgid(proc.pid)
                except ProcessLookupError:
                    pgid = getattr(proc, "_hermes_pgid", None)
                    if pgid is None:
                        raise

                try:
                    os.killpg(pgid, signal.SIGTERM)  # windows-footgun: ok — POSIX process-group SIGTERM (guarded by _IS_WINDOWS above)
                except ProcessLookupError:
                    return

                # Wait on the process group, not just the shell wrapper. Under
                # load the wrapper can exit before grandchildren do; returning
                # at that point leaves orphaned process-group members behind.
                if _wait_for_group_exit(pgid, 1.0):
                    return

                try:
                    # POSIX-only: _IS_WINDOWS is handled by the outer branch.
                    os.killpg(pgid, signal.SIGKILL)  # windows-footgun: ok — POSIX process-group SIGKILL
                except ProcessLookupError:
                    return
                _wait_for_group_exit(pgid, 2.0)
                try:
                    proc.wait(timeout=0.2)
                except (subprocess.TimeoutExpired, OSError):
                    pass
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except Exception:
                pass

    def _update_cwd(self, result: dict):
        """Update cwd from the stdout marker emitted by the wrapped command.

        Skip the assignment when the path no longer exists as a directory —
        a stale cwd can leave a bad value in the marker file, and propagating
        it would re-wedge the next ``Popen``.  The ``_run_bash`` recovery
        path will resolve a safe fallback if needed.

        On Windows with the bash shell type, the CWD value may be in
        MSYS form (``/c/Users/x``). Translate it to native Windows form
        before validating with ``os.path.isdir``.
        """
        try:
            with open(self._cwd_file, encoding="utf-8", errors="replace") as f:
                cwd_path = f.read().strip()
            if _IS_WINDOWS and self._shell_type not in ("powershell", "pwsh"):
                cwd_path = _msys_to_windows_path(cwd_path)
            if cwd_path and os.path.isdir(cwd_path):
                self.cwd = cwd_path
        except (OSError, FileNotFoundError):
            pass

        # Still strip the marker from output so it's not visible
        self._extract_cwd_from_output(result)

    def _extract_cwd_from_output(self, result: dict):
        """Base semantics plus: Git Bash ``pwd -P`` emits MSYS form on Windows —
        normalize to native and require the dir to exist, else ``_run_bash`` would
        warn every command. A stale path rolls back to the previous cwd, which this
        command did not observe, so ``cwd_observed`` is dropped. For the PowerShell
        shell the marker is already native, so no translation is applied.
        """
        prev_cwd = self.cwd
        super()._extract_cwd_from_output(result)
        if self.cwd != prev_cwd:
            normalized = (
                _msys_to_windows_path(self.cwd)
                if _IS_WINDOWS and getattr(self, "_shell_type", "bash") not in ("powershell", "pwsh")
                else self.cwd
            )
            if normalized and os.path.isdir(normalized):
                self.cwd = normalized
                result["cwd"] = normalized
            else:
                self.cwd = prev_cwd
                result.pop("cwd_observed", None)
                result.pop("cwd", None)

    def cleanup(self):
        """Clean up temp files, including orphaned atomic-write snapshots
        (``snap.tmp.<bashpid>``) a failed/interrupted mv could leave behind
        (see #38249), and tear down the persistent PowerShell session if any."""
        session = getattr(self, "_pwsh_session", None)
        if session is not None:
            try:
                session.close()
            except Exception:
                pass
            self._pwsh_session = None
        import glob
        try:
            stale = glob.glob(f"{self._snapshot_path}.tmp.*")
        except Exception:
            stale = []
        for f in (self._snapshot_path, self._cwd_file, *stale):
            with contextlib.suppress(OSError):
                os.unlink(f)
