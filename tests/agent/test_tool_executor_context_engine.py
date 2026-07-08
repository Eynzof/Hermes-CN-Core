"""Tests for context engine tool dispatch — compact and context_usage.

These tests verify the inline dispatch logic in tool_executor.py by testing
the key behavior directly: that compact calls _compress_context with correct
args, and context_usage returns usage status.

We test at the tool-executor level by exercising the exact code path that
was added, keeping the mock surface minimal.
"""

import orjson
from types import SimpleNamespace
from unittest.mock import MagicMock, patch, call

import pytest

from agent.tool_executor import execute_tool_calls_sequential


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def agent():
    """Create a MagicMock with minimal setup needed to reach context engine dispatch."""
    a = MagicMock()
    a._context_engine_tool_names = {"context_usage", "compact"}
    a._interrupt_requested = False
    a.tool_delay = 0
    a._quiet_mode = True
    a._cached_system_prompt = "You are helpful."
    a.config_context_length = 200000

    # Compressor
    a.context_compressor = MagicMock()
    a.context_compressor.handle_tool_call.return_value = orjson.dumps({
        "status": "acknowledged",
        "message": "ok",
        "current_usage": {},
    }).decode('utf-8')
    a.context_compressor.compression_count = 0

    # Bypass guardrails — return a decision that allows execution
    a._tool_guardrails = SimpleNamespace(
        before_call=lambda n, a: SimpleNamespace(
            allows_execution=True,
            decision="proceed",
            message="",
        )
    )

    # Keep the pipeline transparent
    a._append_guardrail_observation = lambda name, args, result, failed: result
    a._tool_result_content_for_active_model = lambda name, result: result

    # Default compress: return shorter list to simulate compression
    a._compress_context.side_effect = lambda msgs, sysp, **kw: (
        [{"role": "system", "content": "compressed"}],
        "New system prompt",
    )

    # Flush/spinner no-ops
    a._vprint = MagicMock()
    a._flush_messages_to_session_db = MagicMock()
    a._save_tool_result_to_trajectory = MagicMock()
    a._emit_provider_log = MagicMock()
    a._checkpoint_now = MagicMock()
    a._run_post_tool_call_hooks = MagicMock()
    a._print_fn = print
    a._should_emit_quiet_tool_messages.return_value = False

    # Touch activity no-op
    a._memory_manager = None
    a._touch_activity = MagicMock()
    a._current_tool = None
    a._record_file_mutation_result = MagicMock()
    a._tool_dedup_tracker = SimpleNamespace(
        check_and_register=lambda n, a: SimpleNamespace(reminder_text=None)
    )
    a._safe_print = MagicMock()
    a._wrap_verbose = lambda *a, **kw: ""
    a._tool_search_scope_cache = None
    a._subdirectory_hints = SimpleNamespace(check_tool_call=lambda n, a: None)
    a._apply_pending_steer_to_tool_results = MagicMock()
    a._is_multimodal_tool_result = lambda r: False

    return a


