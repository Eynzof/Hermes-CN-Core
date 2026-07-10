"""Performance benchmarks for tool dispatch system.

All tests run in OFFLINE mode — no real LLM API calls.
"""

import orjson
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.perf
def test_handle_function_call_simple(timing_context):
    """Measure handle_function_call with simple tool arguments."""
    from model_tools import handle_function_call

    test_cases = [
        ("read_file", {"path": "/tmp/test.txt"}),
        ("write_file", {"path": "/tmp/out.txt", "content": "hello world"}),
    ]

    with timing_context.measure("dispatch_simple"):
        for tool_name, args in test_cases:
            result = handle_function_call(tool_name, orjson.dumps(args).decode('utf-8'))

    summary = timing_context.summary()
    total_ms = summary.get("dispatch_simple", {}).get("total_ms", 0)
    avg_ms = total_ms / len(test_cases)
    print(f"\n  Dispatch 2 simple tools: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert total_ms < 5000, f"2 dispatches took {total_ms:.1f}ms (expected < 5000ms)"


@pytest.mark.perf
def test_check_fn_cached_timing(timing_context):
    """Measure check_fn cached probe overhead."""
    from tools.registry import registry, _check_fn_cached

    def fast_check_fn() -> bool:
        return True

    registry.register(
        name="_perf_test_tool",
        toolset="perf_test",
        schema={"name": "_perf_test_tool", "description": "perf test", "parameters": {}},
        handler=lambda args, **kw: '{"ok": true}',
        check_fn=fast_check_fn,
    )

    with timing_context.measure("check_fn_uncached"):
        result = _check_fn_cached(fast_check_fn)

    with timing_context.measure("check_fn_cached"):
        result2 = _check_fn_cached(fast_check_fn)

    summary = timing_context.summary()
    uncached_ms = summary.get("check_fn_uncached", {}).get("total_ms", 0)
    cached_ms = summary.get("check_fn_cached", {}).get("total_ms", 0)
    print(f"\n  check_fn uncached: {uncached_ms:.1f}ms")
    print(f"  check_fn cached: {cached_ms:.1f}ms")


@pytest.mark.perf
def test_tool_definitions_rebuild_timing(timing_context):
    """Measure get_tool_definitions overhead with cache invalidation."""
    from model_tools import get_tool_definitions
    from tools.registry import registry

    # Warm the cache
    _ = get_tool_definitions()

    with timing_context.measure("get_defs_cached"):
        tools = get_tool_definitions()

    # Force cache regeneration
    registry._generation += 1

    with timing_context.measure("get_defs_rebuild"):
        tools2 = get_tool_definitions()

    summary = timing_context.summary()
    cached_ms = summary.get("get_defs_cached", {}).get("total_ms", 0)
    rebuild_ms = summary.get("get_defs_rebuild", {}).get("total_ms", 0)
    print(f"\n  Tool defs (cached): {cached_ms:.1f}ms")
    print(f"  Tool defs (rebuild): {rebuild_ms:.1f}ms")
    assert len(tools) > 0
    assert len(tools2) > 0


@pytest.mark.perf
def test_tool_schema_generation_timing(timing_context):
    """Measure JSON schema generation for tool definitions."""
    from model_tools import get_tool_definitions
    import orjson as json_mod

    with timing_context.measure("schema_generation"):
        tools = get_tool_definitions()
        schema_json = json_mod.dumps(tools).decode('utf-8')

    total_ms = timing_context.summary().get("schema_generation", {}).get("total_ms", 0)
    schema_size = len(schema_json)
    print(f"\n  Schema generation: {total_ms:.1f}ms, {schema_size} bytes")
    assert total_ms < 2000, f"Schema generation took {total_ms:.1f}ms (expected < 2000ms)"
    assert len(tools) > 0


@pytest.mark.perf
def test_tool_budget_enforcement_timing(timing_context):
    """Measure tool result budget enforcement timing."""
    from tools.tool_result_storage import enforce_turn_budget

    results = [
        {"tool_name": f"tool_{i}", "result": orjson.dumps({"data": "x" * 500}).decode('utf-8')}
        for i in range(20)
    ]

    with timing_context.measure("enforce_budget"):
        pruned = enforce_turn_budget(results)

    total_ms = timing_context.summary().get("enforce_budget", {}).get("total_ms", 0)
    print(f"\n  Budget enforcement (20 results): {total_ms:.1f}ms")
    assert total_ms < 500, f"Budget enforcement took {total_ms:.1f}ms (expected < 500ms)"

# ─────────────────────────────────────────────────────────────────────────────
# P1: Concurrent tool dispatch — fast path + no-contention benchmarks
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.perf
def test_dispatch_fast_path(timing_context, tmp_path, monkeypatch):
    """Exact-schema arguments bypass the repair + double-coerce pipeline.

    When a tool call's arguments already match the schema (known keys, correct
    types, no nested-object repair), ``handle_function_call`` must skip
    ``repair_tool_arg_keys`` (and the redundant re-coercion) entirely — that
    pipeline is the dominant per-call overhead once tool discovery is warm.
    Non-exact payloads (aliased keys, string-typed numbers) must still be
    repaired/coerced, and JSON-string arguments must be parsed, not dropped.
    """
    import model_tools
    from model_tools import (
        handle_function_call,
        get_tool_definitions,
        _args_match_schema_exactly,
    )
    from tools.registry import registry

    # Warm discovery so schema lookups + tool-module imports are cached. That
    # first-call cost is a separate hotspot; the fast path targets everything
    # after it.
    get_tool_definitions()

    # read_file dedups an unchanged re-read (and blocks re-reading a region
    # ~50x), so every content-returning assertion below uses its OWN fresh file
    # — that keeps these checks about dispatch shape, not the read-loop guards.
    def _fresh(name: str, text: str) -> str:
        f = tmp_path / name
        f.write_text(text, encoding="utf-8")
        return str(f)

    schema = registry.get_schema("read_file")
    probe = str(tmp_path / "probe.txt")  # the classifier never touches disk
    # Exactness classifier: the gate the fast path relies on.
    assert _args_match_schema_exactly({"path": probe}, schema) is True
    assert _args_match_schema_exactly({"file": probe}, schema) is False       # alias → repair
    assert _args_match_schema_exactly({"path": probe, "offset": "2"}, schema) is False  # coerce

    # Warm the terminal env once on a throwaway file (env creation is a
    # one-time cost, not part of the per-dispatch overhead we measure).
    handle_function_call("read_file", {"path": _fresh("warm.txt", "warm\n")})

    # Spy on the repair pass to prove the fast path bypasses it.
    calls = {"n": 0}
    real_repair = model_tools.repair_tool_arg_keys

    def _spy(name, args, *a, **k):
        calls["n"] += 1
        return real_repair(name, args, *a, **k)

    monkeypatch.setattr(model_tools, "repair_tool_arg_keys", _spy)

    # Exact-match dispatch: repair must NOT run, and the arguments must reach
    # the handler (a fresh file is read back).
    calls["n"] = 0
    res_exact = handle_function_call("read_file", {"path": _fresh("exact.txt", "hello exact\n")})
    assert calls["n"] == 0, f"repair ran {calls['n']}x on exact-match args"
    assert "hello exact" in res_exact

    # JSON-string arguments are parsed once and take the same fast path — they
    # must NOT be silently dropped to an empty dict.
    calls["n"] = 0
    res_str = handle_function_call("read_file", orjson.dumps({"path": _fresh("jstr.txt", "hello jstr\n")}).decode('utf-8'))
    assert calls["n"] == 0
    assert "hello jstr" in res_str

    # Non-exact (aliased key) dispatch: repair MUST run and fix file → path.
    calls["n"] = 0
    res_alias = handle_function_call("read_file", {"file": _fresh("alias.txt", "hello alias\n")})
    assert calls["n"] == 1, "repair should run once for an aliased key"
    assert "hello alias" in res_alias

    # Timing loop over DISTINCT fresh files so the read-loop / dedup guards
    # never trip (each file is read exactly once).
    loop_files = [_fresh(f"loop_{i}.txt", f"loop {i}\n") for i in range(40)]
    calls["n"] = 0
    with timing_context.measure("dispatch_fast_path"):
        for lp in loop_files:
            handle_function_call("read_file", {"path": lp})
    assert calls["n"] == 0, f"repair ran {calls['n']}x across exact-match dispatches"

    total_ms = timing_context.summary().get("dispatch_fast_path", {}).get("total_ms", 0)
    avg_ms = total_ms / len(loop_files)
    print(f"\n  Fast-path dispatch x{len(loop_files)}: {total_ms:.1f}ms (avg {avg_ms:.3f}ms)")
    assert avg_ms < 50, f"fast-path dispatch avg {avg_ms:.3f}ms (expected < 50ms)"


@pytest.mark.perf
def test_concurrent_dispatch_no_contention(timing_context, tmp_path):
    """Three concurrent file-op dispatches finish well under the 500ms target.

    Mirrors the ``tool_dispatch_three_calls`` hotspot but with discovery warmed
    (that cost is a separate plan). Distinct files exercise the striped per-file
    write lock without cross-file contention.
    """
    from concurrent.futures import ThreadPoolExecutor
    from model_tools import handle_function_call, get_tool_definitions

    get_tool_definitions()  # warm discovery (separate hotspot)

    files = []
    for i in range(3):
        f = tmp_path / f"c{i}.txt"
        f.write_text(f"content {i}\n", encoding="utf-8")
        files.append(str(f))

    # Warm the terminal env once so env creation isn't charged to the timed run.
    for p in files:
        handle_function_call("read_file", {"path": p})

    def _op(p):
        rd = handle_function_call("read_file", {"path": p})
        wr = handle_function_call("write_file", {"path": p, "content": "updated\n"})
        return rd, wr

    with timing_context.measure("concurrent_dispatch_three"):
        with ThreadPoolExecutor(max_workers=3) as ex:
            list(ex.map(_op, files))

    total_ms = timing_context.summary().get("concurrent_dispatch_three", {}).get("total_ms", 0)
    print(f"\n  3 concurrent file-op dispatches: {total_ms:.1f}ms")
    assert total_ms < 500, f"3 concurrent dispatches took {total_ms:.1f}ms (expected < 500ms)"

    # Correctness: every write landed.
    for p in files:
        assert Path(p).read_text(encoding="utf-8") == "updated\n"


# ─────────────────────────────────────────────────────────────────────────────
# P-043: First-dispatch latency — background warmup of the dispatch path
#
# The cold first dispatch / first API request pays a ~4,486 ms tax (tool-module
# imports + check_fn probes + schema assembly). warm_dispatch_path() moves that
# off the user-visible hot path so the first real tool call is fast, not a
# multi-second hang. Hotspots #8 (dispatch_simple) / #9 (get_tool_definitions_
# first) in reports/perf/root-cause-analysis.md.
# ─────────────────────────────────────────────────────────────────────────────


def _fresh_file(tmp_path, name: str, text: str) -> str:
    """Write a distinct file and return its path.

    read_file dedups an unchanged re-read (and rate-limits re-reading a region),
    so every dispatch below reads its OWN fresh file — that keeps these checks
    about dispatch latency, not the read-loop guards.
    """
    f = tmp_path / name
    f.write_text(text, encoding="utf-8")
    return str(f)


@pytest.mark.perf
def test_dispatch_first_call_latency(timing_context, tmp_path):
    """The first real tool dispatch, after the idle-window warmup, is < 100 ms.

    Simulates what an entry point does before the first turn (CLI banner idle
    window / gateway startup / ``AIAgent.warmup()``): ``warm_dispatch_path``
    completes discovery + builds the schema catalog + pre-serializes schemas.
    With that done off the hot path, the user's first dispatch must be far under
    the ~4,486 ms cold outlier this plan targets.
    """
    from model_tools import handle_function_call, warm_dispatch_path

    # 1) Idle-window warmup (synchronous + forced so the warm body runs here
    #    regardless of what earlier tests in this process already warmed).
    warm_dispatch_path(background=False, force=True)

    # 2) Warm the terminal env once on a throwaway file — env creation is a
    #    one-time cost, not the discovery cold-start this plan removes (the
    #    fast-path test warms it the same way).
    handle_function_call("read_file", {"path": _fresh_file(tmp_path, "warm.txt", "warm\n")})

    # 3) Measure the first content dispatch after warmup.
    target = _fresh_file(tmp_path, "first.txt", "hello first dispatch\n")
    with timing_context.measure("first_dispatch_warm"):
        res = handle_function_call("read_file", {"path": target})
    assert "hello first dispatch" in res

    total_ms = timing_context.summary().get("first_dispatch_warm", {}).get("total_ms", 0)
    print(f"\n  First dispatch (warmed): {total_ms:.2f}ms (cold baseline ~4,486ms)")
    assert total_ms < 100, f"first warmed dispatch took {total_ms:.2f}ms (expected < 100ms)"


@pytest.mark.perf
def test_args_exact_match_bypass(monkeypatch, tmp_path):
    """Exact-schema args skip the P-013 repair pass; drifted args still repair.

    Verifies the fast-path gate (``_args_match_schema_exactly``) and that
    ``handle_function_call`` acts on it — repair must NOT run for exact args and
    MUST run once for an aliased key — without measuring time (that's the
    fast-path / benchmark tests' job).
    """
    import model_tools
    from model_tools import (
        handle_function_call,
        get_tool_definitions,
        _args_match_schema_exactly,
    )
    from tools.registry import registry

    get_tool_definitions()  # warm discovery so schema lookups are cached
    schema = registry.get_schema("read_file")
    probe = str(tmp_path / "probe.txt")  # the classifier never touches disk

    # Classifier truth table — the exact gate the fast path relies on.
    assert _args_match_schema_exactly({"path": probe}, schema) is True
    assert _args_match_schema_exactly({"file": probe}, schema) is False           # alias → repair
    assert _args_match_schema_exactly({"path": probe, "offset": "2"}, schema) is False  # coerce
    assert _args_match_schema_exactly("not-a-dict", schema) is False               # non-dict
    assert _args_match_schema_exactly({"path": probe}, None) is False              # no schema

    # Warm the terminal env once.
    handle_function_call("read_file", {"path": _fresh_file(tmp_path, "w.txt", "w\n")})

    # Spy on the repair pass.
    calls = {"n": 0}
    real_repair = model_tools.repair_tool_arg_keys

    def _spy(name, args, *a, **k):
        calls["n"] += 1
        return real_repair(name, args, *a, **k)

    monkeypatch.setattr(model_tools, "repair_tool_arg_keys", _spy)

    # Exact args → repair skipped, arguments still reach the handler.
    calls["n"] = 0
    res_exact = handle_function_call("read_file", {"path": _fresh_file(tmp_path, "e.txt", "exact ok\n")})
    assert calls["n"] == 0, f"repair ran {calls['n']}x on exact-match args"
    assert "exact ok" in res_exact

    # Aliased key → repair runs exactly once and fixes file → path.
    calls["n"] = 0
    res_alias = handle_function_call("read_file", {"file": _fresh_file(tmp_path, "a.txt", "alias ok\n")})
    assert calls["n"] == 1, "repair should run once for an aliased key"
    assert "alias ok" in res_alias


@pytest.mark.perf
def test_dispatch_warmup_benchmark(timing_context, tmp_path):
    """10 dispatches in a loop after warmup — no multi-second cold outlier.

    Cold, the first of these would be the ~4,486 ms ``dispatch_simple`` outlier.
    With ``warm_dispatch_path`` run first (as an entry point does at idle) every
    call is on the warm fast path, so the whole loop stays in the low-ms range.
    """
    from model_tools import handle_function_call, warm_dispatch_path

    warm_dispatch_path(background=False, force=True)  # idle-window warmup

    files = [_fresh_file(tmp_path, f"b{i}.txt", f"line {i}\n") for i in range(11)]

    # Warm the terminal env once (env creation is separate one-time cost).
    handle_function_call("read_file", {"path": files[0]})

    for i in range(1, 11):
        with timing_context.measure("warmup_bench"):
            handle_function_call("read_file", {"path": files[i]})

    summary = timing_context.summary().get("warmup_bench", {})
    durations = summary.get("durations", [])
    print(f"\n  10 warmed dispatches (ms): {[round(d, 3) for d in durations]}")
    # No multi-second cold outlier survives the warmup, and the loop is fast.
    assert summary.get("max_ms", 0) < 100, f"a warmed dispatch spiked to {summary.get('max_ms')}ms"
    assert summary.get("mean_ms", 0) < 50, f"warmed dispatch mean {summary.get('mean_ms')}ms (expected < 50ms)"


@pytest.mark.perf
def test_warm_dispatch_path_idempotent():
    """warm_dispatch_path warms the process-wide cache and is idempotent.

    Synchronous warm returns None and populates the get_tool_definitions cache;
    a background warm for a fresh fingerprint spawns exactly one daemon thread;
    a repeat warm for the same fingerprint is a no-op (already warmed).
    """
    from model_tools import (
        warm_dispatch_path,
        get_tool_definitions,
        _reset_dispatch_warm_state,
    )

    _reset_dispatch_warm_state()

    # Synchronous warm → None, and the schema catalog is now built.
    assert warm_dispatch_path(background=False) is None
    assert len(get_tool_definitions(quiet_mode=True)) > 0

    # Background warm for a fresh fingerprint spawns a joinable daemon thread.
    _reset_dispatch_warm_state()
    thread = warm_dispatch_path(enabled_toolsets=["file"], background=True)
    assert thread is not None
    assert thread.daemon is True
    thread.join(timeout=30)
    assert not thread.is_alive()

    # The "file" toolset really warmed (read_file schema is available).
    file_defs = get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
    assert any(t["function"]["name"] == "read_file" for t in file_defs)

    # Idempotent: same fingerprint again is a no-op → no new thread.
    assert warm_dispatch_path(enabled_toolsets=["file"], background=True) is None
