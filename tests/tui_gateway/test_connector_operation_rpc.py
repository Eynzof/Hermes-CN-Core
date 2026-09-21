"""Connector operation RPCs and events over the real dispatch path.

- ``connectors.operation.status`` reads the live operation with the same ownership checks as
  ``connectors.list`` (4000 / 4001); ``deadline_at`` is the server's, never recomputed
- ``connection.respond`` finds the op by ``op_id`` and drives target transitions; a foreign
  transport cannot
- every target transition and the settlement emit ``connection.update`` to the session
- ``pending_connection`` on resume comes from the live registry
"""

import json
import os
import threading
import time
from contextlib import ExitStack, suppress

import pytest

from tools.connectors import live
from tools.connectors.contract import Actor, TargetState
from tools.connectors.operation import ConnectionOperation, Target
from tui_gateway import server
from tui_gateway.transport import StdioTransport


# Every reply here is produced on the RPC pool, and every event on the dispatcher;
# both land on ``owner.frames`` from PipeClient's reader thread. The canonical runner
# executes one pytest process per file on every core (16-48 at once), where the same
# file takes 7s alone and 57s under load — so a 2s poll for that frame is a race
# against the scheduler, not a contract. Wait on the reader thread's condition with a
# bound generous enough for a saturated box (AGENTS.md: timing tests must not assume a
# quiet runner, wall-clock bounds >= 2s); a genuinely missing frame still fails.
_FRAME_WAIT_SECONDS = 30.0


class PipeClient:
    def __init__(self, stack):
        read_fd, write_fd = os.pipe()
        self.reader = stack.enter_context(os.fdopen(read_fd, "r", encoding="utf-8"))
        self.writer = os.fdopen(write_fd, "w", encoding="utf-8")
        stack.callback(lambda: suppress(BrokenPipeError) and self.writer.close())
        self.transport = StdioTransport(lambda: self.writer, threading.Lock())
        self.frames = []
        # Notifies waiters (``reply``/``events``) as frames arrive instead of making
        # them poll and guess how long the scheduler will need.
        self.frame_condition = threading.Condition()
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self):
        for line in self.reader:
            frame = json.loads(line)
            with self.frame_condition:
                self.frames.append(frame)
                self.frame_condition.notify_all()

    def write(self, obj):
        return self.transport.write(obj)

    def close(self):
        pass

    def reply(self, reply_id, *, after=0):
        """Block until the reply ``reply_id`` arrives after frame index ``after``."""
        deadline = time.monotonic() + _FRAME_WAIT_SECONDS
        with self.frame_condition:
            while True:
                replies = [f for f in self.frames[after:] if f.get("id") == reply_id]
                if replies:
                    return replies[-1]
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise AssertionError(f"no reply with id={reply_id}")
                self.frame_condition.wait(remaining)

    def events(self, kind, *, expect=1):
        deadline = time.monotonic() + _FRAME_WAIT_SECONDS
        with self.frame_condition:
            while True:
                found = [f["params"] for f in self.frames if f.get("method") == "event" and f["params"]["type"] == kind]
                remaining = deadline - time.monotonic()
                if len(found) >= expect or remaining <= 0:
                    return found
                self.frame_condition.wait(remaining)


SID = "op-rpc-session"


@pytest.fixture
def owned(monkeypatch):
    live.reset_for_tests()
    with ExitStack() as stack:
        owner, stranger = PipeClient(stack), PipeClient(stack)
        session = dict(transport=owner.transport, agent=None, session_key=SID, history=[],
                       history_lock=threading.Lock(), history_version=0, running=False, attached_images=[],
                       source="desktop")
        monkeypatch.setitem(server._sessions, SID, session)
        yield owner, stranger, session
    live.reset_for_tests()


def _rpc(client, method, **params):
    return server.dispatch({"jsonrpc": "2.0", "id": 7, "method": method, "params": {"session_id": SID, **params}},
                           client.transport)


def _open_op():
    operation = ConnectionOperation([Target("gmail", "connector", "connect"), Target("notion", "connector", "connect")],
                                    session_key=SID)
    live.open(operation)
    return operation


def test_operation_status_returns_the_live_snapshot_with_the_server_deadline(owned):
    owner, _, _ = owned
    operation = _open_op()
    operation.transition("gmail", TargetState.initiated, Actor.backend_watcher, connect_url="https://l/gmail")
    reply = _rpc(owner, "connectors.operation.status", op_id=operation.op_id)
    result = reply["result"]
    assert result["op_id"] == operation.op_id
    assert result["deadline_at"] == operation.deadline_at
    assert result["settled"] is False and result["settled_by"] is None
    by = {t["name"]: t for t in result["targets"]}
    assert by["gmail"]["state"] == "initiated" and by["gmail"]["connect_url"] == "https://l/gmail"
    assert by["notion"]["state"] == "pending"


def test_operation_status_is_owner_only_and_validates_params(owned):
    owner, stranger, _ = owned
    operation = _open_op()
    assert _rpc(stranger, "connectors.operation.status", op_id=operation.op_id)["error"]["code"] == 4001
    assert _rpc(owner, "connectors.operation.status")["error"]["code"] == 4000
    assert _rpc(owner, "connectors.operation.status", op_id="nope")["error"]["code"] == 4004


