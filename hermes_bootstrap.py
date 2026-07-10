"""Windows UTF-8 bootstrap for Hermes entry points.

Python on Windows has two long-standing text-encoding footguns:

1. ``sys.stdout`` / ``sys.stderr`` are bound to the console code page
   (``cp1252`` on US-locale installs), so ``print("café")`` crashes with
   ``UnicodeEncodeError: 'charmap' codec can't encode character``.

2. Child processes spawned via ``subprocess`` don't know to use UTF-8
   unless ``PYTHONUTF8`` and/or ``PYTHONIOENCODING`` are set in their
   environment — so any Python subprocess (the execute_code sandbox,
   delegation children, linter subprocesses, etc.) inherits the same
   cp1252 defaults and hits the same UnicodeEncodeError.

This module fixes both on Windows *only* — POSIX is untouched.  It
should be imported at the very top of every Hermes entry point
(``hermes``, ``hermes-agent``, ``hermes-acp``, ``python -m gateway.run``,
``batch_runner.py``, ``cron/scheduler.py``) before any other imports
that might do file I/O or print to stdout.

What this module does on Windows:

  - Sets ``os.environ["PYTHONUTF8"] = "1"`` (PEP 540 UTF-8 mode) so
    every child process we spawn uses UTF-8 for ``open()`` and stdio.
  - Sets ``os.environ["PYTHONIOENCODING"] = "utf-8"`` for belt-and-
    suspenders — some tools read this instead of / in addition to
    ``PYTHONUTF8``.
  - Sets the console code page to CP_UTF8 (65001) via
    ``SetConsoleCP`` / ``SetConsoleOutputCP`` so the console host and
    PowerShell subprocesses inherit UTF-8 by default.
  - Reconfigures ``sys.stdout`` / ``sys.stderr`` to UTF-8 in the current
    process, using the ``reconfigure()`` API (Python 3.7+).  This fixes
    ``print("café")`` in the parent without a re-exec.

What this module does NOT do:

  - It does not re-exec Python with ``-X utf8``, so ``open()`` calls in
    the *current* process still default to locale encoding.  Those need
    an explicit ``encoding="utf-8"`` at the call site (lint rule
    ``PLW1514`` / ``PYI058``).  Ruff is the right tool for that sweep.

  - All Windows behaviour can be disabled by setting the environment
    variable ``HERMES_DISABLE_WINDOWS_UTF8=1`` before import.

What this module does on POSIX:

  - Nothing.  POSIX systems are already UTF-8 by default in 99% of cases,
    and we don't want to touch ``LANG``/``LC_*`` behavior that users may
    have configured intentionally.  If someone hits a C/POSIX locale on
    Linux, they can export ``PYTHONUTF8=1`` themselves — we won't override.

Idempotent: safe to call multiple times.  ``_bootstrap_once`` guards
against double-reconfigure.
"""

from __future__ import annotations

import os
import sys

_IS_WINDOWS = sys.platform == "win32"
_bootstrap_applied = False


