"""A timed-out turn must release its own interactive wait, not other sessions."""

import threading
import time
import types
from unittest.mock import Mock

import pytest

from tui_gateway import server


class _FastPollEvent:
    """Keep real cancellation semantics without a ten-second test poll."""

    def __init__(self):
        self.event = threading.Event()

    def is_set(self):
        return self.event.is_set()

    def set(self):
        self.event.set()

    def wait(self, timeout=None):
        return self.event.wait(min(timeout or 0.01, 0.01))


@pytest.mark.parametrize(
    "emit_fails,interrupt_fails", [(False, False), (True, False), (False, True), (True, True)]
)
def test_watchdog_releases_blocking_prompt_and_keeps_live_turn_serialized(
    monkeypatch, emit_fails, interrupt_fails
):
    sid = "watchdog-owner"
    other_event = threading.Event()
    prompt_emitted = threading.Event()
    block_returned = threading.Event()
    finish_worker = threading.Event()
    answer = []
    agent = types.SimpleNamespace(interrupt=Mock())
    if interrupt_fails:
        agent.interrupt.side_effect = RuntimeError("interrupt transport failed")
    session = {"agent": agent, "running": True, "history_lock": threading.RLock()}

    def emit(event, owner, payload):
        if event == "clarify.request" and owner == sid:
            prompt_emitted.set()
        if event == "error" and emit_fails:
            raise RuntimeError("renderer disconnected")

    monkeypatch.setattr(server, "_emit", emit)
    monkeypatch.setattr(server, "_pending", {"other": ("other-session", other_event)})
    monkeypatch.setattr(server, "_answers", {})

    def worker():
        try:
            answer.append(server._block("clarify.request", sid, {"question": "Choose"}, timeout=5))
            block_returned.set()
            finish_worker.wait(5)
        finally:
            session["running"] = False

    run_thread = threading.Thread(target=worker, daemon=True)
    session["_run_thread"] = run_thread
    run_thread.start()
    assert prompt_emitted.wait(2)

    monkeypatch.setattr(server, "threading", types.SimpleNamespace(Event=_FastPollEvent, RLock=threading.RLock))
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(server, "time", types.SimpleNamespace(time=lambda: next(clock)))
    monkeypatch.setattr(server, "_turn_watchdog_seconds", lambda: 1)
    cancel = server._start_turn_watchdog(sid, session)
    try:
        assert block_returned.wait(1), "watchdog left clarify waiting after reporting timeout"
        assert answer == [""]
        assert session["running"] is True, "next turn admitted before old worker exited"
        assert not other_event.is_set(), "watchdog cancelled an unrelated session"
        agent.interrupt.assert_called_once()
    finally:
        cancel()
        server._clear_pending(sid)
        finish_worker.set()
        run_thread.join(2)
    assert not run_thread.is_alive()


def test_watchdog_clears_running_for_an_already_dead_worker(monkeypatch):
    worker = threading.Thread(target=lambda: None)
    worker.start()
    worker.join()
    agent = types.SimpleNamespace(interrupt=Mock())
    session = {"agent": agent, "running": True, "history_lock": threading.RLock(), "_run_thread": worker}
    monkeypatch.setattr(server, "_emit", lambda *args: None)
    monkeypatch.setattr(server, "threading", types.SimpleNamespace(Event=_FastPollEvent, RLock=threading.RLock))
    clock = iter([0.0, 2.0])
    monkeypatch.setattr(server, "time", types.SimpleNamespace(time=lambda: next(clock)))
    monkeypatch.setattr(server, "_turn_watchdog_seconds", lambda: 1)
    cancel = server._start_turn_watchdog("dead-worker", session)
    try:
        deadline = time.monotonic() + 1
        while session["running"] and time.monotonic() < deadline:
            time.sleep(0.01)
        assert session["running"] is False
    finally:
        cancel()
