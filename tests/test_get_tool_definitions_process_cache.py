"""Process-wide tool-definitions cache (P0 optimization).

``model_tools.get_tool_definitions()`` memoizes its ``(schema_list,
status_lines)`` result **process-wide**, keyed on the toolset selection +
registry generation + config fingerprint + shell type. The first call builds
the whole catalog (lazy-imports the tool modules, probes each ``check_fn``,
constructs + sanitizes the JSON schemas); every subsequent call for the same
selection returns a fresh copy of the cached schemas in near-zero time — in
BOTH ``quiet_mode`` and non-quiet callers.

Root cause pinned here (see ``reports/perf/root-cause-analysis.md`` hotspots
#8/#9): the cache key captured ``registry._generation`` **before**
``_compute_tool_definitions`` triggered the lazy tool-module imports that bump
it, so the first cached entry was stored under an already-stale generation and
the *immediately following* call missed and rebuilt (~12 ms). Keying the stored
entry on the post-compute (settled) generation makes the second call a hit
(<1 ms) — the "second agent init is effectively free" property the plan targets.

These are behaviour contracts (cache-hit / invalidation invariants), not
wall-clock snapshots; the timing benchmarks live in
``tests/performance/test_agent_init.py``.
"""
from __future__ import annotations

import time
from unittest.mock import MagicMock, patch

import pytest

import model_tools
from tools.registry import registry


@pytest.fixture(autouse=True)
def _clear_cache():
    """Every test starts and ends with an empty tool-definitions cache."""
    model_tools._tool_defs_cache.clear()
    yield
    model_tools._tool_defs_cache.clear()


def _names(defs):
    return sorted(d["function"]["name"] for d in defs)


def _spy_compute():
    """Wrap ``_compute_tool_definitions`` so cache misses (rebuilds) can be
    counted without changing behaviour."""
    return patch.object(
        model_tools,
        "_compute_tool_definitions",
        wraps=model_tools._compute_tool_definitions,
    )


class TestProcessWideToolDefinitionsCache:

    def test_first_call_leaves_a_single_live_entry(self):
        """One call must leave exactly ONE cache entry — not a stale
        pre-import-generation entry PLUS a live post-import one. This is the
        direct guard for the generation-keying bug that left a dead entry
        behind and forced the next call to rebuild."""
        model_tools.get_tool_definitions(quiet_mode=True)
        assert len(model_tools._tool_defs_cache) == 1

    def test_second_call_is_a_cache_hit(self):
        """With the registry settled, the call right after the first is a hit:
        no rebuild, generation unchanged, identical content, still one entry."""
        first = model_tools.get_tool_definitions(quiet_mode=True)
        gen_settled = registry._generation
        with _spy_compute() as spy:
            second = model_tools.get_tool_definitions(quiet_mode=True)
        spy.assert_not_called()
        assert registry._generation == gen_settled
        assert len(model_tools._tool_defs_cache) == 1
        assert _names(first) == _names(second)
        # #17335: a fresh list object each time (never an alias of the cache).
        assert first is not second

    def test_cache_shared_between_quiet_and_nonquiet(self):
        """quiet_mode is deliberately NOT part of the key: a quiet call warms
        the entry and a following non-quiet call is served from it (no
        rebuild) while still returning identical schemas."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        with _spy_compute() as spy:
            nonquiet = model_tools.get_tool_definitions(
                enabled_toolsets=["file"], quiet_mode=False,
            )
        spy.assert_not_called()
        quiet = model_tools.get_tool_definitions(
            enabled_toolsets=["file"], quiet_mode=True,
        )
        assert _names(nonquiet) == _names(quiet)

    def test_nonquiet_first_then_quiet_also_hits(self):
        """Symmetric to the above: a non-quiet call warms the same entry a
        subsequent quiet caller reuses."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=False)
        with _spy_compute() as spy:
            model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        spy.assert_not_called()

    def test_generation_bump_invalidates(self):
        """A genuine registry mutation (generation bump) still forces a
        rebuild — the cache is correct, not merely fast."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        registry._generation += 1
        with _spy_compute() as spy:
            model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        spy.assert_called_once()

    def test_distinct_toolsets_get_distinct_entries(self):
        """Different toolset selections are memoized independently."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        model_tools.get_tool_definitions(enabled_toolsets=["web"], quiet_mode=True)
        assert len(model_tools._tool_defs_cache) == 2


# ---------------------------------------------------------------------------
# End-to-end: real AIAgent constructions reuse the process-wide catalog.
# ---------------------------------------------------------------------------

_AGENT_KW = dict(
    base_url="http://localhost:9/v1",   # unreachable discard port (no network)
    api_key="sk-test-mock-key",
    model="test/mock-model",
    max_iterations=5,
    quiet_mode=True,
    skip_context_files=True,
    skip_memory=True,
    enabled_toolsets=["file"],
)


@pytest.fixture
def offline_agent_factory():
    """Yield the ``AIAgent`` class with the model endpoint pinned unreachable
    and OpenAI mocked, so construction exercises the real tool-definitions path
    without any network I/O."""
    from run_agent import AIAgent
    import agent.model_metadata as mm

    model_tools._clear_tool_defs_cache()
    mm._reset_endpoint_reachable_cache()
    with patch("run_agent.OpenAI") as mock_cls, \
            patch.object(mm, "_endpoint_reachable", return_value=False):
        mock_cls.return_value = MagicMock()
        yield AIAgent


def test_get_tool_definitions_process_cache(offline_agent_factory):
    """Two agents built back-to-back share the process-wide tool catalog: the
    second agent's tool list is identical to the first. Once the registry has
    settled (one-time lazy plugin/MCP/alias registrations during the first
    constructions), further agent inits reuse the cached definitions and do
    NOT rebuild them — the "second agent init is free" success criterion.
    """
    AIAgent = offline_agent_factory

    agent1 = AIAgent(**_AGENT_KW)
    agent2 = AIAgent(**_AGENT_KW)
    assert agent1.tools, "agent built no tools — test setup is wrong"
    assert _names(agent1.tools) == _names(agent2.tools)

    # Warm past the one-time agent-init registrations that settle the registry
    # generation, then prove a steady-state construction reuses the cache with
    # zero schema rebuilds.
    AIAgent(**_AGENT_KW)  # third construction — generation is settled by now
    with _spy_compute() as spy:
        agent_settled = AIAgent(**_AGENT_KW)
    assert _names(agent_settled.tools) == _names(agent1.tools)
    spy.assert_not_called()


def test_many_agent_inits_do_not_rebuild_per_agent(offline_agent_factory):
    """Rebuilds are a small process-wide constant, NOT one-per-agent: building
    eight agents must trigger far fewer than eight schema rebuilds (in practice
    ≤ 2, once for the initial catalog and once as the registry settles)."""
    AIAgent = offline_agent_factory

    rebuilds = {"n": 0}
    real_compute = model_tools._compute_tool_definitions

    def _counting(*a, **k):
        rebuilds["n"] += 1
        return real_compute(*a, **k)

    with patch.object(model_tools, "_compute_tool_definitions", _counting):
        agents = [AIAgent(**_AGENT_KW) for _ in range(8)]

    assert all(a.tools for a in agents)
    assert rebuilds["n"] < 8, (
        f"tool schemas rebuilt {rebuilds['n']}x for 8 agents — the process "
        f"cache is not being reused across constructions"
    )
    assert rebuilds["n"] <= 3, (
        f"expected the catalog to be built ~once and settle, got "
        f"{rebuilds['n']} rebuilds"
    )
