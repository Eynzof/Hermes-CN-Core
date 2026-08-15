"""Performance benchmarks for context compression.

All tests run in OFFLINE mode — no real LLM API calls.
Tests measure token estimation and message processing performance.
"""

import sys
from pathlib import Path
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _build_history(num_turns: int, tokens_per_msg: int = 200):
    msgs = [{"role": "system", "content": "You are a helpful assistant." * 10}]
    for i in range(num_turns):
        msgs.append({"role": "user", "content": f"Question {i}. " + "A" * tokens_per_msg})
        msgs.append({"role": "assistant", "content": f"Answer {i}. " + "B" * tokens_per_msg})
    return msgs


@pytest.mark.perf
def test_token_estimation_scaling(timing_context):
    """Measure token estimation scaling with message count."""
    from agent.model_metadata import estimate_messages_tokens_rough

    for num_turns in [5, 15, 30, 50]:
        messages = _build_history(num_turns)
        with timing_context.measure(f"estimate_tokens_{num_turns}turns"):
            for _ in range(5):
                token_count = estimate_messages_tokens_rough(messages)

    summary = timing_context.summary()
    for num_turns in [5, 15, 30, 50]:
        key = f"estimate_tokens_{num_turns}turns"
        ms = summary.get(key, {}).get("total_ms", 0)
        avg = ms / 5
        print(f"\n  Estimate tokens ({num_turns}turns): {ms:.1f}ms total, {avg:.1f}ms avg")
        assert avg < 100, f"Token estimation {num_turns}turns avg {avg:.1f}ms (expected < 100ms)"


@pytest.mark.perf
@pytest.mark.perf_baseline
def test_compression_baseline(timing_context):
    """Baseline measurement for message token estimation."""
    from agent.model_metadata import estimate_messages_tokens_rough

    messages = _build_history(25, tokens_per_msg=400)

    with timing_context.measure("compression_decision"):
        estimated_tokens = estimate_messages_tokens_rough(messages)

    with timing_context.measure("compression_execute"):
        # Simulate compression by trimming old messages
        kept = messages[:1] + messages[-10:]  # system + last 5 turns
        _ = estimate_messages_tokens_rough(kept)

    summary = timing_context.summary()

    from tests.performance.conftest import save_baseline
    save_baseline("compression", {"summary": summary})

    decision_ms = summary.get("compression_decision", {}).get("total_ms", 0)
    exec_ms = summary.get("compression_execute", {}).get("total_ms", 0)
    print(f"\n  Token estimation: {decision_ms:.1f}ms")
    print(f"  Simulated compression: {exec_ms:.1f}ms")
    assert decision_ms < 500, f"Decision took {decision_ms:.1f}ms (expected < 500ms)"