"""Regression tests for issue #17335.

:func:`model_tools.get_tool_definitions` memoizes results process-wide to
avoid re-walking the registry on every agent construction. The memo now serves
both the Gateway ``quiet_mode=True`` path and the CLI/TUI ``quiet_mode=False``
path from one entry (replaying the status prints for the latter). The cached
object must NOT be aliased into callers' return values — long-lived Gateway
processes mutate the returned list (``run_agent`` appends memory and LCM
context-engine tool schemas to ``self.tools``), and a shared list would poison
subsequent agent inits with duplicate tool names. Providers that enforce
uniqueness (DeepSeek, Xiaomi MiMo, Moonshot/Kimi) then reject the API call
with HTTP 400.

These tests pin:
- the cache-hit path returns a fresh list (existing #17098 behavior)
- the first uncached call also returns a fresh list (the fix)
- every call returns a list that is not the cached one, even after mutation
- the cache is shared across quiet/non-quiet callers, replaying status prints
"""
from __future__ import annotations

import pytest

import model_tools


@pytest.fixture(autouse=True)
def _clear_cache():
    """Each test starts with an empty quiet_mode cache."""
    model_tools._tool_defs_cache.clear()
    yield
    model_tools._tool_defs_cache.clear()


class TestQuietModeCacheIsolation:

    def test_first_uncached_call_returns_fresh_list(self):
        """The first quiet_mode call must not alias the cached object \u2014
        otherwise a caller mutating the returned list mutates the cache."""
        first = model_tools.get_tool_definitions(quiet_mode=True)
        assert isinstance(first, list)
        # Find the cached value to compare identity. The cache stores a
        # (schema_list, status_lines) pair; the schema list is element [0].
        assert len(model_tools._tool_defs_cache) == 1
        cached_result, _status = next(iter(model_tools._tool_defs_cache.values()))
        assert first is not cached_result, (
            "issue #17335: first quiet_mode call returned the cached list "
            "by reference — mutations will leak into subsequent calls."
        )

    def test_cache_hit_returns_fresh_list(self):
        """The cache-hit path already returned a copy pre-fix; pin it."""
        first = model_tools.get_tool_definitions(quiet_mode=True)
        second = model_tools.get_tool_definitions(quiet_mode=True)
        assert first is not second
        cached_result, _status = next(iter(model_tools._tool_defs_cache.values()))
        assert second is not cached_result

    def test_caller_mutation_does_not_poison_cache(self):
        """Simulate run_agent appending LCM tool schemas to the returned
        list. A second call must NOT see those appended entries."""
        first = model_tools.get_tool_definitions(quiet_mode=True)
        baseline_len = len(first)
        # Caller mutates the returned list (this is what run_agent does
        # when it injects memory + context-engine tool schemas).
        first.append({"type": "function", "function": {"name": "lcm_grep"}})
        first.append({"type": "function", "function": {"name": "lcm_expand"}})

        second = model_tools.get_tool_definitions(quiet_mode=True)
        # Length must match the original \u2014 cache pollution would make
        # second 2 entries longer.
        assert len(second) == baseline_len, (
            f"issue #17335: cache was polluted by caller mutation. "
            f"first len={baseline_len}, mutated len={len(first)}, "
            f"second-call len={len(second)} \u2014 expected {baseline_len}."
        )
        names = [t.get("function", {}).get("name") for t in second]
        assert "lcm_grep" not in names
        assert "lcm_expand" not in names

    def test_repeated_caller_mutation_does_not_accumulate(self):
        """The original Gateway symptom: every agent init in a long-lived
        process appends LCM schemas, accumulating duplicates over time."""
        baseline = len(model_tools.get_tool_definitions(quiet_mode=True))
        for _ in range(5):
            tools = model_tools.get_tool_definitions(quiet_mode=True)
            tools.append({"type": "function", "function": {"name": "lcm_grep"}})
        final = model_tools.get_tool_definitions(quiet_mode=True)
        assert len(final) == baseline, (
            f"Cache accumulated mutations across {5} agent inits: "
            f"baseline={baseline}, final={len(final)}."
        )

    def test_cache_bounded_by_eviction(self):
        """The cache evicts the oldest entry when it reaches the cap,
        keeping the cache bounded instead of growing unbounded over a
        long-lived Gateway's lifetime (#19251)."""
        cap = model_tools._TOOL_DEFS_CACHE_MAX
        # Fill cache to the cap with distinct keys by varying enabled_toolsets.
        for i in range(cap):
            model_tools.get_tool_definitions(
                enabled_toolsets=[f"fake_toolset_{i}"], quiet_mode=True,
            )
        assert len(model_tools._tool_defs_cache) == cap

        # Adding one more must evict the oldest, not clear everything and
        # not grow past the cap.
        model_tools.get_tool_definitions(
            enabled_toolsets=["fake_toolset_overflow"], quiet_mode=True,
        )
        assert len(model_tools._tool_defs_cache) == cap, (
            "Eviction should keep the cache at the cap, not clear it or grow"
        )

    def test_non_quiet_mode_uses_cache_and_replays_status(self, capsys):
        """quiet_mode=False now shares the same process-wide memo as the
        Gateway path (the schema list is identical regardless of quiet_mode;
        only the status prints differ). A non-quiet call populates the cache,
        and a subsequent non-quiet call is served from it while STILL emitting
        the tool-selection status lines (replayed from the cached copy)."""
        first = model_tools.get_tool_definitions(
            enabled_toolsets=["file"], quiet_mode=False,
        )
        assert len(model_tools._tool_defs_cache) == 1
        out1 = capsys.readouterr().out
        assert "Final tool selection" in out1, (
            "non-quiet call must print the tool-selection status lines"
        )

        # Second non-quiet call is a cache hit but must still replay the
        # status lines so the CLI/TUI user keeps seeing them.
        second = model_tools.get_tool_definitions(
            enabled_toolsets=["file"], quiet_mode=False,
        )
        out2 = capsys.readouterr().out
        assert "Final tool selection" in out2
        # Same schema content, but a fresh list object (isolation preserved).
        assert [t["function"]["name"] for t in first] == [
            t["function"]["name"] for t in second
        ]
        assert first is not second

    def test_quiet_mode_hit_is_silent(self, capsys):
        """A quiet_mode cache hit must NOT emit status lines — the whole point
        of quiet_mode is suppressed stdout, even when a prior non-quiet call
        populated the shared cache entry."""
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        capsys.readouterr()  # drain the (possibly non-empty) miss-path output
        model_tools.get_tool_definitions(enabled_toolsets=["file"], quiet_mode=True)
        assert capsys.readouterr().out == ""
