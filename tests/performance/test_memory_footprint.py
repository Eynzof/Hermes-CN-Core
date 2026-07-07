"""Memory footprint benchmarks for Hermes-CN-Core.

All tests run in OFFLINE mode — no real LLM API calls.
"""

import gc
import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

def get_process_memory() -> float:
    if HAS_PSUTIL:
        return psutil.Process(os.getpid()).memory_info().rss / (1024 * 1024)
    return 0.0


@pytest.mark.perf
def test_idle_memory_footprint(timing_context):
    """Measure baseline memory usage with a freshly initialized agent."""
    from run_agent import AIAgent
    if not HAS_PSUTIL:
        pytest.skip("psutil not available")

    gc.collect()
    before_mb = get_process_memory()

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        agent = AIAgent(
            base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
            model="test/mock-model", max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
        )

    gc.collect()
    after_mb = get_process_memory()
    delta_mb = after_mb - before_mb

    print(f"\n  Memory before init: {before_mb:.1f} MB")
    print(f"  Memory after init: {after_mb:.1f} MB (delta: {delta_mb:.1f} MB)")
    assert delta_mb < 500, f"Memory increase too high: {delta_mb:.1f} MB"
    assert after_mb < 1000, f"Idle memory too high: {after_mb:.1f} MB"


@pytest.mark.perf
def test_message_list_growth(timing_context):
    """Track message list growth across simulated conversation turns."""
    msgs = [{"role": "system", "content": "You are a helpful assistant."}]
    for i in range(20):
        msgs.append({"role": "user", "content": f"Test question {i}." * 50})
        msgs.append({"role": "assistant", "content": f"Test answer {i}." * 200})

    size_mb = len(str(msgs)) / (1024 * 1024)
    print(f"\n  Messages: {len(msgs)} after 20 turns, {size_mb:.2f} MB")
    assert size_mb < 20, f"Message list too large: {size_mb:.2f} MB"


@pytest.mark.perf
def test_thread_pool_leak_detection(timing_context):
    """Detect thread leaks after concurrent tool execution."""
    import concurrent.futures, threading

    before = threading.active_count()
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:
        futures = [ex.submit(lambda x: x + 1, i) for i in range(20)]
        _ = [f.result() for f in futures]

    after = threading.active_count()
    delta = after - before
    print(f"\n  Threads: {before} -> {after} (delta: {delta})")
    assert delta < 10, f"Thread leak detected: {delta} new threads"


@pytest.mark.perf
def test_tool_result_storage_memory(timing_context):
    """Measure memory used by accumulated tool results across turns."""
    from tools.tool_result_storage import maybe_persist_tool_result

    results = []
    for turn in range(50):
        for tnum in range(5):
            results.append(json.dumps({"data": "A" * 1000}))

    total_mb = len(str(results)) / (1024 * 1024)
    print(f"\n  Stored 250 tool results: {total_mb:.2f} MB")
    assert total_mb < 20


@pytest.mark.perf
@pytest.mark.perf_baseline
def test_memory_baseline(timing_context):
    """Baseline memory measurement."""
    from run_agent import AIAgent
    if not HAS_PSUTIL:
        pytest.skip("psutil not available")

    gc.collect()
    baseline_mb = get_process_memory()

    with patch("run_agent.OpenAI") as mock_cls:
        mock_cls.return_value = MagicMock()
        agent = AIAgent(
            base_url="http://localhost:9999/v1", api_key="sk-test-mock-key",
            model="test/mock-model", max_iterations=5, quiet_mode=True,
            skip_context_files=True, skip_memory=True,
        )

    gc.collect()
    final_mb = get_process_memory()

    from tests.performance.conftest import save_baseline
    save_baseline("memory_footprint", {"baseline_mb": baseline_mb, "final_mb": final_mb})

    print(f"\n  Memory baseline: {baseline_mb:.1f} MB -> {final_mb:.1f} MB")
    assert final_mb < 1500, f"Memory exceeds limit: {final_mb:.1f} MB"