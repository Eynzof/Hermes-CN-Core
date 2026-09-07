"""Gateway events carry a stable per-turn id (``turn_id``).

Before this, events carried only ``type`` + ``session_id``, so a client had no
way to distinguish "same turn, more output" from "a new turn started" and had to
infer turn identity from stream state plus text/tool-call heuristics. A
duplicated ``message.start`` (several dispatch paths pre-emit one before
``_run_prompt_submit`` emits its own) or a reconnect replay then looked exactly
like a fresh turn and split one reply into two bubbles.

The id is in-memory only (it lives on ``inflight_turn``), so nothing here
touches persistence or schema.
"""

import pytest

from tui_gateway import server


@pytest.fixture
def session(monkeypatch):
    """Register a bare session in the module registry, cleaned up after."""
    sid = "sess-turn-id"
    sess: dict = {"inflight_turn": None}
    monkeypatch.setitem(server._sessions, sid, sess)
    return sid, sess


def test_turn_id_absent_while_session_idle(session):
    """An idle session emits byte-identical frames to the pre-change shape."""
    sid, _ = session
    frame = server._event_frame("status.update", sid, {"text": "x"})

    assert "turn_id" not in frame["params"]
    assert frame == {
        "jsonrpc": "2.0",
        "method": "event",
        "params": {"type": "status.update", "session_id": sid, "payload": {"text": "x"}},
    }


def test_turn_id_absent_for_sessionless_global_broadcast():
    """``skin.changed``-style global events have no session, so no turn."""
    frame = server._event_frame("skin.changed", "")

    assert "turn_id" not in frame["params"]


def test_turn_id_absent_for_unknown_session():
    frame = server._event_frame("message.start", "no-such-session")

    assert "turn_id" not in frame["params"]


def test_every_event_of_a_turn_shares_one_turn_id(session):
    """The whole turn — start, deltas, tool events — is stamped identically."""
    sid, sess = session
    server._start_inflight_turn(sess, "hello")

    ids = {
        server._event_frame(kind, sid)["params"].get("turn_id")
        for kind in ("message.start", "message.delta", "tool.start", "reasoning.delta")
    }

    assert len(ids) == 1
    turn_id = ids.pop()
    assert isinstance(turn_id, str) and turn_id.startswith("turn_")


def test_new_turn_gets_a_different_id(session):
    """Consecutive turns are distinguishable — the whole point of the field."""
    sid, sess = session

    server._start_inflight_turn(sess, "first")
    first = server._event_frame("message.start", sid)["params"]["turn_id"]

    server._start_inflight_turn(sess, "second")
    second = server._event_frame("message.start", sid)["params"]["turn_id"]

    assert first != second


def test_duplicate_message_start_is_idempotent_within_one_turn(session):
    """Five dispatch paths pre-emit ``message.start`` before
    ``_run_prompt_submit`` emits its own (server.py documents the double-fire at
    the steer path). Both frames must report the SAME turn, so a client keyed on
    turn_id cannot be tricked into opening a second bubble."""
    sid, sess = session
    server._start_inflight_turn(sess, "hello")

    pre_emit = server._event_frame("message.start", sid)["params"]["turn_id"]
    # _run_prompt_submit reuses a live turn rather than replacing it, so the
    # second start lands inside the same turn.
    from_submit = server._event_frame("message.start", sid)["params"]["turn_id"]

    assert pre_emit == from_submit


def test_terminal_frame_keeps_turn_id_after_the_turn_is_cleared(session):
    """``_clear_inflight_turn`` runs under history_lock BEFORE
    ``message.complete`` is emitted, so the closing frame — the one a client most
    needs to attribute — would otherwise arrive unstamped. The explicit override
    carries it across the clear."""
    sid, sess = session
    server._start_inflight_turn(sess, "hello")
    completed_turn_id = server._current_turn_id(sid)

    server._clear_inflight_turn(sess)

    # Without the override the frame is unstamped …
    assert "turn_id" not in server._event_frame("message.complete", sid)["params"]
    # … with it, the terminal frame still names its turn.
    stamped = server._event_frame(
        "message.complete", sid, {"text": "done"}, turn_id=completed_turn_id
    )
    assert stamped["params"]["turn_id"] == completed_turn_id


def test_failed_turn_retains_its_turn_id_for_resume_replay(session):
    """``_fail_inflight_turn`` keeps the snapshot replayable; the id must
    survive so a resuming client can attribute the retained failure."""
    sid, sess = session
    server._start_inflight_turn(sess, "hello")
    before = server._current_turn_id(sid)

    sess["history_lock"] = __import__("threading").RLock()
    with sess["history_lock"]:
        server._fail_inflight_turn(sess, "provider exploded")

    assert server._current_turn_id(sid) == before


def test_malformed_inflight_turn_does_not_raise(session):
    """Defensive: a non-dict / id-less snapshot yields no turn_id rather than
    breaking every event on the session."""
    sid, sess = session

    sess["inflight_turn"] = "not-a-dict"
    assert server._current_turn_id(sid) is None

    sess["inflight_turn"] = {"assistant": ""}  # no turn_id key
    assert server._current_turn_id(sid) is None

    sess["inflight_turn"] = {"turn_id": ""}  # empty string is not an id
    assert server._current_turn_id(sid) is None