def apply_windows_utf8_bootstrap() -> bool:
    """Apply the Windows UTF-8 bootstrap if we're on Windows.

    Returns True if bootstrap was applied (i.e. we're on Windows and
    haven't already done this), False otherwise.  The return value is
    advisory — callers normally don't need it, but tests may want to
    assert the path was taken.

    Idempotent: subsequent calls after the first are a no-op.
    """
    global _bootstrap_applied

    if not _IS_WINDOWS:
        return False
    if _bootstrap_applied:
        return False

    # Honour the documented escape hatch.
    if os.environ.get("HERMES_DISABLE_WINDOWS_UTF8") in ("1", "true", "True"):
        return False

    # 1. Child processes inherit these and run in UTF-8 mode.
    #    We use setdefault() rather than overwriting so the user can
    #    explicitly opt out by setting PYTHONUTF8=0 in their environment
    #    (or PYTHONIOENCODING=something-else) if they really want to.
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    # 1a. Switch the Windows console code page to UTF-8 (CP 65001).
    #     This ensures that child processes reading from the console
    #     (including PowerShell's own [Console]::OutputEncoding when
    #     not overridden) see UTF-8 rather than cp1252.  The
    #     per-subprocess [Console]::OutputEncoding preamble is a
    #     belt-and-suspenders complement; this system-level setting
    #     catches everything else.
    try:
        import ctypes
        _CP_UTF8 = 65001
        kernel32 = ctypes.windll.kernel32
        kernel32.SetConsoleCP(_CP_UTF8)
        kernel32.SetConsoleOutputCP(_CP_UTF8)
    except Exception:
        # Non-fatal — the per-subprocess preamble still works.
        pass

    # 2. Reconfigure the current process's stdio to UTF-8.  Needed
    #    because os.environ changes don't retroactively rebind sys.stdout
    #    — those were bound at interpreter startup based on the console
    #    code page.  ``reconfigure`` is a TextIOWrapper method since 3.7.
    #
    #    errors="replace" means that if we ever *read* something from
    #    stdin that isn't UTF-8 (unlikely but possible with piped input
    #    from legacy tools), we'll get U+FFFD replacement chars rather
    #    than a crash.  Output is pure UTF-8.
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            # Not a TextIOWrapper (could be redirected to a BytesIO in
            # tests, or a non-standard stream in some embedded cases).
            # Skip silently — the env-var fix is still in effect for
            # child processes, which is the bigger win.
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            # Already closed, or someone replaced it with something
            # non-reconfigurable.  Non-fatal.
            pass

    # stdin is reconfigured separately with errors="replace" too — input
    # from a legacy pipe shouldn't crash the process.
    stdin = getattr(sys, "stdin", None)
    if stdin is not None:
        reconfigure = getattr(stdin, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (OSError, ValueError):
                pass

    _bootstrap_applied = True
    return True


def harden_import_path(src_root: str | None = None) -> None:
    """Stop a package in the current directory from shadowing Hermes modules.

    Hermes ships top-level modules with common names (``utils``, ``proxy``,
    ``ui``).  Python always seeds ``sys.path`` with the current directory, so
    launching an entry point from a project that has its own ``utils/`` package
    makes ``from utils import ...`` resolve to the *user's* package and crash
    with an ImportError before the gateway can even start.

    The current directory reaches ``sys.path`` two ways, and a complete guard
    has to handle both:

      - As the empty string ``""`` (or ``"."``) that Python inserts at
        ``sys.path[0]`` for ``-m`` / script launches.
      - As its own *absolute* path, when a venv activation or a project that
        adds itself to ``PYTHONPATH`` puts the directory there explicitly.

    We drop the relative forms outright, then force the real Hermes source root
    to the front — relocating it ahead of any absolute cwd entry rather than
    only inserting when absent, so an absolute cwd path can't keep winning.

    ``src_root`` defaults to the directory this module lives in, which is the
    repository root for every shipped entry point, so the guard is
    self-sufficient and does not depend on the spawner exporting an env var.
    """
    root = src_root or os.environ.get("HERMES_PYTHON_SRC_ROOT") or os.path.dirname(
        os.path.abspath(__file__)
    )

    sys.path[:] = [p for p in sys.path if p not in ("", ".")]

    root_abs = os.path.abspath(root)
    sys.path[:] = [p for p in sys.path if os.path.abspath(p) != root_abs]
    sys.path.insert(0, root)


def activate_durable_lazy_target() -> None:
    """Put the durable lazy-install dir on ``sys.path`` if one is configured.

    On immutable Docker images the agent venv is sealed and lazy installs
    are redirected to a writable dir on the data volume
    (``HERMES_LAZY_INSTALL_TARGET``, e.g. ``/opt/data/lazy-packages``).
    Packages installed there on a previous run must be importable on this
    run, so we activate the dir here — at the very first import, before any
    backend module imports its SDK.

    The activation appends to the END of ``sys.path`` so the core venv
    always wins name collisions (see ``tools.lazy_deps`` for the full
    security rationale). Never raises; a missing/empty target is a no-op.
    """
    if not os.environ.get("HERMES_LAZY_INSTALL_TARGET", "").strip():
        return
    try:
        from tools import lazy_deps
        lazy_deps.activate_durable_lazy_target()
    except Exception:
        # Bootstrap must never crash an entry point. If activation fails the
        # backend simply reports itself unavailable, exactly as before.
        pass


def install_import_accelerator() -> bool:
    """Install the first-party import accelerator (``import_accelerator``).

    Places a meta-path finder at ``sys.meta_path[0]`` that resolves Hermes's own
    top-level modules/packages with a single dict lookup, skipping the
    ``sys.path`` directory scan (``nt.stat`` / ``nt._path_exists``) the stock
    machinery pays per import.  Done here — the first import of every entry
    point — so the entire ``run_agent`` import cascade benefits.

    Fully guarded and idempotent: any failure (or a missing module during a
    partial ``hermes update``) leaves the standard import machinery untouched,
    exactly like the UTF-8 fast path above.  Returns True when THIS call
    installed the finder.  Honour ``HERMES_DISABLE_IMPORT_ACCELERATOR`` inside
    ``import_accelerator.install`` itself.
    """
    try:
        import import_accelerator

        return import_accelerator.install()
    except Exception:
        return False


def maybe_precompile_on_start() -> bool:
    """Opt-in background ``.pyc`` warm-up for the run-from-source layout.

    ``scripts/precompile.py`` front-loads ``builtins.compile`` (~10.82% of cold
    agent-init) off the hot path.  ``pip install`` already byte-compiles
    installed packages, so this only matters when Hermes runs *from source* (the
    CN fork's Windows default).  It is gated behind ``HERMES_PRECOMPILE_ON_START``
    and is a strict no-op:

    * unless that env var is truthy (so it never surprises anyone);
    * under a frozen build (no source tree to compile);
    * under pytest — the hermetic test runner gives every test file its own
      fresh temp ``HERMES_HOME`` (hence no stamp), so an ungated warm-up would
      spawn a ``compileall`` thread in *every* test subprocess and thrash CI.

    Runs :func:`precompile_in_background` (a daemon thread, stamp-guarded), so
    the first start after an update warms the cache while the user reads the
    banner and the NEXT start reads ready ``.pyc``.  Returns True when a warm-up
    thread was started.  Never raises.
    """
    if os.environ.get("HERMES_PRECOMPILE_ON_START") not in ("1", "true", "True"):
        return False
    if getattr(sys, "frozen", False):
        return False
    if os.environ.get("PYTEST_CURRENT_TEST") or "pytest" in sys.modules:
        return False
    try:
        import importlib.util

        root = os.path.dirname(os.path.abspath(__file__))
        pc_path = os.path.join(root, "scripts", "precompile.py")
        if not os.path.isfile(pc_path):
            return False
        spec = importlib.util.spec_from_file_location("_hermes_precompile_boot", pc_path)
        if spec is None or spec.loader is None:
            return False
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        module.precompile_in_background(quiet=True)
        return True
    except Exception:
        # A warm-up helper must never break startup.
        return False


# Apply on import — entry points just need ``import hermes_bootstrap``
# (or ``from hermes_bootstrap import apply_windows_utf8_bootstrap``) at
# the very top of their module, before importing anything else.  The
# import side effect does the right thing.
apply_windows_utf8_bootstrap()

# Accelerate the first-party import cascade that follows (best-effort; installed
# before the heavy modules load so they resolve via a dict lookup instead of a
# per-entry sys.path scan).
install_import_accelerator()

# Opt-in background .pyc warm-up (run-from-source only; no-op by default and
# under tests/frozen builds).
maybe_precompile_on_start()

# Activate the durable lazy-install target (immutable Docker images) so
# packages installed into the data volume on a previous run are importable
# this run, before any backend module imports its SDK. No-op when unset.
activate_durable_lazy_target()


def _configure_managed_runtime_caches() -> None:
    """Converge third-party framework/tool caches under the desktop-managed
    runtime's HERMES_HOME so they don't bloat C: on Windows.

    No-op unless ``HERMES_DESKTOP_MANAGED=1``.  Done here, at the first import of
    every entry point, so it runs before anything imports transformers / tiktoken
    / playwright.  The import is lazy and the whole thing is guarded so this
    module stays minimal and dependency-free for the UTF-8 fast path, and a
    failure here can never block startup.
    """
    try:
        from hermes_constants import configure_managed_runtime_caches

        configure_managed_runtime_caches()
    except Exception:
        pass


_configure_managed_runtime_caches()
