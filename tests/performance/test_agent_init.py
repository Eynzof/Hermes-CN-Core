"""Performance benchmarks for agent initialization.

All tests run in OFFLINE mode — no real LLM API calls.
"""

import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.perf
def test_import_time_trace(timing_context, perf_output_dir):
    """Measure import cascade time for main agent module."""
    import subprocess
    cmd = [sys.executable, "-X", "importtime", "-c", "from run_agent import AIAgent"]

    with timing_context.measure("import_cascade"):
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)

    total_ms = timing_context.summary().get("import_cascade", {}).get("total_ms", 0)
    trace_path = perf_output_dir / "raw" / "import_timing.log"
    if result.stderr:
        trace_path.write_text(result.stderr, encoding="utf-8")

    print(f"\n  Import cascade time: {total_ms:.1f}ms")
    print(f"  Import trace saved to: {trace_path}")
    assert total_ms < 60000


# ---------------------------------------------------------------------------
# Lazy tool-import regression guard
# (reports/perf/root-cause-analysis.md hotspot #7 / .plans/02-Lazy-Tool-Imports.md)
#
# Historically ``from run_agent import AIAgent`` eagerly imported every
# ``tools/*.py`` (via model_tools' module-level discover_builtin_tools()),
# registering ~70 tools and dragging in the browser/playwright/image-gen deps
# — ~1,115 ms of import cascade paid on every process start, even for an agent
# whose enabled toolsets touch a handful of modules. Discovery is now deferred:
# the registry keeps a statically-scanned metadata index and imports a tool's
# module only when that tool is first requested, gated by the enabled toolsets.
#
# These guards run in a clean subprocess so the sys.modules snapshot is not
# polluted by other in-process tests that already imported tool modules.
# ---------------------------------------------------------------------------

_LAZY_IMPORT_PROBE = r'''
import sys, json, time

def self_registering_tool_modules_loaded():
    import tools.registry as R
    self_reg = set(R.build_tool_index()["modules"])
    return sorted(m for m in sys.modules if m in self_reg)

t0 = time.perf_counter()
from run_agent import AIAgent  # noqa: F401
import_ms = (time.perf_counter() - t0) * 1000
loaded_after_import = self_registering_tool_modules_loaded()

import model_tools

# Empty toolset resolves to zero tools and must not import any tool module.
empty_defs = model_tools.get_tool_definitions(enabled_toolsets=[], quiet_mode=True)
browser_after_empty = "tools.browser_tool" in sys.modules

# A specific toolset imports only its own module tree, never the browser tree.
file_defs = model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
file_loaded = "tools.file_tools" in sys.modules
browser_after_file = "tools.browser_tool" in sys.modules

print("RESULT " + json.dumps({
    "import_ms": import_ms,
    "self_reg_loaded_after_import": loaded_after_import,
    "empty_defs": len(empty_defs),
    "browser_after_empty": browser_after_empty,
    "file_defs": len(file_defs),
    "file_tools_loaded": file_loaded,
    "browser_after_file": browser_after_file,
}))
'''


