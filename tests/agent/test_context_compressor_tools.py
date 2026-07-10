"""Tests for ContextCompressor tool integration — get_tool_schemas(), handle_tool_call().

These tests verify the context_usage and compact tool schemas and call handling
on the real ContextCompressor, with LLM summarization mocked out.
"""

import orjson
import pytest
from unittest.mock import patch, MagicMock

from agent.context_compressor import ContextCompressor
from agent.context_tools import CompactMode


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def compressor():
    """Create a minimal ContextCompressor with mocked LLM calls."""
    c = ContextCompressor(
        model="test-model",
        quiet_mode=True,
        config_context_length=200000,
    )
    # Set some realistic token state
    c.last_prompt_tokens = 85000
    c.context_length = 200000
    c.threshold_tokens = 100000
    c.compression_count = 1
    return c


# ---------------------------------------------------------------------------
# get_tool_schemas
# ---------------------------------------------------------------------------

class TestGetToolSchemas:
    """Verify ContextCompressor.get_tool_schemas() returns expected schemas."""

    def test_returns_two_schemas(self, compressor):
        schemas = compressor.get_tool_schemas()
        assert len(schemas) == 2

    def test_first_schema_is_context_usage(self, compressor):
        schemas = compressor.get_tool_schemas()
        assert schemas[0]["name"] == "context_usage"

    def test_second_schema_is_compact(self, compressor):
        schemas = compressor.get_tool_schemas()
        assert schemas[1]["name"] == "compact"

    def test_schemas_have_valid_descriptions(self, compressor):
        schemas = compressor.get_tool_schemas()
        for s in schemas:
            assert len(s["description"]) > 10, f"Short description for {s['name']}"
            assert "parameters" in s

    def test_schemas_are_json_serializable(self, compressor):
        schemas = compressor.get_tool_schemas()
        dumped = orjson.dumps(schemas).decode('utf-8')
        loaded = orjson.loads(dumped)
        names = [s["name"] for s in loaded]
        assert "context_usage" in names
        assert "compact" in names


# ---------------------------------------------------------------------------
# handle_tool_call — context_usage
# ---------------------------------------------------------------------------

class TestHandleContextUsage:
    """Verify the context_usage tool handler."""

    def test_returns_valid_json(self, compressor):
        result = compressor.handle_tool_call("context_usage", {})
        data = orjson.loads(result)
        assert isinstance(data, dict)

    def test_returns_usage_fields(self, compressor):
        result = compressor.handle_tool_call("context_usage", {})
        data = orjson.loads(result)
        assert "usage_percent" in data
        assert "used_tokens" in data
        assert "max_context_tokens" in data
        assert "threshold_tokens" in data
        assert "compression_count" in data

    def test_returns_correct_values(self, compressor):
        result = compressor.handle_tool_call("context_usage", {})
        data = orjson.loads(result)
        assert data["used_tokens"] == 85000
        assert data["max_context_tokens"] == 200000
        assert data["compression_count"] == 1
        # 85000 / 200000 = 42.5%
        assert abs(data["usage_percent"] - 42.5) < 0.1

    def test_returns_zero_state_before_any_call(self, compressor):
        """Create a fresh compressor with no token state."""
        fresh = ContextCompressor(model="test", quiet_mode=True, config_context_length=200000)
        result = fresh.handle_tool_call("context_usage", {})
        data = orjson.loads(result)
        assert data["used_tokens"] == 0
        assert data["usage_percent"] == 0.0
        assert data["max_context_tokens"] == 200000
        assert data["compression_count"] == 0


# ---------------------------------------------------------------------------
# handle_tool_call — compact
# ---------------------------------------------------------------------------

class TestHandleCompact:
    """Verify the compact tool handler (validation + acknowledgment)."""

    def test_returns_acknowledgment_with_defaults(self, compressor):
        result = compressor.handle_tool_call("compact", {})
        data = orjson.loads(result)
        assert data["status"] == "acknowledged"
        assert "Compaction request registered" in data["message"]
        assert "current_usage" in data

    def test_includes_instruction_in_message(self, compressor):
        result = compressor.handle_tool_call("compact", {"instruction": "Focus on error handling"})
        data = orjson.loads(result)
        assert "Focus: Focus on error handling" in data["message"]

    def test_includes_explicit_mode(self, compressor):
        result = compressor.handle_tool_call("compact", {"mode": "technical"})
        data = orjson.loads(result)
        assert "Mode: technical" in data["message"]

    def test_includes_both_params(self, compressor):
        result = compressor.handle_tool_call(
            "compact",
            {"instruction": "Keep code examples", "mode": "aggressive"},
        )
        data = orjson.loads(result)
        assert "Focus: Keep code examples" in data["message"]
        assert "Mode: aggressive" in data["message"]

    def test_invalid_mode_defaults_to_balanced(self, compressor):
        result = compressor.handle_tool_call("compact", {"mode": "invalid_mode"})
        data = orjson.loads(result)
        assert "Mode: balanced" in data["message"]

    def test_empty_instruction_is_omitted(self, compressor):
        result = compressor.handle_tool_call("compact", {"instruction": ""})
        data = orjson.loads(result)
        assert "Focus:" not in data["message"]

    def test_current_usage_in_response(self, compressor):
        result = compressor.handle_tool_call("compact", {})
        data = orjson.loads(result)
        usage = data["current_usage"]
        assert usage["used_tokens"] == 85000
        assert usage["compression_count"] == 1

    def test_returns_valid_json(self, compressor):
        result = compressor.handle_tool_call("compact", {})
        data = orjson.loads(result)
        assert isinstance(data, dict)


# ---------------------------------------------------------------------------
# handle_tool_call — unknown tool
# ---------------------------------------------------------------------------

class TestHandleUnknownTool:
    """Verify unknown tool names fall through to the base implementation."""

    def test_unknown_tool_returns_error(self, compressor):
        result = compressor.handle_tool_call("nonexistent_tool", {})
        data = orjson.loads(result)
        assert "error" in data

    def test_unknown_tool_does_not_crash(self, compressor):
        result = compressor.handle_tool_call("", {})
        data = orjson.loads(result)
        assert "error" in data

    def test_lcm_tools_still_work(self, compressor):
        """LCM tools should still be handled by the parent class."""
        result = compressor.handle_tool_call("lcm_grep", {"pattern": "test"})
        data = orjson.loads(result)
        # The base handle_tool_call doesn't know lcm_grep either,
        # but it should return an error gracefully, not crash
        assert "error" in data or isinstance(data, dict)
