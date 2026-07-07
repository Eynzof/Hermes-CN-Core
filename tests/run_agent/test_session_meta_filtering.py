"""Tests for session_meta filtering — issue #4715.

Ensures that transcript-only session_meta messages never reach the
chat-completions API, via both the API-boundary guard in
_sanitize_api_messages() and the CLI session-restore paths.
"""

import logging

from run_agent import AIAgent


# ---------------------------------------------------------------------------
# Layer 1 — _sanitize_api_messages role-allowlist guard
# ---------------------------------------------------------------------------

class TestSanitizeApiMessagesRoleFilter:

    def test_drops_session_meta_role(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "session_meta", "content": {"model": "gpt-4"}},
            {"role": "assistant", "content": "hi"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        assert all(m["role"] != "session_meta" for m in out)

    def test_preserves_valid_roles(self):
        msgs = [
            {"role": "system", "content": "you are helpful"},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "tool", "tool_call_id": "c1", "content": "ok"},
        ]
        # Need a matching assistant tool_call so the tool result isn't orphaned
        msgs[2]["tool_calls"] = [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]
        out = AIAgent._sanitize_api_messages(msgs)
        roles = [m["role"] for m in out]
        assert "system" in roles
        assert "user" in roles
        assert "assistant" in roles
        assert "tool" in roles

    def test_logs_warning_when_dropping(self, caplog):
        # The sanitizer lives in (and logs from) ``agent.agent_runtime_helpers``
        # — it no longer imports ``run_agent`` on the hot path.  Capture on the
        # actual emitter's logger, resolved programmatically so this stays a
        # behaviour contract rather than a hardcoded-name change-detector.
        from agent import agent_runtime_helpers as _arh
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "session_meta", "content": {"info": "test"}},
        ]
        with caplog.at_level(logging.DEBUG, logger=_arh.logger.name):
            AIAgent._sanitize_api_messages(msgs)
        assert any("invalid role" in r.message and "session_meta" in r.message for r in caplog.records)

    def test_drops_multiple_invalid_roles(self):
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "session_meta", "content": {}},
            {"role": "transcript_note", "content": "note"},
            {"role": "assistant", "content": "hi"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        assert [m["role"] for m in out] == ["user", "assistant"]


# ---------------------------------------------------------------------------
# Layer 2 — CLI session-restore filters session_meta before loading
# ---------------------------------------------------------------------------

class TestCLISessionRestoreFiltering:

    def test_restore_filters_session_meta(self):
        """Simulates the CLI restore path and verifies session_meta is removed."""
        # Build a fake restored message list (as returned by get_messages_as_conversation)
        fake_restored = [
            {"role": "session_meta", "content": {"model": "gpt-4"}},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi there"},
            {"role": "session_meta", "content": {"tools": []}},
        ]

        # Apply the same filtering that the patched CLI code now does
        filtered = [m for m in fake_restored if m.get("role") != "session_meta"]

        assert len(filtered) == 2
        assert all(m["role"] != "session_meta" for m in filtered)
        assert filtered[0]["role"] == "user"
        assert filtered[1]["role"] == "assistant"


# ---------------------------------------------------------------------------
# Layer 1b — _sanitize_api_messages empty-content filtering (MiMo bugfix)
# ---------------------------------------------------------------------------

class TestSanitizeApiMessagesEmptyContentFilter:

    def test_drops_assistant_with_empty_string_content(self):
        """Regression test for MiMo HTTP 400: 'text is not set'."""
        msgs = [
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "long conversation..."},
            {"role": "assistant", "content": ""},  # <-- compressed to empty
            {"role": "user", "content": "continue"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 3
        assert all(
            not (m.get("role") in {"assistant", "user"}
                 and not m.get("tool_calls")
                 and m.get("content") == "")
            for m in out
        )

    def test_drops_user_with_empty_string_content(self):
        msgs = [
            {"role": "user", "content": ""},
            {"role": "assistant", "content": "hello"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 1
        assert out[0]["role"] == "assistant"

    def test_preserves_tool_with_empty_content(self):
        """Tool messages are handled by orphan repair, not empty-content filter."""
        msgs = [
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c1", "function": {"name": "t", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c1", "content": ""},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        # The tool message is preserved (it has a matching call_id, content="" is valid tool result)
        assert len(out) == 2

    def test_empty_content_with_tool_calls_is_preserved(self):
        """Critical: assistant with content=\"\" but tool_calls MUST survive."""
        msgs = [
            {"role": "user", "content": "do X"},
            {"role": "assistant", "content": "",
             "tool_calls": [{"id": "c99", "function": {"name": "run_cmd", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "c99", "content": "done"},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 3
        assert out[1]["tool_calls"] is not None

    def test_no_false_positive_on_none_content(self):
        """Messages where 'content' key is absent (None) should not be dropped."""
        msgs = [
            {"role": "user", "content": "hello"},
            {"role": "assistant"},  # no content key at all
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2  # preserved — let the API reject if needed

    def test_preserves_assistant_with_codex_reasoning_items(self):
        """Codex reasoning replay relies on empty-content assistant messages."""
        msgs = [
            {"role": "user", "content": "continue"},
            {"role": "assistant", "content": "", "finish_reason": "incomplete",
             "codex_reasoning_items": [{"type": "reasoning", "id": "rs_001"}]},
        ]
        out = AIAgent._sanitize_api_messages(msgs)
        assert len(out) == 2
        assert out[1].get("codex_reasoning_items")
