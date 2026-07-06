"""Regression tests for tool-call-only turns followed by more tool calls.

Pins the contract that the core conversation loop emits an explicit
``tool_calls_committed_callback`` before executing tools.  This gives the
gateway/Desktop UI a clean boundary for turns whose first assistant payload is
a tool call with no preceding text, preventing the state machine from getting
stuck between ``message.start`` and the first ``tool.start``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import pytest


def _mock_assistant_msg(content="", tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _mock_tool_call(name, arguments="{}", call_id=None):
    return SimpleNamespace(
        id=call_id or f"call_{name}",
        type="function",
        function=SimpleNamespace(name=name, arguments=arguments),
    )


def _mock_response(content="", finish_reason="stop", tool_calls=None):
    msg = _mock_assistant_msg(content=content, tool_calls=tool_calls)
    choice = SimpleNamespace(message=msg, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], model="test/model", usage=None)


@pytest.fixture()
def tool_loop_agent():
    """AIAgent wired for a two-tool turn without real tool side effects."""
    from run_agent import AIAgent

    with (
        patch("run_agent.get_tool_definitions", return_value=[]),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI"),
    ):
        agent = AIAgent(
            api_key="test-key-1234567890",
            base_url="https://openrouter.ai/api/v1",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
        )
        agent.client = MagicMock()
        agent._cached_system_prompt = "You are helpful."
        agent._use_prompt_caching = False
        agent.tool_delay = 0
        agent.compression_enabled = False
        agent.save_trajectories = False
        # Pretend the only tools we need are valid so the loop does not try to
        # repair or reject the mock tool calls.
        agent.valid_tool_names = {"write_file", "terminal"}
        return agent


def _fake_execute_tool_calls(assistant_message, messages, effective_task_id, api_call_count):
    """Stub tool execution: append a synthetic tool result for every call."""
    for tc in assistant_message.tool_calls:
        messages.append(
            {
                "role": "tool",
                "tool_call_id": tc.id,
                "name": tc.function.name,
                "content": '{"ok": true}',
            }
        )


class TestToolCallStreamingConvergence:
    """A tool-call-only assistant message must be committed before execution."""

    def test_tool_call_only_turn_emits_committed_callback(self, tool_loop_agent):
        """The first assistant payload is a write_file call with no text.

        The loop must call ``tool_calls_committed_callback`` once for that
        assistant message before ``_execute_tool_calls`` runs.
        """
        agent = tool_loop_agent
        agent.client.chat.completions.create.side_effect = [
            _mock_response(
                finish_reason="tool_calls",
                tool_calls=[_mock_tool_call("write_file", '{"path":"/tmp/x","content":"1"}')],
            ),
            _mock_response(content="Done.", finish_reason="stop"),
        ]

        committed_cb = Mock()
        delta_cb = Mock()
        agent.tool_calls_committed_callback = committed_cb
        agent.stream_delta_callback = delta_cb

        with patch.object(agent, "_execute_tool_calls", side_effect=_fake_execute_tool_calls):
            result = agent.run_conversation("write a file")

        assert "Done." in result["final_response"]
        committed_cb.assert_called_once()
        assistant_msg = committed_cb.call_args[0][0]
        assert assistant_msg["role"] == "assistant"
        assert [tc["id"] for tc in assistant_msg["tool_calls"]] == ["call_write_file"]

    def test_back_to_back_tool_calls_emit_two_committed_callbacks(self, tool_loop_agent):
        """A write_file call is followed by a terminal call; both assistant
        messages are tool-call-only.  The loop must commit each one before its
        tools execute.
        """
        agent = tool_loop_agent
        agent.client.chat.completions.create.side_effect = [
            _mock_response(
                finish_reason="tool_calls",
                tool_calls=[_mock_tool_call("write_file", '{"path":"/tmp/x","content":"1"}')],
            ),
            _mock_response(
                finish_reason="tool_calls",
                tool_calls=[_mock_tool_call("terminal", '{"command":"python /tmp/x"}')],
            ),
            _mock_response(content="Finished.", finish_reason="stop"),
        ]

        committed_cb = Mock()
        agent.tool_calls_committed_callback = committed_cb

        with patch.object(agent, "_execute_tool_calls", side_effect=_fake_execute_tool_calls):
            result = agent.run_conversation("write then run")

        assert "Finished." in result["final_response"]
        assert committed_cb.call_count == 2
        ids = []
        for call in committed_cb.call_args_list:
            msg = call[0][0]
            ids.extend(tc["id"] for tc in msg.get("tool_calls", []))
        assert ids == ["call_write_file", "call_terminal"]

    def test_stream_delta_flush_still_fires_for_tool_call_turns(self, tool_loop_agent):
        """Even when no text streamed, the loop should still signal the
        display callback with ``None`` before tool execution so any open
        streaming box is closed.
        """
        agent = tool_loop_agent
        agent.client.chat.completions.create.side_effect = [
            _mock_response(
                finish_reason="tool_calls",
                tool_calls=[_mock_tool_call("write_file", '{"path":"/tmp/x","content":"1"}')],
            ),
            _mock_response(content="Done.", finish_reason="stop"),
        ]

        delta_cb = Mock()
        agent.stream_delta_callback = delta_cb

        with patch.object(agent, "_execute_tool_calls", side_effect=_fake_execute_tool_calls):
            agent.run_conversation("write a file")

        # The loop flushes the stream display before executing tools.
        assert None in [call[0][0] for call in delta_cb.call_args_list]
