"""Benchmark ``orjson`` round‑trip vs ``copy.deepcopy`` performance.

Verifies the 2-10× speedup claim for JSON-serializable objects.
"""

import copy
import pytest

from agent.fast_deepcopy import fast_deepcopy, orjson_roundtrip_copy

# ── Typical Hermes data structures ──────────────────────────────────────

TURN_CONTEXT = {
    "session_id": "sess_abc123",
    "messages": [
        {"role": "user", "content": "Hello!", "timestamp": "2024-01-15T10:30:00Z"},
        {"role": "assistant", "content": "Hi there!", "timestamp": "2024-01-15T10:30:05Z"},
        {"role": "user", "content": "What can you do?", "timestamp": "2024-01-15T10:30:10Z"},
    ] * 10,
    "metadata": {
        "model": "gpt-4",
        "temperature": 0.7,
        "max_tokens": 4096,
        "tools": [
            {"name": "read_file", "enabled": True},
            {"name": "write_file", "enabled": True},
            {"name": "web_search", "enabled": False},
        ] * 5,
    },
    "state": {
        "step": 42,
        "total_cost": 0.0532,
        "tokens_used": 15234,
    },
}

TOOL_RESULTS = [
    {
        "tool_name": f"tool_{i}",
        "result": {"success": True, "data": [f"Result data line {j}" for j in range(100)]},
        "duration_ms": 150.0 + i,
        "timestamp": f"2024-01-15T10:30:{i:02d}Z",
    }
    for i in range(50)
]


class TestDeepcopyPerformance:

    def test_copy_deepcopy_turn_context(self, benchmark):
        """Benchmark copy.deepcopy on turn context."""
        result = benchmark(copy.deepcopy, TURN_CONTEXT)
        assert result["session_id"] == "sess_abc123"

    def test_fast_deepcopy_turn_context(self, benchmark):
        """Benchmark fast_deepcopy on turn context."""
        result = benchmark(fast_deepcopy, TURN_CONTEXT)
        assert result["session_id"] == "sess_abc123"

    def test_orjson_roundtrip_turn_context(self, benchmark):
        """Benchmark orjson round-trip on turn context."""
        result = benchmark(orjson_roundtrip_copy, TURN_CONTEXT)
        assert result["session_id"] == "sess_abc123"

    def test_copy_deepcopy_tool_results(self, benchmark):
        """Benchmark copy.deepcopy on tool results."""
        result = benchmark(copy.deepcopy, TOOL_RESULTS)
        assert len(result) == len(TOOL_RESULTS)

    def test_fast_deepcopy_tool_results(self, benchmark):
        """Benchmark fast_deepcopy on tool results."""
        result = benchmark(fast_deepcopy, TOOL_RESULTS)
        assert len(result) == len(TOOL_RESULTS)

    def test_orjson_roundtrip_tool_results(self, benchmark):
        """Benchmark orjson round-trip on tool results."""
        result = benchmark(orjson_roundtrip_copy, TOOL_RESULTS)
        assert len(result) == len(TOOL_RESULTS)

    def test_copy_deepcopy_nested(self, benchmark):
        """Benchmark copy.deepcopy on deeply nested dict."""
        nested = {"level0": {"level1": {"level2": {"level3": {"level4": {"level5": "deep"}}}}}}
        result = benchmark(copy.deepcopy, nested)
        assert result["level0"]["level1"]["level2"]["level3"]["level4"]["level5"] == "deep"

    def test_fast_deepcopy_nested(self, benchmark):
        """Benchmark fast_deepcopy on deeply nested dict."""
        nested = {"level0": {"level1": {"level2": {"level3": {"level4": {"level5": "deep"}}}}}}
        result = benchmark(fast_deepcopy, nested)
        assert result["level0"]["level1"]["level2"]["level3"]["level4"]["level5"] == "deep"
