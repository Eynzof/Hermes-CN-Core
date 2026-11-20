"""Exercise the CN turn watchdog alongside real renderer requests."""

import queue
import threading
import time
import types


def test_turn_watchdog_pauses_for_clarify_then_rearms(monkeypatch):
    from tui_gateway import server, server_requests

    frames = queue.Queue()
    monkeypatch.setattr(server_requests, "_write", frames.put)
    question = {"qid": "q0", "id": "", "question": "Continue?", "choices": None,
                "choices_offered": [], "multi_select": False}
    thread = threading.Thread(target=server._clarify_block, args=("s1", [question]), daemon=True)
    thread.start()
    request = frames.get(timeout=10)
    assert request["method"] == "clarify"
    withdraw_read = server_requests.send_async("window.read", "s1", {}, lambda _: None)
    interrupted = threading.Event()
    session = {"running": True, "agent": types.SimpleNamespace(interrupt=lambda _: interrupted.set())}
    threshold = 0.1
    monkeypatch.setattr(server, "_turn_watchdog_seconds", lambda: threshold)

    class FastPoll(threading.Event):
        def wait(self, timeout=None):
            return super().wait(0.01)

    # Speed up the watchdog's ten-second poll without changing the real
    # blocking request, its deadline or the thread handling the user's reply.
    with monkeypatch.context() as setup:
        setup.setattr(server, "threading", types.SimpleNamespace(Event=FastPoll, RLock=threading.RLock))
        cancel = server._start_turn_watchdog("s1", session)
    try:
        assert not interrupted.wait(3 * threshold), "a pending clarification was interrupted"
        answered_at = time.monotonic()
        server.dispatch({"jsonrpc": "2.0", "id": request["id"], "result": {}})
        thread.join(timeout=2)
        assert not thread.is_alive()
        assert interrupted.wait(2), "a machine read hid the subsequent stall"
        assert time.monotonic() - answered_at >= threshold
        assert not session["running"]
    finally:
        cancel()
        withdraw_read("test cleanup")
        if thread.is_alive():
            server.dispatch({"jsonrpc": "2.0", "id": request["id"], "result": {}})
            thread.join(timeout=2)
