#!/usr/bin/env python3
"""Ahead-of-time bytecode compilation for the Hermes source tree.

Running Hermes *from source* (the CN fork's default on Windows) compiles each
``.py`` to ``__pycache__/*.pyc`` lazily on first import.  On Windows every one
of those first-touch compiles pays ``_io.open_code`` + ``builtins.compile`` and
a Windows Defender real-time scan of the freshly written ``.pyc`` — a
measurable slice of cold agent-init (flame-graph hotspots ``_io.open_code``
~4.39% and ``builtins.compile`` ~10.82%).  Pre-compiling the tree once
(post-install / post-update / in CI) front-loads that work so the first agent
start reads ready ``.pyc`` files instead of compiling them on the hot path.

``pip install`` already byte-compiles installed packages; this script is for
the run-from-source layout where nothing has compiled the tree yet.

Usage::

    python scripts/precompile.py                 # compile the key source trees
    python scripts/precompile.py agent tools     # only these subtrees
    python scripts/precompile.py --all --quiet   # whole repo, minimal output
"""

from __future__ import annotations

import argparse
import compileall
import hashlib
import os
from agent.re_compat import re
import sys
import threading
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# The Python that actually ships in the running agent.  Kept explicit (rather
# than "the whole repo") so the default pass does not walk ``.venv`` /
# ``node_modules`` / ``website`` and so it never compiles test-only trees.
DEFAULT_TARGETS: tuple[str, ...] = (
    "agent",
    "tools",
    "hermes_cli",
    "gateway",
    "cron",
    "plugins",
    "providers",
    "acp_adapter",
    "tui_gateway",
)

# Top-level single-file modules (compiled individually — they live at the repo
# root alongside directories we do NOT want to recurse into).
DEFAULT_TOP_LEVEL_MODULES: tuple[str, ...] = (
    "run_agent.py",
    "model_tools.py",
    "toolsets.py",
    "toolset_distributions.py",
    "batch_runner.py",
    "trajectory_compressor.py",
    "cli.py",
    "hermes_bootstrap.py",
    "hermes_constants.py",
    "hermes_state.py",
    "hermes_time.py",
    "hermes_logging.py",
    "utils.py",
    "platform_utils.py",
    "import_accelerator.py",
    "mcp_serve.py",
    "agent.py",
)

# Directory names that are never part of the running agent; skipped even under
# an explicit ``--all`` pass so the walk stays quick.
_SKIP_DIRS: tuple[str, ...] = (
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "reports",
    "website",
    "ui-tui",
    ".plans",
    ".kimix_cache",
    "assets",
)


def _skip_regex() -> "re.Pattern[str]":
    """Regex matching any path segment we want ``compileall`` to skip."""
    joined = "|".join(re.escape(d) for d in _SKIP_DIRS)
    return re.compile(rf"(^|[\\/])(?:{joined})([\\/]|$)")


def _pyproject_targets() -> "list[str] | None":
    """Optional target override from ``[tool.hermes.precompile] targets`` in
    pyproject.toml.  Returns a list of dir/file strings, or None when the table
    is absent/malformed (fall back to the hardcoded defaults).  Never raises.
    """
    try:
        import tomllib
    except Exception:
        return None
    try:
        data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    targets = (
        data.get("tool", {}).get("hermes", {}).get("precompile", {}).get("targets")
    )
    if isinstance(targets, list) and targets and all(isinstance(t, str) for t in targets):
        return targets
    return None


def _default_target_list() -> "list[str]":
    """The effective default targets: the pyproject override if present, else
    the built-in dir targets + top-level single-file modules."""
    override = _pyproject_targets()
    if override is not None:
        return list(override)
    return [*DEFAULT_TARGETS, *DEFAULT_TOP_LEVEL_MODULES]


def precompile_all(paths=None, *, quiet: bool = True, workers: int = 0) -> bool:
    """Byte-compile *paths* into ``__pycache__``; return ``True`` if all clean.

    *paths* defaults to the :func:`_default_target_list` selection (the
    ``[tool.hermes.precompile]`` override, or :data:`DEFAULT_TARGETS` plus the
    top-level modules).  A syntax error in a single module is reported by
    ``compileall`` and does NOT raise — a bad file must not abort the whole
    warm-up — but it makes the return value ``False`` so callers/CI can surface
    it.
    """
    rx = _skip_regex()
    quiet_level = 1 if quiet else 0
    ok = True

    if paths is None:
        paths = _default_target_list()
    dir_targets = []
    file_targets = []
    for raw in paths:
        p = Path(raw)
        p = p if p.is_absolute() else (REPO_ROOT / p)
        (file_targets if p.is_file() else dir_targets).append(p)

    for target in dir_targets:
        if not target.exists():
            continue
        result = compileall.compile_dir(
            str(target),
            quiet=quiet_level,
            rx=rx,
            workers=workers,
            optimize=0,
        )
        ok = ok and bool(result)

    for target in file_targets:
        if not target.exists():
            continue
        result = compileall.compile_file(str(target), quiet=quiet_level, optimize=0)
        ok = ok and bool(result)

    return ok


