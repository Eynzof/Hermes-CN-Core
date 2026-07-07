"""Garbage-collector tuning for the agent-init hot path.

Python's generational cyclic GC fires automatically whenever the number of
container allocations minus deallocations in generation 0 crosses its
threshold (default 700).  Agent construction is allocation-heavy: the first
``AIAgent()`` triggers the lazy tool-module import cascade, config
deep-merges, provider resolution, credential handling, and context-engine
bootstrap — churning tens of thousands of transient dicts / lists / strings.
Left to run, the collector kicks in several times *during* init and each pass
stops the world to walk every tracked container.  That is pure overhead
(almost nothing allocated that early is unreachable cyclic garbage), and it is
exactly the ``<built-in method gc.collect>`` slice that dominated the
agent-init flame graph (.plans/14-GC-Collection-Overhead.md).

:func:`gc_init_freeze` suppresses *automatic* collection for the duration of
the init window and runs a single explicit :func:`gc.collect` afterwards.  The
same garbage is reclaimed in one batched pass instead of N threshold-triggered
stop-the-world scans, so the GC cost drops off the critical path while memory
is still reclaimed promptly once init finishes.

Why this is more than a bare ``gc.disable()`` / ``gc.enable()`` pair:

* **Re-entrant + concurrent safe.**  The gateway, batch runner, and parallel
  delegation construct agents from several threads, and construction can nest
  (an orchestrator builds a child agent).  A naive wrapped
  ``disable``/``enable`` would let an inner or sibling construction re-enable
  GC while an outer one is still initializing.  We depth-count under a lock;
  only the OUTERMOST window restores GC and performs the one collect.
* **Restores the ORIGINAL state.**  If the process deliberately ran
  ``gc.disable()`` before constructing an agent, we leave GC disabled on the
  way out and skip the collect — we never silently turn automatic GC back on
  or force a collection the operator opted out of.
* **Never raises.**  GC tuning is a pure optimization; any failure degrades to
  "GC behaves normally" and must never break agent construction.
* **Opt-out.**  Set ``HERMES_DISABLE_GC_INIT_FREEZE=1`` to revert to stock
  behavior (useful when debugging a suspected reclamation issue).

The optional :func:`freeze_permanent_objects` wraps ``gc.freeze()`` for
callers that want to move already-loaded, process-lifetime objects (imported
modules, tool schemas) into the permanent generation the collector never
scans.  It is opt-in only — freezing objects that later become garbage would
pin them for the process lifetime — so nothing here calls it automatically.
"""

from __future__ import annotations

import contextlib
import functools
import gc
import os
import threading
from typing import Callable, Iterator, TypeVar

__all__ = [
    "gc_init_freeze",
    "gc_frozen_init",
    "is_init_gc_frozen",
    "post_init_collect_count",
    "freeze_permanent_objects",
]

_ENV_DISABLE = "HERMES_DISABLE_GC_INIT_FREEZE"

# All state below is process-global because ``gc`` enabled-state is itself a
# single process-wide flag.  The lock is held only for the O(1) depth/flag
# bookkeeping at window enter/exit — never across the ``yield`` (arbitrary init
# code, which may spawn threads that construct more agents) and never across
# the post-init ``gc.collect()`` (a potentially slow heap walk).
_lock = threading.Lock()
_depth = 0                 # number of init windows currently open (all threads)
_gc_was_enabled = False    # gc.isenabled() captured when depth went 0 -> 1
_post_init_collects = 0    # count of explicit post-init collections performed


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _freeze_suppressed() -> bool:
    """True when the operator opted out of the init GC freeze."""
    try:
        return _truthy(os.environ.get(_ENV_DISABLE, ""))
    except Exception:
        return False


def _enter_window() -> None:
    """Open an init window, disabling automatic GC on the outermost entry."""
    global _depth, _gc_was_enabled
    with _lock:
        if _depth == 0:
            try:
                _gc_was_enabled = gc.isenabled()
                if _gc_was_enabled:
                    gc.disable()
            except Exception:
                # If we could not read/flip GC state, behave as if GC was
                # already off so the matching exit neither re-enables nor
                # collects — i.e. we make no net change.
                _gc_was_enabled = False
        _depth += 1


def _exit_window() -> None:
    """Close an init window; the outermost close restores GC and collects once."""
    global _depth, _post_init_collects
    do_collect = False
    with _lock:
        if _depth > 0:
            _depth -= 1
        if _depth == 0 and _gc_was_enabled:
            # Re-enable under the lock (cheap flag flip) so a concurrent window
            # opening right after we release cannot be re-enabled by us.
            try:
                gc.enable()
            except Exception:
                pass
            do_collect = True
            _post_init_collects += 1
    # Batched reclamation runs OUTSIDE the lock so concurrent constructions are
    # not serialized behind a full heap walk.  Running collect() while another
    # thread has since re-frozen (disabled) GC is harmless: collect() performs
    # one pass regardless of the enabled flag and does not alter it.
    if do_collect:
        try:
            gc.collect()
        except Exception:
            pass


@contextlib.contextmanager
def gc_init_freeze() -> Iterator[None]:
    """Suppress automatic GC for an init window, collecting once on exit.

    Safe to nest and to run concurrently across threads: only the outermost
    active window restores GC and performs the single explicit collection, and
    the original enabled-state is always preserved.  No-op (transparent
    passthrough) when ``HERMES_DISABLE_GC_INIT_FREEZE`` is set.
    """
    if _freeze_suppressed():
        yield
        return
    entered = False
    try:
        _enter_window()
        entered = True
        yield
    finally:
        if entered:
            _exit_window()


_F = TypeVar("_F", bound=Callable[..., object])


def gc_frozen_init(func: _F) -> _F:
    """Decorator running *func* inside :func:`gc_init_freeze`.

    Applied to ``agent.agent_init.init_agent`` so every construction path —
    including any that calls ``init_agent`` directly rather than through
    ``AIAgent.__init__`` — gets the freeze.  Depth-counting makes the nested
    case (``AIAgent.__init__`` already froze) a cheap no-op.
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        with gc_init_freeze():
            return func(*args, **kwargs)

    return wrapper  # type: ignore[return-value]


def is_init_gc_frozen() -> bool:
    """True while at least one init window is open (introspection / tests)."""
    with _lock:
        return _depth > 0


def post_init_collect_count() -> int:
    """Number of explicit post-init ``gc.collect()`` calls performed so far.

    Exposed for tests asserting the "one batched collection per init window"
    contract from the optimization plan's success criteria.
    """
    with _lock:
        return _post_init_collects


def freeze_permanent_objects() -> bool:
    """Move currently-tracked objects into the permanent (unscanned) generation.

    Thin, never-raising wrapper over :func:`gc.freeze` (CPython 3.7+).  Frozen
    objects are excluded from every future collection, so long-lived
    module-level state (imported modules, registered tool schemas) stops being
    re-scanned on each generational pass.

    Opt-in only and never called automatically: anything alive at call time is
    pinned for the process lifetime, so callers must invoke it at a point where
    only genuinely permanent objects are live (e.g. right after startup imports
    complete, before request-scoped work begins).  Returns True when the freeze
    ran, False if unavailable or suppressed.
    """
    if _freeze_suppressed():
        return False
    freeze = getattr(gc, "freeze", None)
    if freeze is None:  # pragma: no cover - only on <3.7 interpreters
        return False
    try:
        freeze()
        return True
    except Exception:
        return False