@pytest.mark.perf
def test_import_time_lazy(timing_context):
    """Importing the agent must NOT trigger the built-in tool import cascade,
    and toolset selection must gate which tool modules get imported.
    """
    import subprocess
    import json

    with timing_context.measure("import_lazy"):
        result = subprocess.run(
            [sys.executable, "-c", _LAZY_IMPORT_PROBE],
            capture_output=True, text=True, timeout=120, cwd=str(PROJECT_ROOT),
        )
    assert result.returncode == 0, f"probe failed:\n{result.stderr[-2000:]}"
    lines = [ln for ln in result.stdout.splitlines() if ln.startswith("RESULT ")]
    assert lines, f"probe produced no RESULT line:\n{result.stdout[-2000:]}"
    data = json.loads(lines[-1][len("RESULT "):])
    print(f"\n  Lazy import probe: {data}")

    # THE core invariant: importing run_agent imports ZERO self-registering
    # tool modules. If this regresses, the whole tool tree is being pulled in
    # at import time again (the #7 import_cascade hotspot is back).
    assert data["self_reg_loaded_after_import"] == [], (
        "importing run_agent eagerly imported self-registering tool modules "
        f"{data['self_reg_loaded_after_import']} — the lazy tool-import "
        "optimization has regressed."
    )

    # Empty toolset -> no tools, and specifically not the (heaviest) browser tree.
    assert data["empty_defs"] == 0
    assert not data["browser_after_empty"], (
        "get_tool_definitions(enabled_toolsets=[]) imported the browser tool tree"
    )

    # Enabling only the 'file' toolset lazily loads file_tools but NOT browser.
    assert data["file_tools_loaded"], "the 'file' toolset did not load tools.file_tools"
    assert data["file_defs"] > 0, "the 'file' toolset returned no tool definitions"
    assert not data["browser_after_file"], (
        "enabling the 'file' toolset imported the browser tool tree — toolset "
        "gating is not limiting imports to the requested toolset"
    )

    # Generous wall-clock ceiling. The point of the test is the invariants
    # above (no cascade), not a hard latency number that depends on the host's
    # framework-import speed; this ceiling only catches a gross regression
    # while staying well under the pre-optimization ~1,115 ms cascade + the
    # unavoidable one-off framework import.
    assert data["import_ms"] < 20000, f"run_agent import took {data['import_ms']:.0f}ms"


@pytest.mark.perf
def test_agent_init_warm_start(timing_context):
    """Measure AIAgent initialization time (realistic baseline)."""
    from run_agent import AIAgent

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        with timing_context.measure("init_agent_warm"):
            agent = AIAgent(
                base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
                model="test/mock-model", max_iterations=5, quiet_mode=True,
                skip_context_files=True, skip_memory=True,
            )

    total_ms = timing_context.summary().get("init_agent_warm", {}).get("total_ms", 0)
    print(f"\n  Agent init: {total_ms:.1f}ms")
    assert agent is not None
    # Realistic threshold — includes tool discovery, plugin scanning, etc.
    assert total_ms < 120000, f"Agent init took {total_ms:.1f}ms (expected < 120s)"


@pytest.mark.perf
def test_agent_init_minimal_config(timing_context):
    """Measure AIAgent init with minimal config and no tools."""
    from run_agent import AIAgent

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        with timing_context.measure("init_agent_minimal"):
            agent = AIAgent(
                base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
                model="test/mock-model", max_iterations=2, quiet_mode=True,
                skip_context_files=True, skip_memory=True, enabled_toolsets=[],
            )

    total_ms = timing_context.summary().get("init_agent_minimal", {}).get("total_ms", 0)
    print(f"\n  Agent init (minimal): {total_ms:.1f}ms")
    assert total_ms < 90000, f"Minimal agent init took {total_ms:.1f}ms (expected < 90s)"


@pytest.mark.perf
def test_get_tool_definitions_timing(timing_context):
    """Measure get_tool_definitions() with realistic tools."""
    from model_tools import get_tool_definitions

    with timing_context.measure("get_tool_definitions_first"):
        tools = get_tool_definitions()
    with timing_context.measure("get_tool_definitions_cached"):
        tools2 = get_tool_definitions()

    summary = timing_context.summary()
    first_ms = summary.get("get_tool_definitions_first", {}).get("total_ms", 0)
    cached_ms = summary.get("get_tool_definitions_cached", {}).get("total_ms", 0)
    print(f"\n  Tool defs (first): {first_ms:.1f}ms, (cached): {cached_ms:.1f}ms")
    assert len(tools) > 0


@pytest.mark.perf
def test_plugin_discovery_timing(timing_context, tmp_path):
    """Measure plugin discovery overhead."""
    from hermes_cli.plugins import PluginManager

    mgr = PluginManager()
    # Use introspection to discover available plugins
    with timing_context.measure("plugin_discovery"):
        # PluginManager stores discovered plugins internally
        _ = mgr.get_plugins() if hasattr(mgr, 'get_plugins') else []

    total_ms = timing_context.summary().get("plugin_discovery", {}).get("total_ms", 0)
    print(f"\n  Plugin discovery: {total_ms:.1f}ms")
    assert total_ms < 5000


