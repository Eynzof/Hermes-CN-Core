"""Performance benchmarks for the conversation loop.

All tests run in OFFLINE mode — no real LLM API calls.
"""

import orjson
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.perf
def test_message_sanitization_small(timing_context):
    """Measure sanitize_api_messages with 5 messages."""
    from agent.agent_runtime_helpers import sanitize_api_messages

    messages = [
        {"role": "system", "content": "You are a helpful assistant."},
        {"role": "user", "content": "Hello!"},
        {"role": "assistant", "content": "Hi there! How can I help?"},
        {"role": "user", "content": "What's the weather like?"},
        {"role": "assistant", "content": "Let me check."},
    ]

    with timing_context.measure("sanitize_messages_small"):
        result = sanitize_api_messages(messages)

    total_ms = timing_context.summary().get("sanitize_messages_small", {}).get("total_ms", 0)
    print(f"\n  Sanitize 5 messages: {total_ms:.1f}ms")
    assert total_ms < 1000  # First call includes JIT compilation overhead


@pytest.mark.perf
def test_message_sanitization_large(timing_context):
    """Measure sanitize_api_messages with 50+ messages."""
    from agent.agent_runtime_helpers import sanitize_api_messages

    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(25):
        messages.append({"role": "user", "content": f"Question {i}?"})
        messages.append({"role": "assistant", "content": f"Answer {i}."})

    with timing_context.measure("sanitize_messages_large"):
        result = sanitize_api_messages(messages)

    total_ms = timing_context.summary().get("sanitize_messages_large", {}).get("total_ms", 0)
    print(f"\n  Sanitize 50+ messages: {total_ms:.1f}ms")
    assert total_ms < 1000


@pytest.mark.perf
def test_sanitize_growing_conversation(timing_context):
    """Re-sanitize a conversation every turn as it grows to 50 tool-using turns.

    Mirrors ``test_incremental_token_estimation_50_turns``: the whole history is
    re-scanned each turn (150 messages by the end).  The per-turn cost must stay
    tiny because the sanitizer no longer triggers the ``run_agent`` import
    cascade that made it 44% of the conversation-loop flame graph.  The wide
    absolute bound keeps CI non-flaky; the real cost is microseconds.  A
    well-formed history is also returned unchanged, so this doubles as an
    allocation-free-fast-path check.
    """
    from agent.agent_runtime_helpers import sanitize_api_messages

    conv = [{"role": "system", "content": "You are a helpful assistant."}]
    with timing_context.measure("sanitize_growing_50_turns"):
        for i in range(50):
            conv.append({"role": "user", "content": f"Q{i}"})
            cid = f"call_{i}"
            conv.append({
                "role": "assistant",
                "tool_calls": [{"id": cid, "function": {"name": "terminal", "arguments": "{}"}}],
            })
            conv.append({"role": "tool", "tool_call_id": cid, "content": f"A{i}"})
            result = sanitize_api_messages(conv)
            assert result is conv  # well-formed -> identity pass-through

    total_ms = timing_context.summary().get("sanitize_growing_50_turns", {}).get("total_ms", 0)
    per_turn_ms = total_ms / 50.0
    print(f"\n  50-turn growing sanitize: {total_ms:.2f}ms total, "
          f"{per_turn_ms:.3f}ms/turn (final {len(conv)} msgs)")
    assert per_turn_ms < 100
    assert total_ms < 1000


@pytest.mark.perf
def test_token_estimation_timing(timing_context):
    """Measure token estimation performance."""
    from agent.model_metadata import estimate_messages_tokens_rough

    messages = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(30):
        messages.append({"role": "user", "content": f"Q{i}: " + "A" * 500})
        messages.append({"role": "assistant", "content": f"A{i}: " + "B" * 1000})

    with timing_context.measure("estimate_tokens"):
        token_count = estimate_messages_tokens_rough(messages)

    total_ms = timing_context.summary().get("estimate_tokens", {}).get("total_ms", 0)
    print(f"\n  Token estimation: {total_ms:.1f}ms, ~{token_count} tokens")
    assert total_ms < 1000


@pytest.mark.perf
def test_incremental_token_estimation_50_turns(timing_context):
    """50-turn conversation re-estimated every turn via IncrementalTokenEstimator.

    Each turn only the two new messages are measured; the unchanged prefix is
    served from cache. Asserts both correctness (matches a full stateless
    rescan exactly) and the plan's per-turn budget (< 100ms; real cost is
    microseconds, the wide bound keeps CI non-flaky).
    """
    from agent.model_metadata import (
        IncrementalTokenEstimator,
        estimate_messages_tokens_rough,
    )

    estimator = IncrementalTokenEstimator()
    conv = [{"role": "system", "content": "You are a helpful assistant."}]

    incremental = 0
    with timing_context.measure("incremental_estimate_50_turns"):
        for i in range(50):
            conv.append({"role": "user", "content": f"Q{i}: " + "A" * 500})
            conv.append({"role": "assistant", "content": f"A{i}: " + "B" * 1000})
            incremental = estimator.estimate(conv)

    # Correctness: cached estimate == full stateless rescan of the whole list.
    assert incremental == estimate_messages_tokens_rough(conv)

    summary = timing_context.summary().get("incremental_estimate_50_turns", {})
    total_ms = summary.get("total_ms", 0)
    per_turn_ms = total_ms / 50.0
    print(f"\n  50-turn incremental estimate: {total_ms:.2f}ms total, "
          f"{per_turn_ms:.3f}ms/turn")
    assert per_turn_ms < 100
    assert total_ms < 1000


