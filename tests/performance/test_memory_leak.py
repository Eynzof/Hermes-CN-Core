"""Memory-leak and long-session stability benchmarks (P2 memory profile plan).

Covers the three memory optimizations:

  1. ``ToolResultStore`` — a memory-bounded tool-result buffer that streams
     large results to disk so a long-running session's resident set stays
     bounded regardless of turn count.
  2. ``gc.collect()`` after context compaction — reclaims the dropped
     transcript slice promptly instead of waiting on the generational GC.
  3. ``__slots__`` on the hot ``ToolCallResult`` record — no per-instance
     ``__dict__``.

All tests run OFFLINE (no LLM calls). The load-bearing assertions are
deterministic byte-accounting checks that hold with or without ``psutil``;
the optional RSS check skips gracefully when ``psutil`` is unavailable.
"""

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

from agent.tool_result_store import (  # noqa: E402
    DEFAULT_MAX_MEMORY_BYTES,
    DEFAULT_SPILL_THRESHOLD_BYTES,
    ToolCallResult,
    ToolResultStore,
    measure_result_bytes,
)


def _rss_mb() -> float:
    if HAS_PSUTIL:
        return psutil.Process().memory_info().rss / (1024 * 1024)
    return 0.0


# ── __slots__ contract ──────────────────────────────────────────────────────

@pytest.mark.perf
def test_tool_call_result_uses_slots(timing_context):
    """The hot per-tool record must carry no per-instance ``__dict__``."""
    r = ToolCallResult("terminal", {"command": "ls"}, "ok", duration_ms=3.0)
    assert r.name == "terminal"
    assert r.arguments == {"command": "ls"}
    assert r.result == "ok"
    assert r.duration_ms == 3.0
    assert r.error is None
    # __slots__ means no __dict__ and no ad-hoc attributes.
    assert not hasattr(r, "__dict__")
    with pytest.raises(AttributeError):
        r.unexpected_attr = 1  # type: ignore[attr-defined]


@pytest.mark.perf
def test_tool_result_store_uses_slots(timing_context):
    """The store itself is slotted so many concurrent stores stay cheap."""
    store = ToolResultStore()
    try:
        assert not hasattr(store, "__dict__")
        with pytest.raises(AttributeError):
            store.unexpected_attr = 1  # type: ignore[attr-defined]
    finally:
        store.close()


# ── spill-to-disk ───────────────────────────────────────────────────────────

@pytest.mark.perf
def test_tool_result_spill_to_disk(tmp_path, timing_context):
    """A large result streams to disk; small ones stay in memory."""
    spill_dir = tmp_path / "spill"
    store = ToolResultStore(
        max_memory_bytes=10 * 1024 * 1024,
        spill_threshold_bytes=100 * 1024,
        spill_dir=spill_dir,
    )
    try:
        small_idx = store.add("x" * 32)
        big_idx = store.add("B" * (200 * 1024))

        assert store.memory_count == 1
        assert store.spilled_count == 1
        # Spilled payload is NOT counted against the in-memory footprint.
        assert store.memory_bytes < 1024
        assert store.spilled_bytes >= 200 * 1024

        # Exactly one file was written for the one spilled result.
        files = list(spill_dir.glob("*.json"))
        assert len(files) == 1, files

        # Retrieval is transparent regardless of where a result lives.
        assert store.get(small_idx) == "x" * 32
        assert store.get(big_idx) == "B" * (200 * 1024)
    finally:
        store.close()


@pytest.mark.perf
def test_tool_result_store_memory_bound(tmp_path, timing_context):
    """The aggregate cap bounds resident memory even for many small results."""
    cap = 64 * 1024
    store = ToolResultStore(
        max_memory_bytes=cap,
        spill_threshold_bytes=0,  # disable per-result trigger; only cap bites
        spill_dir=tmp_path / "spill",
    )
    try:
        for _ in range(500):
            store.add("y" * 1024)
        # 500 KB of payload, but resident memory never exceeds the 64 KB cap.
        assert store.memory_bytes <= cap
        assert store.spilled_count > 0
        assert len(store) == 500
        # Everything is still retrievable.
        assert store.get(0) == "y" * 1024
        assert store.get(499) == "y" * 1024
    finally:
        store.close()