@pytest.mark.perf
def test_session_db_connection_timing(timing_context, tmp_path):
    """Measure session database initialization time."""
    from hermes_state import SessionDB

    db_path = tmp_path / "test_session.db"

    with timing_context.measure("session_db_connect"):
        # SessionDB connects on init
        db = SessionDB(db_path=db_path)

    total_ms = timing_context.summary().get("session_db_connect", {}).get("total_ms", 0)
    print(f"\n  Session DB init: {total_ms:.1f}ms")
    assert total_ms < 1000


@pytest.mark.perf
@pytest.mark.perf_baseline
def test_agent_init_baseline(timing_context):
    """Baseline measurement for agent initialization."""
    from run_agent import AIAgent

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        with timing_context.measure("init_agent_full"):
            agent = AIAgent(
                base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
                model="test/mock-model", max_iterations=10, quiet_mode=True,
                skip_context_files=True, skip_memory=True,
            )

    summary = timing_context.summary()
    total_ms = summary.get("init_agent_full", {}).get("total_ms", 0)

    from tests.performance.conftest import save_baseline
    save_baseline("agent_init", {"total_ms": total_ms, "summary": summary})

    print(f"\n  Agent init (baseline): {total_ms:.1f}ms")
    assert total_ms < 120000, f"Agent init took {total_ms:.1f}ms"
    assert agent is not None

# ---------------------------------------------------------------------------
# Lazy cold-start regression guards (reports/perf/root-cause-analysis.md #1-4)
#
# Root cause: AIAgent.__init__ resolved the model's context length by
# synchronously probing the model endpoint (ContextCompressor ->
# get_model_context_length -> detect_local_server_type / query_ollama_* /
# fetch_endpoint_model_metadata). Against a down/placeholder endpoint each
# construction blocked ~40-66s on connect timeouts — paid on EVERY init, not
# just the first. The fix bounds + caches an endpoint-reachability verdict so
# an unreachable endpoint short-circuits the HTTP probing.
# ---------------------------------------------------------------------------

_COLD_INIT_KWARGS = dict(
    base_url="http://localhost:9/v1",  # unreachable placeholder (discard port)
    api_key="sk-test-mock-key",
    model="test/mock-model",
    max_iterations=5,
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
)


@pytest.mark.perf
def test_agent_init_cold_vs_warm(timing_context):
    """Cold init is sub-second and warm init is ~instant — no per-construction
    network probing.

    Three measurements:

    * ``init_cold`` — a fresh (first-in-process) construction with the
      endpoint's reachability verdict resolved promptly. On any host where a
      closed port refuses immediately this is the real path; here it is pinned
      via ``_endpoint_reachable`` so the number reflects Hermes's own init work
      rather than this machine's firewall/connect-timeout behaviour.
    * ``init_warm`` — a second construction reusing the process-wide caches.
    * ``init_warm_realprobe`` — two constructions against the REAL (unpinned)
      unreachable endpoint; the second must still be fast, proving the
      reachability verdict is cached instead of re-probed. Pre-fix this second
      construction cost ~40s.
    """
    from run_agent import AIAgent
    import agent.model_metadata as mm
    from model_tools import _clear_tool_defs_cache

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()

        # Target path: prompt reachability verdict (normal fast-refuse case).
        _clear_tool_defs_cache()
        mm._reset_endpoint_reachable_cache()
        with patch.object(mm, "_endpoint_reachable", return_value=False):
            with timing_context.measure("init_cold"):
                cold_agent = AIAgent(**_COLD_INIT_KWARGS)
            with timing_context.measure("init_warm"):
                warm_agent = AIAgent(**_COLD_INIT_KWARGS)

        # Real path: unpinned reachability against a genuinely down endpoint.
        # The first probe pays the (bounded) reachability check; the second must
        # hit the cached verdict and be fast — the sharp regression guard.
        _clear_tool_defs_cache()
        mm._reset_endpoint_reachable_cache()
        _ = AIAgent(**_COLD_INIT_KWARGS)  # primes reachability + tool caches
        with timing_context.measure("init_warm_realprobe"):
            realprobe_agent = AIAgent(**_COLD_INIT_KWARGS)

    summary = timing_context.summary()
    cold_ms = summary["init_cold"]["total_ms"]
    warm_ms = summary["init_warm"]["total_ms"]
    warm_realprobe_ms = summary["init_warm_realprobe"]["total_ms"]
    print(
        f"\n  Cold init: {cold_ms:.1f}ms | Warm init: {warm_ms:.1f}ms | "
        f"Warm (real probe): {warm_realprobe_ms:.1f}ms"
    )

    assert cold_agent is not None and warm_agent is not None and realprobe_agent is not None
    # Cold init builds the tool catalog once but must not block on the endpoint.
    # Target ~1s (measured ~0.5s); the 3s ceiling absorbs slow CI runners while
    # staying >20x below the pre-fix ~40-66s cold start.
    assert cold_ms < 3000, f"Cold init {cold_ms:.1f}ms (expected < 3s; target ~1s)"
    # Warm init reuses the caches — must be tiny (pre-fix re-probe cost ~40s).
    assert warm_ms < 1000, f"Warm init {warm_ms:.1f}ms (expected ~65ms)"
    # A second construction against the REAL down endpoint must NOT re-probe
    # (pre-fix ~40s). This is the sharpest guard for the eager-network hotspot.
    assert warm_realprobe_ms < 5000, (
        f"Warm init w/ real reachability {warm_realprobe_ms:.1f}ms — endpoint is "
        f"being re-probed on every construction (expected cached verdict, << 40s)"
    )
    # Warm reuse must be cheaper than the initial cold build.
    assert warm_ms <= cold_ms + 1.0