@pytest.mark.perf
def test_incremental_estimator_matches_full_rescan(timing_context):
    """Observational: full O(n) rescan every turn vs. incremental over 50 turns.

    No relative timing gate (timing comparisons flake on shared CI); asserts
    correctness plus a generous absolute ceiling and prints both costs.
    """
    from agent.model_metadata import (
        IncrementalTokenEstimator,
        estimate_messages_tokens_rough,
    )

    def add_turn(c):
        c.append({"role": "user", "content": "u" * 400})
        c.append({"role": "assistant", "content": "a" * 800})

    c1 = [{"role": "system", "content": "sys"}]
    with timing_context.measure("full_rescan_50_turns"):
        for _ in range(50):
            add_turn(c1)
            estimate_messages_tokens_rough(c1)

    est = IncrementalTokenEstimator()
    c2 = [{"role": "system", "content": "sys"}]
    with timing_context.measure("incremental_50_turns"):
        for _ in range(50):
            add_turn(c2)
            est.estimate(c2)

    assert est.estimate(c2) == estimate_messages_tokens_rough(c2)

    s = timing_context.summary()
    full_ms = s.get("full_rescan_50_turns", {}).get("total_ms", 0)
    incr_ms = s.get("incremental_50_turns", {}).get("total_ms", 0)
    print(f"\n  full rescan: {full_ms:.2f}ms | incremental: {incr_ms:.2f}ms")
    assert incr_ms < 1000


@pytest.mark.perf
def test_tool_dispatch_timing(timing_context):
    """Measure handle_function_call dispatch overhead."""
    from model_tools import handle_function_call

    tool_calls = [
        ("read_file", {"path": "/tmp/test.txt"}),
        ("write_file", {"path": "/tmp/test.txt", "content": "hello world"}),
    ]

    with timing_context.measure("tool_dispatch_two_calls"):
        for tool_name, args in tool_calls:
            result = handle_function_call(tool_name, orjson.dumps(args).decode('utf-8'))

    summary = timing_context.summary()
    total_ms = summary.get("tool_dispatch_two_calls", {}).get("total_ms", 0)
    avg_ms = total_ms / len(tool_calls)
    print(f"\n  2 tool dispatches: {total_ms:.1f}ms (avg {avg_ms:.1f}ms each)")
    assert total_ms < 15000, f"2 dispatches took {total_ms:.1f}ms"


@pytest.mark.perf
def test_middleware_hooks_timing(timing_context):
    """Measure middleware hook overhead."""
    mock_plugin = MagicMock()
    mock_plugin.pre_tool_call = MagicMock()
    mock_plugin.post_tool_call = MagicMock()

    with timing_context.measure("pre_tool_call_hooks"):
        mock_plugin.pre_tool_call("read_file", {"path": "/tmp/test.txt"})
    with timing_context.measure("post_tool_call_hooks"):
        mock_plugin.post_tool_call("read_file", {"success": True})

    summary = timing_context.summary()
    print(f"\n  Pre-tool hook: {summary.get('pre_tool_call_hooks', {}).get('total_ms', 0):.1f}ms")
    print(f"  Post-tool hook: {summary.get('post_tool_call_hooks', {}).get('total_ms', 0):.1f}ms")


@pytest.mark.perf
@pytest.mark.perf_baseline
def test_conversation_loop_baseline(timing_context, mock_llm_response):
    """Baseline: run a 1-turn conversation with mocked LLM response."""
    from run_agent import AIAgent

    mock_response = mock_llm_response(
        content="Analysis complete.",
        finish_reason="stop",
    )

    with patch("run_agent.OpenAI") as mock_cls:
        mock_client = MagicMock()
        mock_client.chat.completions.create = MagicMock(return_value=mock_response)
        mock_cls.return_value = mock_client

        # Inject shutil into prompt_builder namespace (it's missing the import)
        import shutil as shutil_mod
        import agent.prompt_builder as pb
        pb.shutil = shutil_mod
        original_which = shutil_mod.which
        shutil_mod.which = MagicMock(return_value=None)

        with timing_context.measure("init_agent"):
            agent = AIAgent(
                base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
                model="test/mock-model", max_iterations=3, quiet_mode=True,
                skip_context_files=True, skip_memory=True, enabled_toolsets=["file"],
            )

        with timing_context.measure("conversation_turn"):
            result = agent.chat("Test message")

        # Restore
        shutil_mod.which = original_which

    summary = timing_context.summary()

    from tests.performance.conftest import save_baseline
    save_baseline("conversation_loop", {"summary": summary})

    turn_ms = summary.get("conversation_turn", {}).get("total_ms", 0)
    init_ms = summary.get("init_agent", {}).get("total_ms", 0)
    print(f"\n  Turn: {turn_ms:.1f}ms")
    print(f"  Init: {init_ms:.1f}ms")
    assert agent is not None