"""Shared fixtures, profiling decorators, and mock factories for the performance test suite.

All tests run in OFFLINE mode — no real LLM API calls. Every network-bound
model call is replaced with mocks/fakes/canned responses.

Usage:
    @pytest.mark.perf
    class TestSomething:
        def test_speed(self, timing_context):
            with timing_context("my_section"):
                do_something()
            report = timing_context.summary()
"""

import csv
import orjson
import os
import sys
import time
import timeit
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import MagicMock, AsyncMock, patch

import pytest

# Ensure project root is importable
PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# ── Report directory ──────────────────────────────────────────────────────
REPORTS_DIR = PROJECT_ROOT / "reports" / "perf"
RAW_DIR = REPORTS_DIR / "raw"
ATTACHMENTS_DIR = REPORTS_DIR / "attachments"
BASELINES_DIR = REPORTS_DIR / "baselines"

for d in (RAW_DIR, ATTACHMENTS_DIR, BASELINES_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ── Timing Instrumentation ─────────────────────────────────────────────────

@dataclass
class TimingSample:
    """A single timing measurement."""
    section: str
    start_time: float
    end_time: float
    duration_ms: float
    iteration: int = 0


@dataclass
class TimingContext:
    """Collects timing samples during a test."""

    samples: List[TimingSample] = field(default_factory=list)
    _current_iteration: int = 0
    _section_entry: float = 0.0
    _section_name: str = ""

    def start(self, section: str):
        """Begin timing a section."""
        self._section_name = section
        self._section_entry = time.perf_counter()

    def stop(self):
        """End timing the current section and record the sample."""
        elapsed = time.perf_counter() - self._section_entry
        self.samples.append(TimingSample(
            section=self._section_name,
            start_time=self._section_entry,
            end_time=time.perf_counter(),
            duration_ms=round(elapsed * 1000, 3),
            iteration=self._current_iteration,
        ))

    @contextmanager
    def measure(self, section: str):
        """Context manager for timing a block."""
        self.start(section)
        try:
            yield
        finally:
            self.stop()

    def new_iteration(self):
        """Advance to the next iteration counter."""
        self._current_iteration += 1

    def summary(self) -> Dict[str, Any]:
        """Aggregate timing data grouped by section name."""
        from collections import defaultdict
        grouped = defaultdict(list)
        for s in self.samples:
            grouped[s.section].append(s.duration_ms)

        result = {}
        for section, durations in sorted(grouped.items()):
            result[section] = {
                "count": len(durations),
                "total_ms": round(sum(durations), 3),
                "mean_ms": round(sum(durations) / len(durations), 3),
                "min_ms": round(min(durations), 3),
                "max_ms": round(max(durations), 3),
                "durations": durations,
            }
        return result

    def to_json(self) -> str:
        """Serialize timing data as JSON."""
        return orjson.dumps({
            "samples": [
                {
                    "section": s.section,
                    "duration_ms": s.duration_ms,
                    "iteration": s.iteration,
                }
                for s in self.samples
            ],
            "summary": self.summary(),
        }, option=orjson.OPT_INDENT_2).decode('utf-8')

    def save_raw(self, test_name: str):
        """Save raw timing data to reports/perf/raw/<test_name>.json."""
        import datetime
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        path = RAW_DIR / f"{timestamp}-{test_name}.json"
        path.write_text(self.to_json(), encoding="utf-8")
        return path


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def timing_context():
    """Provide a TimingContext for collecting performance measurements."""
    ctx = TimingContext()
    yield ctx
    # Auto-save on cleanup
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    test_name = os.environ.get("PYTEST_CURRENT_TEST", "unknown").split("::")[-1]
    ctx.save_raw(test_name)


@pytest.fixture
def perf_output_dir():
    """Return the report output directory."""
    return REPORTS_DIR


@pytest.fixture
def mock_llm_response():
    """Create a mock LLM chat completion response.

    Returns a dict simulating OpenAI-style ChatCompletion response.
    No real network calls involved.
    """
    def _make_response(
        content: str = "This is a mock response.",
        tool_calls: Optional[List[Dict]] = None,
        finish_reason: str = "stop",
        model: str = "mock-model",
    ):
        choice = {
            "index": 0,
            "message": {
                "role": "assistant",
                "content": content,
            },
            "finish_reason": finish_reason,
        }
        if tool_calls:
            choice["message"]["tool_calls"] = [
                {
                    "id": tc.get("id", f"call_{i}"),
                    "type": "function",
                    "function": {
                        "name": tc["name"],
                        "arguments": orjson.dumps(tc.get("arguments", {})).decode('utf-8'),
                    },
                }
                for i, tc in enumerate(tool_calls)
            ]

        return {
            "id": f"mock-chatcmpl-{hash(content) & 0xFFFFFFFF:08x}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [choice],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "total_tokens": 150,
            },
        }
    return _make_response