def test_respond_drives_the_live_operation_and_emits_update(owned):
    owner, stranger, _ = owned
    operation = _open_op()
    operation.transition("gmail", TargetState.initiated, Actor.backend_watcher)
    foreign = _rpc(stranger, "connection.respond", op_id=operation.op_id,
                   result={"targets": [{"name": "notion", "status": "skipped"}]})
    assert foreign["error"]["code"] == 4001
    assert operation.target("notion").state == TargetState.pending

    reply = _rpc(owner, "connection.respond", op_id=operation.op_id,
                 result={"targets": [{"name": "notion", "status": "skipped"}]})
    assert "result" in reply, reply
    assert operation.target("notion").state == TargetState.skipped
    assert operation.wake.is_set()
    updates = owner.events("connection.update", expect=2)
    assert updates and updates[-1]["payload"]["target"] == "notion"
    assert updates[-1]["payload"]["to"] == "skipped" and updates[-1]["payload"]["actor"] == "user"
    assert updates[-1]["payload"]["op_id"] == operation.op_id


def test_respond_cannot_claim_connected_for_a_managed_target(owned):
    owner, _, _ = owned
    operation = _open_op()
    operation.transition("gmail", TargetState.initiated, Actor.backend_watcher)
    reply = _rpc(owner, "connection.respond", op_id=operation.op_id,
                 result={"targets": [{"name": "gmail", "status": "connected"}]})
    assert reply["error"]["code"] == 4002
    assert operation.target("gmail").state == TargetState.initiated


def test_respond_continue_settles_and_emits_the_settlement_update(owned):
    owner, _, _ = owned
    operation = _open_op()
    reply = _rpc(owner, "connection.respond", op_id=operation.op_id, result={"settled_by": "continue"})
    assert "result" in reply
    assert operation.settled and operation.settled_by.value == "continue"
    settled = [u for u in owner.events("connection.update") if u["payload"].get("settled")]
    assert settled and settled[-1]["payload"]["settled_by"] == "continue"


def test_pending_connection_on_resume_comes_from_the_live_registry(owned):
    operation = _open_op()
    payload = server._pending_connection_request_payload(SID)
    assert payload["op_id"] == operation.op_id
    assert payload["deadline_at"] == operation.deadline_at
    live.close(operation)
    assert server._pending_connection_request_payload(SID) is None


def test_panel_connect_reissues_only_a_dead_link(owned, monkeypatch):
    """Try again re-mints a failed or expired target; a waiting target keeps the link it was minted
    with (the card reopens it), so the RPC refuses rather than spend a second mint."""
    owner, _, _ = owned
    operation = _open_op()
    operation.transition("gmail", TargetState.initiated, Actor.backend_watcher, connect_url="https://l/gmail/1")
    operation.transition("notion", TargetState.initiated, Actor.backend_watcher, connect_url="https://l/notion/1")
    operation.transition("notion", TargetState.failed, Actor.backend_watcher, detail="vendor: nope")
    mints = []

    class Client:
        def connections(self, names, *, reinitiate=False):
            mints.append((tuple(names), reinitiate))
            return {"results": [{"connector": n, "status": "initiated", "connect_url": f"https://l/{n}/2"} for n in names]}

    monkeypatch.setattr("tools.connectors.gateway.client.ConnectorClient", Client)
    # The real dispatcher: availability gate passes, then the open operation routes to _reissue.
    monkeypatch.setattr("tools.connectors.connectors_available", lambda: True)
    monkeypatch.setattr("model_tools._select_tool_names", lambda *a, **k: {"manage_connections"})

    def long_rpc(**params):
        # connectors.connect runs on the pool and writes its reply to the transport.
        before = len(owner.frames)
        _rpc(owner, "connectors.connect", **params)
        return owner.reply(7, after=before)

    refused = long_rpc(connectors=["gmail"])
    assert refused["error"]["code"] == 4002 and mints == []
    assert operation.target("gmail").connect_url == "https://l/gmail/1"

    reply = long_rpc(connectors=["notion"])
    assert "result" in reply, reply
    assert mints == [(("notion",), True)]
    assert operation.target("notion").state == TargetState.initiated
    assert operation.target("notion").connect_url == "https://l/notion/2"


@pytest.mark.parametrize("dead", [TargetState.failed, TargetState.expired])
def test_a_failed_reissue_leaves_the_row_failed_with_no_link(owned, monkeypatch, dead):
    """Try again whose mint fails must not show the row as waiting on the old dead link."""
    owner, _, _ = owned
    operation = _open_op()
    operation.transition("notion", TargetState.initiated, Actor.backend_watcher, connect_url="https://l/notion/1")
    actor = Actor.clock if dead == TargetState.expired else Actor.backend_watcher
    operation.transition("notion", dead, actor, detail="vendor: nope")

    class Client:
        def connections(self, names, *, reinitiate=False):
            return {"results": [{"connector": n, "status": "failed", "status_reason": "vendor: still no"} for n in names]}

    monkeypatch.setattr("tools.connectors.gateway.client.ConnectorClient", Client)
    monkeypatch.setattr("tools.connectors.connectors_available", lambda: True)
    monkeypatch.setattr("model_tools._select_tool_names", lambda *a, **k: {"manage_connections"})

    before = len(owner.frames)
    _rpc(owner, "connectors.connect", connectors=["notion"])
    owner.reply(7, after=before)
    target = operation.target("notion")
    assert target.state == TargetState.failed
    assert target.connect_url is None
    assert target.detail == "vendor: still no"

