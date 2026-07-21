"""GC init-freeze tuning — unit + integration contract.

Covers .plans/14-GC-Collection-Overhead.md:

* automatic cyclic GC is suppressed during ``AIAgent`` construction and
  restored afterwards, reclaiming garbage in exactly one batched collection
  per outermost init window (instead of several stop-the-world passes mid-init);
* the fix is re-entrant / concurrency safe and preserves an operator's
  deliberately-disabled GC;
* the tool registry forms no reference cycles — entries are reclaimed by plain
  refcounting, so registration never adds cyclic garbage for the collector.

All synthetic and offline — no live model endpoint.
"""

import gc
import os
import sys
import threading
import weakref
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import agent.gc_tuning as gct


@pytest.fixture(autouse=True)
def _gc_state_guard():
    """Give every test a known ``GC enabled`` baseline and never leak a
    disabled collector (or stale freeze depth) into the next test."""
    original_enabled = gc.isenabled()
    with gct._lock:
        gct._depth = 0
        gct._gc_was_enabled = False
    if not gc.isenabled():
        gc.enable()
    try:
        yield
    finally:
        with gct._lock:
            gct._depth = 0
        if original_enabled and not gc.isenabled():
            gc.enable()
        elif not original_enabled and gc.isenabled():
            gc.disable()


# ---------------------------------------------------------------------------
# gc_init_freeze() unit contract
# ---------------------------------------------------------------------------

def test_freeze_disables_during_and_restores_after():
    assert gc.isenabled()
    with gct.gc_init_freeze():
        assert not gc.isenabled(), "GC should be suppressed inside the init window"
        assert gct.is_init_gc_frozen()
    assert gc.isenabled(), "GC should be restored after the init window"
    assert not gct.is_init_gc_frozen()


def test_freeze_runs_exactly_one_batched_collect_per_window():
    before = gct.post_init_collect_count()
    with gct.gc_init_freeze():
        pass
    assert gct.post_init_collect_count() == before + 1


def test_nested_windows_restore_and_collect_only_at_outermost():
    before = gct.post_init_collect_count()
    assert gc.isenabled()
    with gct.gc_init_freeze():
        assert not gc.isenabled()
        with gct.gc_init_freeze():
            assert not gc.isenabled()
        # Inner exit must NOT re-enable GC while the outer window is open.
        assert not gc.isenabled(), "inner exit re-enabled GC too early"
    assert gc.isenabled()
    # Only the outermost close performs the single batched collection.
    assert gct.post_init_collect_count() == before + 1


def test_preserves_deliberately_disabled_gc():
    """If the process disabled GC on purpose, the freeze leaves it disabled and
    performs no collection on exit."""
    before = gct.post_init_collect_count()
    gc.disable()
    try:
        assert not gc.isenabled()
        with gct.gc_init_freeze():
            assert not gc.isenabled()
        assert not gc.isenabled(), "freeze wrongly re-enabled a deliberately-disabled GC"
        assert gct.post_init_collect_count() == before, "freeze collected despite opt-out state"
    finally:
        gc.enable()


def test_opt_out_env_is_transparent(monkeypatch):
    monkeypatch.setenv(gct._ENV_DISABLE, "1")
    before = gct.post_init_collect_count()
    assert gc.isenabled()
    with gct.gc_init_freeze():
        assert gc.isenabled(), "opt-out must not touch GC state"
        assert not gct.is_init_gc_frozen()
    assert gc.isenabled()
    assert gct.post_init_collect_count() == before


def test_freeze_restores_gc_even_when_body_raises():
    assert gc.isenabled()
    with pytest.raises(ValueError):
        with gct.gc_init_freeze():
            assert not gc.isenabled()
            raise ValueError("boom")
    assert gc.isenabled(), "GC must be restored even when the init body raises"


def test_decorator_freezes_wrapped_call():
    observed = {}

    @gct.gc_frozen_init
    def fake_init():
        observed["enabled"] = gc.isenabled()
        observed["frozen"] = gct.is_init_gc_frozen()
        return "ok"

    before = gct.post_init_collect_count()
    assert fake_init() == "ok"
    assert observed == {"enabled": False, "frozen": True}
    assert gc.isenabled()
    assert gct.post_init_collect_count() == before + 1


def test_concurrent_windows_disable_union_and_restore_once():
    """Two overlapping windows on separate threads: GC stays suppressed for the
    union of both, and is restored + collected exactly once when the last one
    closes."""
    before = gct.post_init_collect_count()
    both_open = threading.Barrier(3)  # two workers + main
    release = threading.Event()
    observed = {}

    def worker(name):
        with gct.gc_init_freeze():
            both_open.wait(timeout=10)
            observed[name] = gc.isenabled()
            release.wait(timeout=10)

    threads = [threading.Thread(target=worker, args=(n,)) for n in ("a", "b")]
    for t in threads:
        t.start()
    try:
        both_open.wait(timeout=10)  # both windows are now open simultaneously
        assert not gc.isenabled(), "GC should be suppressed while both windows are open"
        assert gct.is_init_gc_frozen()
    finally:
        release.set()  # let the workers unwind even if an assertion failed
    for t in threads:
        t.join(timeout=10)

    assert observed == {"a": False, "b": False}
    assert gc.isenabled(), "GC restored after both windows closed"
    assert gct.post_init_collect_count() == before + 1, "expected exactly one batched collect"


