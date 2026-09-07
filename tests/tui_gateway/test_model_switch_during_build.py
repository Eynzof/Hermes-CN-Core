"""An explicit desktop pick must not lose to an already running draft build."""
import threading
from types import SimpleNamespace

from tui_gateway import server


def test_explicit_model_waits_for_inflight_agent_build(monkeypatch):
    ready = threading.Event()
    applying = threading.Event()
    started = threading.Event()
    agent = SimpleNamespace(model="default")
    session = {
        "agent": None, "agent_ready": ready, "agent_build_started": True,
        "session_key": "model-build-race", "running": False,
    }
    responses = []

    def apply(_sid, current, _value, **_kwargs):
        applying.set()
        if current["agent"] is not None:
            current["agent"].model = "selected"
        return {"value": "selected", "warning": ""}

    def change():
        started.set()
        responses.append(server.handle_request({
            "id": "pick", "method": "config.set",
            "params": {"session_id": "model-build-race", "key": "model",
                       "value": "selected --provider deepseek"},
        }))

    monkeypatch.setattr(server, "_apply_model_switch", apply)
    monkeypatch.setitem(server._sessions, "model-build-race", session)
    thread = threading.Thread(target=change)
    thread.start()
    try:
        assert started.wait(1)
        assert not applying.wait(0.1), "A draft built from old settings can overwrite this early switch"
    finally:
        session["agent"] = agent
        ready.set()
        thread.join(3)
    assert not thread.is_alive()
    assert not responses[0].get("error")
    assert agent.model == "selected"