@pytest.fixture
def mock_stream_chunks():
    """Create mock streaming chunks for testing stream processing.

    Returns a list of dicts simulating streaming ChatCompletionChunk objects.
    """
    def _make_chunks(
        texts: List[str] = None,
        tool_call_deltas: List[Dict] = None,
        finish_reason: str = "stop",
    ):
        if texts is None:
            texts = ["Mock ", "streaming ", "response."]
        chunks = []
        for i, text in enumerate(texts):
            chunk = {
                "id": f"mock-chatcmpl-{i:08x}",
                "object": "chat.completion.chunk",
                "created": int(time.time()),
                "model": "mock-model",
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": text},
                    "finish_reason": None,
                }],
            }
            chunks.append(chunk)

        if tool_call_deltas:
            for tc in tool_call_deltas:
                chunks.append({
                    "id": "mock-chatcmpl-tc",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": "mock-model",
                    "choices": [{
                        "index": 0,
                        "delta": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [tc],
                        },
                        "finish_reason": None,
                    }],
                })

        # Final chunk with finish_reason
        chunks.append({
            "id": "mock-chatcmpl-final",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": "mock-model",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }],
        })
        return chunks
    return _make_chunks


@pytest.fixture
def mock_anthropic_response():
    """Create a mock Anthropic-style message response."""
    def _make_response(
        content: str = "This is a mock Anthropic response.",
        stop_reason: str = "end_turn",
        model: str = "claude-3-sonnet-mock",
    ):
        return {
            "id": f"mock-msg-{hash(content) & 0xFFFFFFFF:08x}",
            "type": "message",
            "role": "assistant",
            "content": [{"type": "text", "text": content}],
            "model": model,
            "stop_reason": stop_reason,
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        }
    return _make_response


@pytest.fixture
def patch_openai_client():
    """Patch the OpenAI client to use a mock that returns canned responses.

    Usage:
        def test_something(patch_openai_client):
            mock_client = patch_openai_client(mock_response)
            # agent now uses mock_client for chat completions
    """
    def _patch(response: Dict = None):
        mock_client = MagicMock()
        mock_chat = MagicMock()
        mock_completions = MagicMock()

        if response:
            mock_create = MagicMock(return_value=response)
        else:
            mock_create = MagicMock()

        mock_completions.create = mock_create
        mock_chat.completions = mock_completions
        mock_client.chat = mock_chat

        patcher = patch("run_agent.OpenAI", return_value=mock_client)
        patcher.start()

        return mock_client, patcher
    return _patch


@pytest.fixture
def patch_auxiliary_client():
    """Patch the auxiliary client's call_llm to return canned summaries.

    This prevents real network calls during context compression profiling.
    """
    def _patch(summary: str = "This is a mock summary of the conversation."):
        mock_aux = MagicMock()
        mock_aux.call_llm = MagicMock(return_value=summary)
        patcher = patch("agent.auxiliary_client.call_llm", return_value=summary)
        patcher.start()
        return mock_aux, patcher
    return _patch


@pytest.fixture
def mock_tool_result():
    """Create a mock tool execution result."""
    def _make(
        tool_name: str = "read_file",
        result: str = '{"success": true}',
        duration_ms: float = 10.0,
    ):
        return {
            "tool_name": tool_name,
            "result": result,
            "duration_ms": duration_ms,
        }
    return _make


# ── Pytest markers / hooks ────────────────────────────────────────────────

def pytest_configure(config):
    """Register custom markers for the performance test suite."""
    config.addinivalue_line(
        "markers",
        "perf: Performance benchmark test (runs in offline mode).",
    )
    config.addinivalue_line(
        "markers",
        "perf_windows: Windows-specific performance benchmark.",
    )
    config.addinivalue_line(
        "markers",
        "perf_baseline: Baseline measurement — results become the comparison target.",
    )


# ── Helpers ────────────────────────────────────────────────────────────────

def assert_within_tolerance(actual: float, expected: float, tolerance: float = 0.2):
    """Assert that |actual - expected| / expected <= tolerance."""
    if expected == 0:
        assert actual == 0, f"Expected 0, got {actual}"
        return
    ratio = abs(actual - expected) / expected
    assert ratio <= tolerance, (
        f"Performance regression: {actual:.1f}ms vs baseline {expected:.1f}ms "
        f"({ratio*100:.1f}% deviation, tolerance {tolerance*100:.1f}%)"
    )


def get_latest_baseline() -> Optional[Dict]:
    """Load the most recent baseline JSON from reports/perf/baselines/."""
    if not BASELINES_DIR.exists():
        return None
    baselines = sorted(BASELINES_DIR.glob("*.json"))
    if not baselines:
        return None
    return orjson.loads(baselines[-1].read_text(encoding="utf-8", errors="replace"))


def save_baseline(test_name: str, summary: Dict):
    """Save timing summary as a baseline for future comparison."""
    import datetime
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    path = BASELINES_DIR / f"{timestamp}-{test_name}.json"
    path.write_text(orjson.dumps(summary, option=orjson.OPT_INDENT_2).decode('utf-8'), encoding="utf-8")
    return path