def _iter_source_files(paths) -> "list[Path]":
    """All ``.py`` files under *paths* (dirs walked, skip-dirs pruned)."""
    rx = _skip_regex()
    out: "list[Path]" = []
    for raw in paths:
        p = Path(raw)
        p = p if p.is_absolute() else (REPO_ROOT / p)
        if p.is_file():
            if p.suffix == ".py":
                out.append(p)
        elif p.is_dir():
            for root, dirs, files in os.walk(p):
                dirs[:] = [d for d in dirs if not rx.search(os.path.join(root, d, ""))]
                out.extend(Path(root) / f for f in files if f.endswith(".py"))
    return out


def source_fingerprint(paths=None) -> str:
    """Cheap content fingerprint of the source tree + interpreter identity.

    Keyed on the interpreter (impl + ``X.Y.Z``) and every source file's
    ``(path, mtime_ns, size)`` — stat-only, so it is far cheaper than a compile
    pass.  Any edit/add/remove of a ``.py`` file, or an interpreter change (whose
    ``.pyc`` magic differs), changes the digest, so a stamp keyed on it can never
    serve a stale "already warm" verdict.
    """
    if paths is None:
        paths = _default_target_list()
    h = hashlib.sha1()
    h.update(f"{sys.implementation.name}:{sys.version_info[:3]}:".encode("utf-8"))
    parts = []
    for f in _iter_source_files(paths):
        try:
            st = f.stat()
        except OSError:
            continue
        parts.append(f"{f}:{st.st_mtime_ns}:{st.st_size}")
    for part in sorted(parts):
        h.update(part.encode("utf-8"))
        h.update(b"|")
    return h.hexdigest()


def _default_stamp_path() -> "Path | None":
    """Profile-aware stamp path under ``<HERMES_HOME>/cache`` (or None)."""
    try:
        from hermes_constants import get_hermes_home

        cache_dir = Path(get_hermes_home()) / "cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "precompile.stamp"
    except Exception:
        return None


def precompile_if_needed(
    paths=None,
    *,
    stamp_path=None,
    quiet: bool = True,
    workers: int = 0,
    force: bool = False,
) -> str:
    """Compile the tree only when its fingerprint differs from the stamp.

    Returns one of ``"skipped"`` (fingerprint matched — already warm),
    ``"compiled"`` (a compile pass ran), or ``"unavailable"`` (a guarded failure
    — the caller can ignore it; imports still compile lazily as before).  Never
    raises: a warm-up helper must not break startup.

    The stamp records the :func:`source_fingerprint`, so it re-runs after any
    source or interpreter change and no-ops otherwise.  Individual per-file
    staleness is still handled by ``compileall``/import itself — this only avoids
    repeating the *full* walk once the tree is warm.
    """
    try:
        stamp = Path(stamp_path) if stamp_path is not None else _default_stamp_path()
        fingerprint = source_fingerprint(paths)
        if not force and stamp is not None and stamp.exists():
            try:
                if stamp.read_text(encoding="utf-8").strip() == fingerprint:
                    return "skipped"
            except OSError:
                pass
        precompile_all(paths, quiet=quiet, workers=workers)
        if stamp is not None:
            try:
                stamp.parent.mkdir(parents=True, exist_ok=True)
                tmp = stamp.with_name(f"{stamp.name}.{os.getpid()}.tmp")
                tmp.write_text(fingerprint, encoding="utf-8")
                os.replace(tmp, stamp)
            except OSError:
                pass
        return "compiled"
    except Exception:
        return "unavailable"


def precompile_in_background(paths=None, **kwargs) -> "threading.Thread":
    """Run :func:`precompile_if_needed` in a daemon thread; return the thread.

    Off the hot path by design: the first agent start after an update warms the
    ``.pyc`` cache while the user reads the banner, so the *next* start reads
    ready ``.pyc`` instead of paying ``builtins.compile`` + ``_io.open_code`` (+
    a Windows Defender scan of each freshly-written ``.pyc``) on the import
    cascade.  Daemon, so it never holds the process open.
    """
    thread = threading.Thread(
        target=lambda: precompile_if_needed(paths, **kwargs),
        name="hermes-precompile",
        daemon=True,
    )
    thread.start()
    return thread


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Pre-compile Hermes .pyc bytecode to warm cold imports."
    )
    parser.add_argument(
        "paths",
        nargs="*",
        help="Files/subtrees to compile (default: the core source trees).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Compile the entire repo root (still skips venv/node_modules/etc.).",
    )
    parser.add_argument("--quiet", action="store_true", help="Suppress per-file output.")
    parser.add_argument(
        "--workers",
        type=int,
        default=0,
        help="Parallel workers (0 = one per CPU; 1 = sequential).",
    )
    parser.add_argument(
        "--if-needed",
        action="store_true",
        help="Skip when the source fingerprint matches the on-disk stamp "
        "(idempotent warm-up; writes/updates the stamp after compiling).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="With --if-needed, recompile even when the stamp matches.",
    )
    args = parser.parse_args(argv)

    if args.all and not args.paths:
        targets = [str(REPO_ROOT)]
    else:
        targets = args.paths or None

    if args.if_needed:
        status = precompile_if_needed(
            targets, quiet=args.quiet, workers=args.workers, force=args.force
        )
        if not args.quiet:
            print(f"precompile: {status}")
        # "skipped"/"compiled" are both success; only a guarded failure is nonzero.
        return 0 if status in ("skipped", "compiled") else 1

    ok = precompile_all(targets, quiet=args.quiet, workers=args.workers)
    if not args.quiet:
        print("precompile: OK" if ok else "precompile: completed with errors")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