@pytest.mark.perf
def test_first_turn_latency(timing_context, mock_llm_response):
    """First user message (agent construction + one turn) completes quickly.

    Pre-fix this was ~55.5s (55s of blocking init + the turn). With lazy/bounded
    endpoint resolution the whole first-message flow is well under 2s. The turn
    uses a mocked LLM (offline); the guard is the end-to-end latency, not the
    model output.
    """
    from run_agent import AIAgent
    import agent.model_metadata as mm

    mock_response = mock_llm_response(content="Done.", finish_reason="stop")

    with patch("run_agent.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        # Match test_conversation_loop_baseline: keep shell detection out of the
        # measured window (prompt_builder probes shutil.which otherwise).
        import shutil as shutil_mod
        import agent.prompt_builder as pb
        pb.shutil = shutil_mod
        original_which = shutil_mod.which
        shutil_mod.which = MagicMock(return_value=None)

        mm._reset_endpoint_reachable_cache()
        try:
            with patch.object(mm, "_endpoint_reachable", return_value=False):
                with timing_context.measure("first_turn_total"):
                    agent = AIAgent(
                        base_url="http://localhost:9/v1", api_key="sk-test-mock-key",
                        model="test/mock-model", max_iterations=3, quiet_mode=True,
                        skip_context_files=True, skip_memory=True,
                        enabled_toolsets=["file"],
                    )
                    result = agent.chat("Test message")
        finally:
            shutil_mod.which = original_which

    total_ms = timing_context.summary().get("first_turn_total", {}).get("total_ms", 0)
    print(f"\n  First turn (init + turn): {total_ms:.1f}ms")
    assert agent is not None
    # chat() must return a string (the turn machinery ran to completion without
    # hanging), even under the offline mock.
    assert isinstance(result, str)
    # Init + first turn must be fast now. Target ~1.5s; 2.5s ceiling for CI
    # headroom — still >20x below the pre-fix ~55.5s.
    assert total_ms < 2500, f"First-turn latency {total_ms:.1f}ms (expected < 2.5s)"


# ---------------------------------------------------------------------------
# Process-wide tool-definitions cache (reports/perf/root-cause-analysis.md
# hotspots #8 dispatch_simple / #9 get_tool_definitions_first;
# .plans/03-Tool-Definitions-Cache.md)
#
# get_tool_definitions() memoizes its (schema_list, status_lines) result
# process-wide. The first call builds the whole catalog (lazy tool-module
# imports + per-tool check_fn probes + schema construction/sanitization); every
# later call for the same toolset selection returns a fresh copy of the cached
# schemas in near-zero time. The cache is keyed on the registry generation
# captured AFTER compute (post lazy-import), so the very next call is a hit
# rather than a stale-generation re-build (~12 ms pre-fix).
# ---------------------------------------------------------------------------


@pytest.mark.perf
def test_tool_definitions_process_cache_speedup(timing_context):
    """Second get_tool_definitions() is served from the process cache and is
    orders of magnitude cheaper than the first full build."""
    from model_tools import get_tool_definitions, _clear_tool_defs_cache
    from tools.registry import registry

    _clear_tool_defs_cache()
    with timing_context.measure("tool_defs_first"):
        first = get_tool_definitions(quiet_mode=True)
    # A run of cached retrievals — take the best to shrug off scheduler jitter.
    best_cached_ms = None
    for _ in range(5):
        with timing_context.measure("tool_defs_cached"):
            cached = get_tool_definitions(quiet_mode=True)
        got = timing_context.summary()["tool_defs_cached"]["min_ms"]
        best_cached_ms = got if best_cached_ms is None else min(best_cached_ms, got)

    summary = timing_context.summary()
    first_ms = summary.get("tool_defs_first", {}).get("total_ms", 0)
    print(f"\n  Tool defs first: {first_ms:.2f}ms | cached best: {best_cached_ms:.3f}ms")
    assert len(first) > 0 and len(cached) > 0
    # Exactly one live cache entry — no stale pre-import-generation duplicate.
    assert len(__import__("model_tools")._tool_defs_cache) == 1
    # A cache hit is sub-millisecond in practice; 5 ms absorbs slow CI runners.
    assert best_cached_ms < 5.0, (
        f"cached get_tool_definitions {best_cached_ms:.3f}ms (expected < 5ms) — "
        f"the process cache is not serving the second call"
    )
    # Forcing a generation bump must re-build (proves correctness, not staleness).
    registry._generation += 1
    with timing_context.measure("tool_defs_rebuild"):
        rebuilt = get_tool_definitions(quiet_mode=True)
    assert len(rebuilt) > 0


@pytest.mark.perf
def test_ten_agent_inits_reuse_tool_cache(timing_context):
    """Ten back-to-back AIAgent constructions stay cheap because the tool
    catalog is built once and reused process-wide.

    Pre-optimization each init rebuilt the schema catalog (~658 ms hotspot #9)
    on top of the cold-start tax, so ten inits ran ~40s+. With the process
    cache the warm inits are a small fraction of the first, and the whole loop
    lands well under the ~2s plan target (measured ~0.5s).
    """
    from run_agent import AIAgent
    import agent.model_metadata as mm
    from model_tools import _clear_tool_defs_cache

    kw = dict(
        base_url="http://localhost:9/v1", api_key="sk-test-mock-key",
        model="test/mock-model", max_iterations=5, quiet_mode=True,
        skip_context_files=True, skip_memory=True, enabled_toolsets=["file"],
    )

    per_agent = []
    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        _clear_tool_defs_cache()
        mm._reset_endpoint_reachable_cache()
        with patch.object(mm, "_endpoint_reachable", return_value=False):
            with timing_context.measure("ten_agent_inits"):
                agent = None
                for _ in range(10):
                    t0 = time.perf_counter()
                    agent = AIAgent(**kw)
                    per_agent.append((time.perf_counter() - t0) * 1000.0)

    total_ms = timing_context.summary().get("ten_agent_inits", {}).get("total_ms", 0)
    first_ms = per_agent[0]
    warm = per_agent[1:]
    warm_avg = sum(warm) / len(warm)
    print(
        f"\n  10 agent inits: total {total_ms:.0f}ms | first {first_ms:.0f}ms | "
        f"warm avg {warm_avg:.1f}ms (target total ~2s)"
    )
    assert agent is not None and all(t >= 0 for t in per_agent)
    # Aggregate ceiling: target ~2s (measured ~0.5s). 10s absorbs slow CI while
    # staying >4x below the pre-optimization ~40s+ for ten cold inits.
    assert total_ms < 10000, f"ten agent inits {total_ms:.0f}ms (target ~2s)"
    # The meaningful, jitter-proof invariant: warm inits reuse the catalog and
    # are a clear fraction of the first (which paid the one-time build).
    assert warm_avg < first_ms, (
        f"warm init avg {warm_avg:.1f}ms not faster than first {first_ms:.1f}ms "
        f"— the tool catalog is being rebuilt on every construction"
    )