def _mock_tool_call(name="web_search", arguments="{}", call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestCompactDispatch:
    """Verify the compact tool's inline dispatch logic."""

    def test_calls_compress_context_with_force_and_mode(self, agent):
        """Compact must call _compress_context with force=True and mode forwarded."""
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "status": "acknowledged",
            "message": "Compaction request registered. Focus: Fix bugs. Mode: technical.",
            "current_usage": {},
        }).decode('utf-8')

        tool_calls = [_mock_tool_call(
            name="compact",
            arguments=orjson.dumps({"instruction": "Fix bugs", "mode": "technical"}).decode('utf-8'),
            call_id="c1",
        )]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = [{"role": "user", "content": "hello"}]

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        agent._compress_context.assert_called_once()
        kw = agent._compress_context.call_args[1]
        assert kw.get("force") is True
        assert kw.get("mode") == "technical"
        assert kw.get("focus_topic") == "Fix bugs"

    def test_default_mode_is_balanced(self, agent):
        """Compact with empty args should use balanced mode."""
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "status": "acknowledged",
            "message": "Compaction request registered. Mode: balanced.",
            "current_usage": {},
        }).decode('utf-8')

        tool_calls = [_mock_tool_call(name="compact", arguments="{}", call_id="c2")]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = [{"role": "user", "content": "hello"}]

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        kw = agent._compress_context.call_args[1]
        assert kw.get("mode") == "balanced"

    def test_messages_replaced_on_success(self, agent):
        """When compression returns fewer messages, the list must be replaced in-place."""
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "status": "acknowledged", "message": "ok", "current_usage": {},
        }).decode('utf-8')
        # Return 0 messages to make it clearly shorter than the original 1
        agent._compress_context.side_effect = lambda msgs, sysp, **kw: (
            [],  # shorter than original 1
            "New system prompt",
        )

        tool_calls = [_mock_tool_call(name="compact", arguments="{}", call_id="c3")]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = [{"role": "user", "content": "hello"}]

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        # Original message should be gone — messages list was cleared and replaced
        # with empty list (our mock return) then the tool result was appended
        assert len(messages) == 1  # just the tool result
        assert messages[0]["role"] == "tool"

    def test_noop_when_no_savings(self, agent):
        """Same-length list from compress should yield status='noop'."""
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "status": "acknowledged", "message": "ok", "current_usage": {},
        }).decode('utf-8')
        agent._compress_context.side_effect = lambda msgs, sysp, **kw: (
            list(msgs),  # same length
            "Same system prompt",
        )

        tool_calls = [_mock_tool_call(name="compact", arguments="{}", call_id="c4")]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = [{"role": "user", "content": "hello"}]

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        tool_results = [m for m in messages if m.get("role") == "tool"]
        data = orjson.loads(tool_results[0]["content"])
        assert data["status"] == "noop"

    def test_compress_error_returns_error_status(self, agent):
        """Exception from _compress_context should be caught and returned as error."""
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "status": "acknowledged", "message": "ok", "current_usage": {},
        }).decode('utf-8')
        agent._compress_context.side_effect = RuntimeError("Service down")

        tool_calls = [_mock_tool_call(
            name="compact",
            arguments=orjson.dumps({"mode": "aggressive"}).decode('utf-8'),
            call_id="c5",
        )]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = [{"role": "user", "content": "hello"}]

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        tool_results = [m for m in messages if m.get("role") == "tool"]
        data = orjson.loads(tool_results[0]["content"])
        assert data["status"] == "error"
        assert "Compaction failed" in data["error"]


class TestContextUsageDispatch:
    """Verify the context_usage tool dispatch."""

    def test_returns_usage_status(self, agent):
        agent.context_compressor.get_usage_status.return_value = {
            "usage_percent": 42.5,
            "used_tokens": 85000,
            "max_context_tokens": 200000,
            "threshold_tokens": 100000,
            "compression_count": 2,
        }
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "usage_percent": 42.5,
            "used_tokens": 85000,
            "max_context_tokens": 200000,
            "threshold_tokens": 100000,
            "compression_count": 2,
        }).decode('utf-8')

        tool_calls = [_mock_tool_call(name="context_usage", call_id="cu1")]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = []

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        tool_results = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_results) == 1
        data = orjson.loads(tool_results[0]["content"])
        assert data["used_tokens"] == 85000
        assert abs(data["usage_percent"] - 42.5) < 0.1

    def test_unknown_context_engine_tool_returns_error(self, agent):
        """Use a tool name that IS in _context_engine_tool_names but returns an error."""
        # Add additional name to the context engine tool set
        agent._context_engine_tool_names = {"context_usage", "compact", "unknown_ce_tool"}
        agent.context_compressor.handle_tool_call.return_value = orjson.dumps({
            "error": "Unknown tool: unknown_ce_tool",
        }).decode('utf-8')

        tool_calls = [_mock_tool_call(name="unknown_ce_tool", call_id="err1")]
        msg = SimpleNamespace(content="", tool_calls=tool_calls)
        messages = []

        execute_tool_calls_sequential(agent, msg, messages, "task-1")

        tool_results = [m for m in messages if m.get("role") == "tool"]
        assert len(tool_results) == 1
        data = orjson.loads(tool_results[0]["content"])
        assert "error" in data
