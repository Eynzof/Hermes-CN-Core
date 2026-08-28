from __future__ import annotations

import threading
import time

from hermes_cli.dashboard_bridge.runtime import ConversationRuntime


class BlockingAgent:
    def __init__(self) -> None:
        self.started = threading.Event()
        self.release = threading.Event()
        self.interrupted = threading.Event()

    def run_conversation(self, user_message, **kwargs):
        self.started.set()
        self.release.wait(timeout=2)
        return {"final_response": '{"decision":"remain_silent"}'}

    def interrupt(self, *args, **kwargs):
        self.interrupted.set()


def _wait_for(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true")
        time.sleep(0.01)


def test_discard_keeps_turn_in_flight_until_worker_finishes():
    agent = BlockingAgent()
    runtime = ConversationRuntime(lambda: agent)
    events: list[tuple[str, dict]] = []

    assert runtime.submit("hello", "corr-1", lambda event, payload: events.append((event, payload)))
    _wait_for(agent.started.is_set)

    runtime.discard("corr-1")

    assert agent.interrupted.is_set()
    assert runtime.in_flight("corr-1")
    assert not runtime.submit("second", "corr-2", lambda *_: None)

    agent.release.set()
    _wait_for(lambda: not runtime.in_flight("corr-1"))
    assert runtime.submit("second", "corr-2", lambda *_: None)


def test_discard_suppresses_late_events_without_releasing_turn():
    class FinishingAgent(BlockingAgent):
        def run_conversation(self, user_message, **kwargs):
            self.started.set()
            self.release.wait(timeout=2)
            kwargs["stream_callback"]("late")
            return {"final_response": "late"}

    agent = FinishingAgent()
    runtime = ConversationRuntime(lambda: agent)
    events: list[tuple[str, dict]] = []

    assert runtime.submit("hello", "corr-1", lambda event, payload: events.append((event, payload)))
    _wait_for(agent.started.is_set)
    runtime.discard("corr-1")
    agent.release.set()

    _wait_for(lambda: not runtime.in_flight("corr-1"))
    assert events == []