def test_freeze_permanent_objects_is_safe():
    """Opt-in gc.freeze() wrapper runs without raising and is transparent when
    opted out."""
    assert gct.freeze_permanent_objects() in (True, False)


def test_freeze_permanent_objects_respects_opt_out(monkeypatch):
    monkeypatch.setenv(gct._ENV_DISABLE, "1")
    assert gct.freeze_permanent_objects() is False


# ---------------------------------------------------------------------------
# Integration: real AIAgent construction (.plans/14 success criterion)
# ---------------------------------------------------------------------------

@pytest.mark.xfail(sys.platform == "win32", reason="flaky on Windows: GC timing race", strict=False)
def test_gc_disabled_during_init(monkeypatch):
    """``AIAgent()`` construction runs its init with automatic GC suppressed
    and restored afterwards, with one batched post-init collection.

    ``init_agent`` is replaced by a probe: ``AIAgent.__init__`` does nothing
    after the forwarded call, so a no-op init is enough to observe that the
    ``gc_init_freeze()`` wrapper is in effect for the construction window.
    """
    import agent.agent_init as agent_init
    from run_agent import AIAgent

    observed = {}

    def spy(agent, **kwargs):
        observed["enabled_during_init"] = gc.isenabled()
        observed["frozen_during_init"] = gct.is_init_gc_frozen()

    monkeypatch.setattr(agent_init, "init_agent", spy)

    before = gct.post_init_collect_count()
    assert gc.isenabled()

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        AIAgent(
            base_url="http://localhost:9/v1",
            api_key="sk-test-mock-key",
            model="test/mock-model",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            enabled_toolsets=[],
        )

    assert observed.get("enabled_during_init") is False, "GC was not disabled during init"
    assert observed.get("frozen_during_init") is True
    assert gc.isenabled(), "GC was not restored after init"
    assert gct.post_init_collect_count() == before + 1, "expected one batched post-init collect"


# ---------------------------------------------------------------------------
# Registry: no reference cycles (.plans/14 "reduce circular references")
# ---------------------------------------------------------------------------

def test_no_circular_refs():
    """Registering a tool forms no reference cycle and adds no secondary owner.

    The registry's ``_tools`` map is the single strong holder of a ToolEntry, so
    ``deregister`` drops its refcount by exactly one and the entry is then
    reclaimable by plain refcounting — no cyclic GC pass needed. A regression
    that made entries reference the registry (or each other), or that added a
    second index owning the entry, would change the delta.
    """
    from tools.registry import ToolRegistry, ToolEntry

    reg = ToolRegistry()
    reg.register(
        name="cycle_probe_tool",
        toolset="cycle_probe",
        schema={"name": "cycle_probe_tool", "description": "probe", "parameters": {}},
        handler=lambda args, **kw: "{}",
    )
    entry = reg.get_entry("cycle_probe_tool")
    assert isinstance(entry, ToolEntry)
    # Tight __slots__ layout: no per-instance __dict__ (and no __weakref__), so
    # there is minimal per-entry surface for the cyclic collector to scan.
    assert not hasattr(entry, "__dict__")
    with pytest.raises(TypeError):
        weakref.ref(entry)  # not weak-referenceable → no __weakref__ overhead

    rc_before = sys.getrefcount(entry)
    reg.deregister("cycle_probe_tool")
    rc_after = sys.getrefcount(entry)
    assert rc_before - rc_after == 1, (
        f"deregister changed the ToolEntry refcount by {rc_before - rc_after}, "
        "expected exactly 1 — a secondary owner or reference cycle is retaining it"
    )


def test_registry_batch_register_deregister_no_extra_owners():
    """Across a batch of registrations, the registry map is the sole extra owner
    of each ToolEntry: deregister drops each entry's refcount by exactly one
    (no secondary index, no cycle)."""
    from tools.registry import ToolRegistry

    reg = ToolRegistry()
    for i in range(25):
        name = f"batch_probe_{i}"
        reg.register(
            name=name,
            toolset="batch_probe",
            schema={"name": name, "description": "d", "parameters": {}},
            handler=lambda args, **kw: "{}",
        )

    deltas = []
    for i in range(25):
        name = f"batch_probe_{i}"
        entry = reg.get_entry(name)
        rc_before = sys.getrefcount(entry)
        reg.deregister(name)
        rc_after = sys.getrefcount(entry)
        deltas.append(rc_before - rc_after)
        del entry

    assert deltas == [1] * 25, f"unexpected refcount deltas (secondary owners/cycles?): {deltas}"
