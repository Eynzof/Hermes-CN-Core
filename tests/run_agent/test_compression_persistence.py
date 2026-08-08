"""Tests for context compression persistence in the gateway.

Verifies that when context compression fires during run_conversation(),
the compressed messages are properly persisted to both SQLite (via the
agent) and JSONL (via the gateway).

Bug scenario (pre-fix):
  1. Gateway loads 200-message history, passes to agent
  2. Agent's run_conversation() compresses to ~30 messages mid-run
  3. _compress_context() resets _last_flushed_db_idx = 0
  4. On exit, _flush_messages_to_session_db() calculates:
     flush_from = max(len(conversation_history=200), _last_flushed_db_idx=0) = 200
  5. messages[200:] is empty (only ~30 messages after compression)
  6. Nothing written to new session's SQLite — compressed context lost
  7. Gateway's history_offset was still 200, producing empty new_messages
  8. Fallback wrote only user/assistant pair — summary lost
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: tempfile/path operations fail")

import run_agent

# ---------------------------------------------------------------------------
# Part 1: Agent-side — _flush_messages_to_session_db after compression
# ---------------------------------------------------------------------------

class TestFlushAfterCompression:
    """Verify that compressed messages are flushed to the new session's SQLite
    even when conversation_history (from the original session) is longer than
    the compressed messages list."""

    def _make_agent(self, session_db):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            from run_agent import AIAgent
            agent = AIAgent(
                api_key="test-key",
                base_url="https://openrouter.ai/api/v1",
                model="test/model",
                quiet_mode=True,
                session_db=session_db,
                session_id="original-session",
                skip_context_files=True,
                skip_memory=True,
            )
        return agent

    def test_flush_after_compression_with_long_history(self):
        """The actual bug: conversation_history longer than compressed messages.

        Before the fix, flush_from = max(len(conversation_history), 0) = 200,
        but messages only has ~30 entries, so messages[200:] is empty.
        After the fix, conversation_history is cleared to None after compression,
        so flush_from = max(0, 0) = 0, and ALL compressed messages are written.
        """
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = SessionDB(db_path=db_path)

            agent = self._make_agent(db)

            # Simulate the original long history (200 messages)
            original_history = [
                {"role": "user" if i % 2 == 0 else "assistant",
                 "content": f"message {i}"}
                for i in range(200)
            ]

            # First, flush original messages to the original session
            agent._flush_messages_to_session_db(original_history, [])
            original_rows = db.get_messages("original-session")
            assert len(original_rows) == 200

            # Now simulate compression: new session, reset idx, shorter messages
            agent.session_id = "compressed-session"
            db.create_session(session_id="compressed-session", source="test")
            agent._last_flushed_db_idx = 0

            # The compressed messages (summary + tail + new turn)
            compressed_messages = [
                {"role": "user", "content": "[CONTEXT COMPACTION] Summary of work..."},
                {"role": "user", "content": "What should we do next?"},
                {"role": "assistant", "content": "Let me check..."},
                {"role": "user", "content": "new question"},
                {"role": "assistant", "content": "new answer"},
            ]

            # THE BUG: passing the original history as conversation_history
            # causes flush_from = max(200, 0) = 200, skipping everything.
            # After the fix, conversation_history should be None.
            agent._flush_messages_to_session_db(compressed_messages, None)

            new_rows = db.get_messages("compressed-session")
            assert len(new_rows) == 5, (
                f"Expected 5 compressed messages in new session, got {len(new_rows)}. "
                f"Compression persistence bug: messages not written to SQLite."
            )

    def test_flush_with_stale_history_loses_messages(self):
        """Stale conversation_history no longer causes data loss."""
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = SessionDB(db_path=db_path)

            agent = self._make_agent(db)

            # Simulate compression reset
            agent.session_id = "new-session"
            db.create_session(session_id="new-session", source="test")
            agent._last_flushed_db_idx = 0

            compressed = [
                {"role": "user", "content": "summary"},
                {"role": "assistant", "content": "continuing..."},
            ]

            # Stale history longer than messages: the old positional flush
            # sliced past the end and dropped both messages (#46053).
            stale_history = [{"role": "user", "content": f"msg{i}"} for i in range(100)]
            agent._flush_messages_to_session_db(compressed, stale_history)

            rows = db.get_messages("new-session")
            assert len(rows) == 2
            assert [row["content"] for row in rows] == ["summary", "continuing..."]

    def test_in_place_compression_rebaseline_prevents_duplicate_compacted_rows(self):
        """In-place compaction already persisted the compacted transcript.

        Regression for the 2026-06-26 SRE compression loop: archive_and_compact()
        inserted a compacted active block, then the same turn continued with
        conversation_history=None and _flush_messages_to_session_db() appended
        the compacted dicts again, doubling live context.
        """
        from agent.conversation_compression import conversation_history_after_compression
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = SessionDB(db_path=db_path)

            agent = self._make_agent(db)
            agent._ensure_db_session()

            original_history = [
                {"role": "user", "content": "old question"},
                {"role": "assistant", "content": "old answer"},
            ]
            agent._flush_messages_to_session_db(original_history, [])
            assert [row["content"] for row in db.get_messages("original-session")] == [
                "old question",
                "old answer",
            ]

            compacted = [
                {"role": "assistant", "content": "[CONTEXT COMPACTION] summary"},
                {"role": "user", "content": "recent question"},
                {"role": "assistant", "content": "recent answer"},
            ]
            db.archive_and_compact("original-session", compacted)
            setattr(agent, "_last_compaction_in_place", True)
            agent._last_flushed_db_idx = 0

            # Same agent turn continues after compaction. The compacted dicts
            # must be treated as already-persisted history; only later appends
            # should be flushed.
            post_compaction_history = conversation_history_after_compression(
                agent, compacted
            )
            assert post_compaction_history is not None
            assert post_compaction_history is not compacted
            assert post_compaction_history == compacted

            messages = compacted + [
                {"role": "tool", "content": "tool result"},
                {"role": "assistant", "content": "final answer"},
            ]
            agent._flush_messages_to_session_db(messages, post_compaction_history)

            rows = db.get_messages("original-session")
            assert [row["content"] for row in rows] == [
                "[CONTEXT COMPACTION] summary",
                "recent question",
                "recent answer",
                "tool result",
                "final answer",
            ]

    def test_rotation_child_session_flushes_full_compressed_transcript_with_markers(self):
        """Regression for #57491: live cached-agent markers must not block child flush."""
        from agent.conversation_compression import compress_context
        from hermes_state import SessionDB

        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = Path(tmpdir) / "test.db"
            db = SessionDB(db_path=db_path)
            parent_sid = "20260701_152840_parent"
            db.create_session(parent_sid, "gateway", model="test/model")

            agent = self._make_agent(db)
            agent.session_id = parent_sid
            agent.compression_in_place = False
            agent._ensure_db_session()

            # Plain marked messages only: the exact-equality assertion below
            # relies on `compressed` containing no message that _flush filters
            # for a reason INDEPENDENT of _db_persisted (ephemeral scaffolding,
            # synthetic recovery turns). Keep this fixture free of such messages
            # or the row count would legitimately differ from len(compressed).
            messages = [
                {
                    "role": "user" if i % 2 == 0 else "assistant",
                    "content": f"message {i}",
                    "_db_persisted": True,
                }
                for i in range(12)
            ]

            with patch("agent.context_compressor.call_llm", side_effect=RuntimeError("no provider")):
                compressed, _ = compress_context(
                    agent, messages, approx_tokens=100_000, system_message="sys"
                )

            assert agent.session_id != parent_sid
            child_sid = agent.session_id

            agent._flush_messages_to_session_db(compressed, None)

            child_rows = db.get_messages(child_sid)
            assert len(child_rows) == len(compressed), (
                f"Expected {len(compressed)} rows in child session, got {len(child_rows)}. "
                f"_db_persisted marker propagation bug (#57491)."
            )
            db.close()

# ---------------------------------------------------------------------------
# Part 2: Gateway-side — history_offset after session split
# ---------------------------------------------------------------------------