@pytest.mark.perf
def test_tool_result_store_roundtrips_payload_types(tmp_path, timing_context):
    """str / dict / list / number / bytes all survive a disk round-trip."""
    store = ToolResultStore(spill_threshold_bytes=1, spill_dir=tmp_path / "spill")
    try:
        cases = [
            "a plain string",
            {"nested": [1, 2, {"deep": True}]},
            [1, 2, 3, "four"],
            42,
            b"\x00\x01raw-bytes\xff",
        ]
        indices = [store.add(c) for c in cases]
        # spill_threshold=1 forces every non-empty payload to disk.
        assert store.spilled_count == len(cases)
        for case, idx in zip(cases, indices):
            assert store.get(idx) == case
    finally:
        store.close()


@pytest.mark.perf
def test_tool_result_store_clear_and_close_cleanup(tmp_path, timing_context):
    """clear() deletes spilled files; close() removes the spill directory."""
    spill_dir = tmp_path / "spill"
    store = ToolResultStore(spill_threshold_bytes=1, spill_dir=spill_dir)
    for i in range(5):
        store.add(f"item-{i}-" * 100)
    assert list(spill_dir.glob("*.json"))

    store.clear()
    assert len(store) == 0
    assert store.memory_bytes == 0
    assert store.spilled_bytes == 0
    assert not list(spill_dir.glob("*.json"))  # files removed

    # Store is still usable after clear().
    again = store.add("reused-" * 100)
    assert store.get(again) == "reused-" * 100

    store.close()
    assert not spill_dir.exists()  # directory removed on close


@pytest.mark.perf
def test_tool_result_store_context_manager(tmp_path, timing_context):
    """Context-manager exit cleans up spilled files and the directory."""
    spill_dir = tmp_path / "cm"
    with ToolResultStore(spill_threshold_bytes=1, spill_dir=spill_dir) as store:
        store.add("z" * 512)
        assert list(spill_dir.glob("*.json"))
    assert not spill_dir.exists()


@pytest.mark.perf
def test_measure_result_bytes(timing_context):
    """Byte measurement is stable across payload shapes (used for accounting)."""
    assert measure_result_bytes("abc") == 3
    assert measure_result_bytes(b"abcd") == 4
    # Non-ASCII counts UTF-8 bytes, not code points.
    assert measure_result_bytes("é") == 2
    # JSON-encoded objects are measured through their serialization.
    assert measure_result_bytes({"k": "v"}) == len('{"k":"v"}')
    # Defaults are sane, positive ceilings.
    assert DEFAULT_MAX_MEMORY_BYTES > DEFAULT_SPILL_THRESHOLD_BYTES > 0


# ── long-session stability ──────────────────────────────────────────────────

@pytest.mark.perf
def test_long_session_memory_stability(tmp_path, timing_context):
    """1000 turns must not grow resident tool-result memory unboundedly."""
    cap = 128 * 1024
    store = ToolResultStore(
        max_memory_bytes=cap,
        spill_threshold_bytes=64 * 1024,
        spill_dir=tmp_path / "spill",
    )
    try:
        peak = 0
        checkpoints = []
        for i in range(1000):
            store.add(f"turn-{i} " * 128)  # ~1 KB per turn
            if i % 100 == 0:
                checkpoints.append(store.memory_bytes)
            peak = max(peak, store.memory_bytes)

        peak = max(peak, store.memory_bytes)
        # Resident memory is bounded by the cap, independent of turn count.
        assert peak <= cap, f"resident memory {peak} exceeded cap {cap}"
        assert len(store) == 1000
        assert store.spilled_count > 0  # cap actually engaged (results spilled)
        # No monotonic growth: later checkpoints are not larger than the cap.
        assert max(checkpoints) <= cap
        # Both ends of the session remain retrievable.
        assert store.get(0).startswith("turn-0 ")
        assert store.get(999).startswith("turn-999 ")
    finally:
        store.close()


