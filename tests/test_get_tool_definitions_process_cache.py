"""Process-wide tool-definitions cache (P0 optimization).

``model_tools.get_tool_definitions()`` memoizes its ``(schema_list,
status_lines)`` result **process-wide**, keyed on the toolset selection +
registry generation + config fingerprint + shell type. The first call builds
the whole catalog (lazy-imports the tool modules, probes each ``check_fn``,
constructs + sanitizes the JSON schemas); every subsequent call for the same
selection returns a fresh copy of the cached schemas in near-zero time — in
BOTH ``quiet_mode`` and non-quiet callers. ``quiet_mode`` is deliberately not
part of the key: the schema list is identical either way, only the stdout
side effect (tool-selection status lines) differs, so the entry carries the
captured status lines and a non-quiet hit replays them. That matters because
``AIAgent.__init__`` passes ``agent.quiet_mode``, i.e. the CLI/TUI path is a hot
caller of this function as well.

Root cause pinned here (see ``reports/perf/root-cause-analysis.md`` hotspots
#8/#9): the cache key captured ``registry._generation`` **before**
``_compute_tool_definitions`` triggered the lazy tool-module imports that bump
it, so the first cached entry was stored under an already-stale generation and
the *immediately following* call missed and rebuilt (~12 ms), leaving a dead
entry behind. Keying the stored entry on the post-compute (settled) generation
makes the second call a hit (<1 ms) — the "second agent init is effectively
free" property the plan targets.

These are behaviour contracts (cache-hit / invalidation invariants), not
wall-clock snapshots; the timing benchmarks live in
``tests/performance/test_agent_init.py``.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import orjson
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


def _cached_pair():
    """The single memo entry, as the ``(schema_list, status_lines)`` pair."""
    assert len(model_tools._tool_defs_cache) == 1, (
        f"expected exactly one memo entry, got {len(model_tools._tool_defs_cache)}"
    )
    return next(iter(model_tools._tool_defs_cache.values()))


class TestProcessWideToolDefinitionsCache:

    def test_first_call_leaves_a_single_live_entry(self):
        """One call must leave exactly ONE entry, keyed on the *settled* registry
        generation — not a stale pre-import-generation entry that the next call
        misses and that lingers as a dead entry until LRU eviction. This is the
        direct guard for the generation-keying bug."""
        model_tools.get_tool_definitions(quiet_mode=True)
        assert len(model_tools._tool_defs_cache) == 1
        settled_generation = registry._generation
        stored_generation = next(iter(model_tools._tool_defs_cache))[3]
        assert stored_generation == settled_generation, (
            "the memo entry was stored under a stale pre-compute generation "
            f"({stored_generation} != {settled_generation}) — the next call will "
            "rebuild and leave a dead entry behind"
        )
        # A second call must not add the dead entry back.
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
        the entry and a following non-quiet call is served from it (no rebuild)
        while still returning identical schemas and still emitting the
        tool-selection status lines (replayed from the cached copy)."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        with _spy_compute() as spy:
            nonquiet = model_tools.get_tool_definitions(
                enabled_toolsets=["file"], quiet_mode=False,
            )
        spy.assert_not_called()
        assert len(model_tools._tool_defs_cache) == 1
        quiet = model_tools.get_tool_definitions(
            enabled_toolsets=["file"], quiet_mode=True,
        )
        assert _names(nonquiet) == _names(quiet)

    def test_nonquiet_hit_replays_the_status_lines(self, capsys):
        """The CLI/TUI path must keep showing the tool-selection lines even when
        it is served from the memo warmed by a (silent) quiet caller."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        capsys.readouterr()
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=False)
        out = capsys.readouterr().out
        assert "Final tool selection" in out, (
            "a non-quiet cache hit must replay the captured status lines"
        )

    def test_nonquiet_first_then_quiet_also_hits(self):
        """Symmetric to the above: a non-quiet call warms the same entry a
        subsequent quiet caller reuses."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=False)
        assert len(model_tools._tool_defs_cache) == 1, (
            "a non-quiet call must populate the shared memo entry"
        )
        with _spy_compute() as spy:
            model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        spy.assert_not_called()

    def test_entry_carries_the_status_lines_even_when_warmed_quietly(self):
        """The lines are captured unconditionally at compute time: an entry warmed
        by a quiet caller must still be replayable for a later non-quiet caller
        (the CLI banner warmup is quiet, the agent init is not)."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        schemas, status_lines = _cached_pair()
        assert schemas, "no schemas memoized — test setup is wrong"
        assert any("Final tool selection" in line for line in status_lines), (
            f"status lines were not captured with the entry: {status_lines!r}"
        )

    def test_definition_json_bytes_are_stable_across_calls(self):
        """Prompt caching is sacred: the tool schemas shipped in the request body
        must stay byte-identical for the same selection across calls in one
        process, whether the call is a miss, a quiet hit or a non-quiet hit."""
        first = model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        second = model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        third = model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=False)
        assert orjson.dumps(first) == orjson.dumps(second) == orjson.dumps(third)
        schemas, _status = _cached_pair()
        assert orjson.dumps(schemas) == orjson.dumps(first)

    def test_entry_registered_during_compute_is_keyed_on_the_settled_generation(self):
        """Deterministic, session-independent guard for the generation-keying bug.

        Resolving a selection for the first time registers its tool modules and
        every ``register()`` bumps ``registry._generation`` — so a key captured
        *before* compute is stale the moment the entry is stored: the next call
        misses, rebuilds and leaves a dead entry behind. Reproduced here by
        bumping the generation from inside the (wrapped) compute step, which is
        what the lazy imports do in a cold process.
        """
        real_compute = model_tools._compute_tool_definitions

        def compute_then_register(*args, **kwargs):
            result = real_compute(*args, **kwargs)
            registry._generation += 1  # a lazy tool module registering itself
            return result

        with patch.object(model_tools, "_compute_tool_definitions", compute_then_register):
            model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        assert len(model_tools._tool_defs_cache) == 1
        stored_generation = next(iter(model_tools._tool_defs_cache))[3]
        assert stored_generation == registry._generation, (
            "the entry was stored under the pre-compute generation "
            f"({stored_generation} != {registry._generation}) — the next call "
            "will miss and rebuild"
        )
        with _spy_compute() as spy:
            model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        spy.assert_not_called()
        assert len(model_tools._tool_defs_cache) == 1, (
            "a dead entry was left behind by the stale pre-compute key"
        )

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

    model_tools._clear_tool_defs_cache()
    with patch("run_agent.OpenAI") as mock_cls:
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


def test_nonquiet_agent_inits_reuse_the_catalog(offline_agent_factory):
    """The CLI/TUI path forwards ``quiet_mode=False`` (``AIAgent.__init__`` passes
    ``agent.quiet_mode``) and must still hit the process-wide memo: a steady-state
    CLI construction reuses the catalog instead of rebuilding it, and the memoized
    status lines are replayed so the selection is still shown."""
    AIAgent = offline_agent_factory
    kwargs = {**_AGENT_KW, "quiet_mode": False}

    AIAgent(**kwargs)
    AIAgent(**kwargs)
    AIAgent(**kwargs)  # third construction — generation is settled by now
    with _spy_compute() as spy:
        agent_settled = AIAgent(**kwargs)
    assert agent_settled.tools, "agent built no tools — test setup is wrong"
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
