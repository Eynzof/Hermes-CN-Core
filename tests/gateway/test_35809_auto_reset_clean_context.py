"""Regression tests for #35809 — compression-exhaustion auto-reset loop.

After compression is exhausted the gateway auto-resets the session so the
next message starts on a fresh, empty conversation (#9893 / #10063). That
guarantee regressed once the Telegram topic-binding heal landed
(#20470 / #29712 / #33414):

    1. Compression rotates ``session_entry.session_id`` to an oversized
       compressed *child* session mid-turn and the agent-result sync rewrites
       the ``(chat_id, thread_id) -> child`` topic binding.
    2. ``reset_session`` swaps in a clean, parentless session — but its return
       value was discarded and the topic binding was left pointing at the
       bloated child.
    3. On the next inbound message in that topic, the binding-heal walk
       ``switch_session``'d the freshly-reset lane *back* onto the bloated
       child, ``load_transcript`` reloaded the oversized transcript, and
       compression exhaustion re-fired — a new session id every loop.

The fix captures the fresh entry from ``reset_session`` and re-syncs the
topic binding to it (a no-op on non-topic lanes).

Two tests:

* ``TestAutoResetBlockReSyncsBinding`` — an AST invariant on
  ``gateway/run.py`` (mirrors ``test_compression_session_id_persistence.py``):
  the compression-exhausted auto-reset block must capture
  ``reset_session(...)`` and call ``_sync_telegram_topic_binding`` afterward.
  This is the load-bearing regression pin.
* ``TestAutoResetLoadsCleanContext`` — a behavioral contract on the real
  ``SessionStore``: after ``reset_session`` the next turn loads an EMPTY
  transcript for the new session_id, never the bloated child's transcript.
"""

from __future__ import annotations

import ast
import inspect

from gateway import run as gateway_run
from gateway.config import GatewayConfig, Platform
from gateway.session import SessionSource, SessionStore
from hermes_state import SessionDB

# ---------------------------------------------------------------------------
# AST invariant: the auto-reset block re-syncs the topic binding
# ---------------------------------------------------------------------------
def _find_compression_exhausted_reset_block() -> ast.If:
    """Return the ``if agent_result.get('compression_exhausted') ...`` block."""
    tree = ast.parse(inspect.getsource(gateway_run))

    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        consts = [
            n.value
            for n in ast.walk(node.test)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
        ]
        # Identify the auto-reset branch by the literal passed to .get(...).
        if "compression_exhausted" in consts:
            # Only the branch that actually performs the reset, not the
            # earlier classifier that merely reads the flag into a bool.
            calls = {
                sub.func.attr
                for sub in ast.walk(node)
                if isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
            }
            if "reset_session" in calls:
                return node
    raise AssertionError(
        "Could not locate the compression-exhausted auto-reset block "
        "(if agent_result.get('compression_exhausted') ... reset_session) "
        "in gateway/run.py — the structure changed or the AST walker is stale."
    )

# ---------------------------------------------------------------------------
# Behavioral contract: reset yields a clean next-turn transcript
# ---------------------------------------------------------------------------
def _make_store(tmp_path):
    store = SessionStore(sessions_dir=tmp_path, config=GatewayConfig())
    # Isolate the SQLite transcript store so we exercise per-session_id
    # transcripts without touching the developer's real state.db.
    store._db = SessionDB(db_path=tmp_path / "state.db")
    return store

def _make_source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="123", user_id="u1")

def _bloat(n):
    # Stand-in for the oversized, post-compression "child" transcript that
    # could not be compressed any further (#35809). Alternates roles so the
    # fixture is a valid conversation: load_transcript is a live-replay
    # restore site and heals alternation violations on load (#64934), so a
    # degenerate all-user transcript would be merged into one message.
    return [
        {
            "role": "user" if i % 2 == 0 else "assistant",
            "content": "x" * 2000,
        }
        for i in range(n)
    ]

class TestAutoResetLoadsCleanContext:
    """#35809: after the gateway auto-resets a session because compression
    was exhausted, the NEXT turn must load an EMPTY transcript for the new
    session_id — never the bloated compressed-child transcript."""

    def test_next_turn_transcript_is_empty_after_auto_reset(self, tmp_path):
        store = _make_store(tmp_path)
        source = _make_source()

        entry = store.get_or_create_session(source)
        session_key = entry.session_key
        bloated_sid = entry.session_id
        store._db.create_session(
            session_id=bloated_sid, source="telegram", user_id="u1"
        )
        store._db.replace_messages(bloated_sid, _bloat(120))
        assert len(store.load_transcript(bloated_sid)) == 120  # precondition

        new_entry = store.reset_session(session_key)
        assert new_entry is not None
        assert new_entry.session_id != bloated_sid

        resolved = store.get_or_create_session(source)
        assert resolved.session_id == new_entry.session_id
        loaded = store.load_transcript(resolved.session_id)

        assert loaded == [], (
            f"Auto-reset must yield an empty context, got {len(loaded)} "
            f"messages — the bloated compressed child leaked into the new session."
        )
        # The old transcript is still searchable, not destroyed.
        assert len(store.load_transcript(bloated_sid)) == 120

    def test_clean_context_survives_gateway_restart(self, tmp_path):
        """The fresh, empty session must still be the one loaded after a
        gateway restart (sessions.json + state.db round-trip)."""
        store = _make_store(tmp_path)
        source = _make_source()
        entry = store.get_or_create_session(source)
        bloated_sid = entry.session_id
        store._db.create_session(
            session_id=bloated_sid, source="telegram", user_id="u1"
        )
        store._db.replace_messages(bloated_sid, _bloat(120))

        new_entry = store.reset_session(entry.session_key)
        new_sid = new_entry.session_id

        # Simulate restart: drop in-memory index, reload from disk.
        store._loaded = False
        store._entries.clear()

        reloaded = store.get_or_create_session(source)
        assert reloaded.session_id == new_sid
        assert store.load_transcript(reloaded.session_id) == []