@pytest.mark.perf
def test_long_session_rss_stability(tmp_path, timing_context):
    """Process RSS stays far below the un-spilled payload total over 1000 turns."""
    if not HAS_PSUTIL:
        pytest.skip("psutil not available")
    import gc

    gc.collect()
    rss_before = _rss_mb()

    store = ToolResultStore(
        max_memory_bytes=256 * 1024,
        spill_threshold_bytes=64 * 1024,
        spill_dir=tmp_path / "spill",
    )
    try:
        for i in range(1000):
            store.add((f"payload-{i} " * 512))  # ~5 KB each -> ~5 MB total
        gc.collect()
        rss_after = _rss_mb()
        growth = rss_after - rss_before

        # ~5 MB of raw payload, but the store caps resident data at 256 KB, so
        # RSS growth must stay far below the un-spilled footprint. Generous
        # bound keeps this from flaking on noisy CI while still catching a real
        # unbounded leak (which would show tens of MB of growth).
        print(f"\n  RSS: {rss_before:.1f} -> {rss_after:.1f} MB (growth {growth:.1f} MB)")
        assert growth < 50, f"RSS grew {growth:.1f} MB over 1000 turns (leak?)"
        assert store.memory_bytes <= 256 * 1024
    finally:
        store.close()


# ── gc after compaction ─────────────────────────────────────────────────────

@pytest.mark.perf
def test_gc_after_compaction_helper(monkeypatch, timing_context):
    """The GC trigger fires only when a compaction drops enough messages."""
    import agent.context_compressor as cc
    from agent.context_compressor import (
        _GC_AFTER_COMPACTION_DROP_THRESHOLD,
        maybe_collect_after_compaction,
    )

    calls = []
    monkeypatch.setattr(cc.gc, "collect", lambda *a, **k: calls.append(1))

    # Below threshold -> no collection.
    assert maybe_collect_after_compaction(_GC_AFTER_COMPACTION_DROP_THRESHOLD - 1) is False
    # At/above threshold -> collect.
    assert maybe_collect_after_compaction(_GC_AFTER_COMPACTION_DROP_THRESHOLD) is True
    assert maybe_collect_after_compaction(1000) is True
    # A non-positive threshold disables the trigger.
    assert maybe_collect_after_compaction(1000, threshold=0) is False
    assert len(calls) == 2


@pytest.mark.perf
def test_gc_runs_after_real_compaction(monkeypatch, timing_context):
    """A real compaction that drops many messages invokes gc.collect()."""
    import agent.context_compressor as cc
    from agent.context_compressor import ContextCompressor

    comp = ContextCompressor(
        model="test/mock-model",
        threshold_percent=0.5,
        protect_first_n=1,
        protect_last_n=2,
        quiet_mode=True,
        config_context_length=200_000,
    )
    # Force a small protected tail so a large middle is summarizable, and stub
    # the summariser so no network call happens.
    comp.tail_token_budget = 60
    monkeypatch.setattr(comp, "_generate_summary", lambda *a, **k: "STRUCTURED SUMMARY")

    real_collect = cc.gc.collect
    calls = []

    def counting_collect(*a, **k):
        calls.append(1)
        return real_collect(*a, **k)

    monkeypatch.setattr(cc.gc, "collect", counting_collect)

    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(30):
        messages.append({"role": "user", "content": f"Question {i} " * 20})
        messages.append({"role": "assistant", "content": f"Answer {i} " * 40})

    compressed = comp.compress(messages, current_tokens=150_000)

    assert len(compressed) < len(messages), "compaction should drop messages"
    assert len(calls) >= 1, "gc.collect() should run after a large compaction drop"


@pytest.mark.perf
def test_no_gc_when_compaction_is_a_noop(monkeypatch, timing_context):
    """A transcript too small to compact must not trigger a GC pass."""
    import agent.context_compressor as cc
    from agent.context_compressor import ContextCompressor

    comp = ContextCompressor(
        model="test/mock-model",
        protect_first_n=1,
        protect_last_n=2,
        quiet_mode=True,
        config_context_length=200_000,
    )
    calls = []
    monkeypatch.setattr(cc.gc, "collect", lambda *a, **k: calls.append(1))

    tiny = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "hi"},
    ]
    out = comp.compress(tiny)
    assert out == tiny  # returned unchanged
    assert calls == []  # no stop-the-world pass for a no-op compaction


# ── run_agent surface ───────────────────────────────────────────────────────

@pytest.mark.perf
def test_tool_result_store_exposed_by_run_agent(timing_context):
    """ToolResultStore/ToolCallResult are importable from run_agent (plan surface)."""
    import run_agent

    assert run_agent.ToolResultStore is ToolResultStore
    assert run_agent.ToolCallResult is ToolCallResult
