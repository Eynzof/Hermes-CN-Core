"""Empty-transcript sanitizer: stop the per-send heal/WARNING loop (#96870).

``repair_empty_non_final_messages`` is still the single owner of the
wire-copy substitution. The send-time projection now fills empty non-final
turns first so the owner heals 0 on the main loop (same pattern as #88955).
When a caller still hits the owner repeatedly, logging escalates once per
session window instead of flooding errors.log.
"""

from __future__ import annotations

import logging

import pytest

from agent.agent_runtime_helpers import (
    _INTERRUPTED_PLACEHOLDER,
    _empty_heal_log_state,
    _empty_heal_pending_notice,
    _empty_heal_user_notified,
    consume_pending_sanitizer_heal_notice,
    fill_empty_non_final_wire_payload,
    get_sanitizer_heal_stats,
    repair_empty_non_final_messages,
)
from hermes_logging import clear_session_context, set_session_context


@pytest.fixture(autouse=True)
def _reset_heal_log():
    _empty_heal_log_state.clear()
    _empty_heal_pending_notice.clear()
    _empty_heal_user_notified.clear()
    clear_session_context()
    yield
    _empty_heal_log_state.clear()
    _empty_heal_pending_notice.clear()
    _empty_heal_user_notified.clear()
    clear_session_context()


def _poisoned_rows():
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": ""},
        {"role": "user", "content": "next"},
    ]


class TestFillEmptyNonFinalWirePayload:
    def test_fills_empty_non_final_assistant(self):
        msg = {"role": "assistant", "content": ""}
        assert fill_empty_non_final_wire_payload(msg, is_final=False) is True
        assert msg["content"] == _INTERRUPTED_PLACEHOLDER

    def test_fills_empty_non_final_user(self):
        msg = {"role": "user", "content": None}
        assert fill_empty_non_final_wire_payload(msg, is_final=False) is True
        assert msg["content"] == _INTERRUPTED_PLACEHOLDER

    def test_skips_final_turn(self):
        msg = {"role": "assistant", "content": ""}
        assert fill_empty_non_final_wire_payload(msg, is_final=True) is False
        assert msg["content"] == ""

    def test_skips_tool_call_turn(self):
        msg = {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"id": "c1", "function": {"name": "x", "arguments": "{}"}}],
        }
        assert fill_empty_non_final_wire_payload(msg, is_final=False) is False
        assert msg["content"] == ""

    def test_skips_codex_commentary_carrier(self):
        msg = {
            "role": "assistant",
            "content": "",
            "codex_message_items": [{"type": "text", "text": "hi"}],
        }
        assert fill_empty_non_final_wire_payload(msg, is_final=False) is False
        assert msg["content"] == ""

    def test_skips_already_populated_content(self):
        msg = {"role": "assistant", "content": "hello"}
        assert fill_empty_non_final_wire_payload(msg, is_final=False) is False
        assert msg["content"] == "hello"


class TestHealLogEscalation:
    def test_warning_then_one_error_then_silence(self, monkeypatch, caplog):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 3)
        set_session_context("sess-heal")
        durable = _poisoned_rows()

        with caplog.at_level(logging.WARNING, logger="run_agent"):
            for _ in range(5):
                out = repair_empty_non_final_messages(
                    [dict(m) for m in durable]
                )
                assert out[1]["content"] == _INTERRUPTED_PLACEHOLDER
                assert durable[1]["content"] == ""

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 2
        assert len(errors) == 1
        assert "healed" in warnings[0].getMessage()
        assert "session window" in errors[0].getMessage()
        assert "/new" in errors[0].getMessage()

    def test_sessions_do_not_share_heal_counters(self, monkeypatch, caplog):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 3)
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            set_session_context("sess-a")
            repair_empty_non_final_messages([dict(m) for m in _poisoned_rows()])
            set_session_context("sess-b")
            repair_empty_non_final_messages([dict(m) for m in _poisoned_rows()])

        warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(warnings) == 2
        assert errors == []

    def test_owner_still_heals_wire_copy_only(self):
        durable = _poisoned_rows()
        out = repair_empty_non_final_messages(durable)
        assert out[1]["content"] == _INTERRUPTED_PLACEHOLDER
        assert durable[1]["content"] == ""
        assert out is not durable


class TestOneTimeUserNotice:
    def _heal_n(self, n):
        for _ in range(n):
            repair_empty_non_final_messages(
                [dict(m) for m in _poisoned_rows()]
            )

    def test_notice_queued_once_at_threshold(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 3)
        set_session_context("sess-notice")

        self._heal_n(2)
        assert consume_pending_sanitizer_heal_notice() is None

        self._heal_n(1)  # crosses threshold
        notice = consume_pending_sanitizer_heal_notice()
        assert notice is not None
        assert "repeated repair" in notice
        assert "/debug share" in notice
        assert "hermes doctor" in notice

        # drained: never delivered twice
        assert consume_pending_sanitizer_heal_notice() is None

    def test_notice_never_rearms_in_new_window(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 2)
        set_session_context("sess-rearm")

        self._heal_n(2)
        assert consume_pending_sanitizer_heal_notice() is not None

        # Simulate window expiry: force a fresh window, cross threshold again.
        arh._empty_heal_log_state["sess-rearm"]["window_start"] -= (
            arh._EMPTY_HEAL_WINDOW_S + 1
        )
        self._heal_n(2)
        assert consume_pending_sanitizer_heal_notice() is None

    def test_notice_scoped_to_session(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 2)
        set_session_context("sess-x")
        self._heal_n(2)
        set_session_context("sess-y")
        # sess-y never escalated; its consume must not steal sess-x's notice
        assert consume_pending_sanitizer_heal_notice() is None
        set_session_context("sess-x")
        assert consume_pending_sanitizer_heal_notice() is not None

    def test_threshold_zero_disables_escalation(self, monkeypatch, caplog):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 0)
        set_session_context("sess-off")
        with caplog.at_level(logging.WARNING, logger="run_agent"):
            self._heal_n(6)
        assert consume_pending_sanitizer_heal_notice() is None
        assert not [r for r in caplog.records if r.levelno == logging.ERROR]

    def test_threshold_read_from_config(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(
            "hermes_cli.config.load_config_readonly",
            lambda: {"agent": {"sanitizer_heal_escalation_threshold": 7}},
        )
        assert arh._heal_escalation_threshold() == 7

    def test_threshold_defaults_when_config_unreadable(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        def _boom():
            raise RuntimeError("no config")

        monkeypatch.setattr("hermes_cli.config.load_config_readonly", _boom)
        assert (
            arh._heal_escalation_threshold() == arh._EMPTY_HEAL_ESCALATE_AFTER
        )


class TestHealStatsSurface:
    def test_counters_visible_and_escalation_flagged(self, monkeypatch):
        import agent.agent_runtime_helpers as arh

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 3)
        set_session_context("sess-stats")
        for _ in range(4):
            repair_empty_non_final_messages(
                [dict(m) for m in _poisoned_rows()]
            )

        stats = get_sanitizer_heal_stats()
        assert stats["sess-stats"]["heal_events"] == 4
        assert stats["sess-stats"]["messages_healed"] == 4
        assert stats["sess-stats"]["escalated"] is True

    def test_debug_report_includes_heal_counters(self, monkeypatch):
        import agent.agent_runtime_helpers as arh
        from hermes_cli.debug import collect_debug_report, LogSnapshot

        monkeypatch.setattr(arh, "_heal_escalation_threshold", lambda: 2)
        set_session_context("sess-report")
        for _ in range(2):
            repair_empty_non_final_messages(
                [dict(m) for m in _poisoned_rows()]
            )

        empty = LogSnapshot(path=None, tail_text="", full_text="")
        report = collect_debug_report(
            log_lines=5,
            dump_text="dump",
            log_snapshots={
                k: empty
                for k in ("agent", "errors", "gateway", "gui", "desktop")
            },
        )
        assert "transcript sanitiser heal counters" in report
        assert "sess-report: 2 heal events" in report
        assert "escalated=True" in report


class TestProjectionStopsReheal:
    def _loop_agent(self):
        from unittest.mock import MagicMock, patch

        from run_agent import AIAgent

        with (
            patch("model_tools.get_tool_definitions", return_value=[]),
            patch("model_tools.check_toolset_requirements", return_value={}),
            patch("agent.process_bootstrap.OpenAI"),
        ):
            agent = AIAgent(
                api_key="test-key-1234567890",
                base_url="https://openrouter.ai/api/v1",
                quiet_mode=True,
                skip_context_files=True,
                skip_memory=True,
            )
        agent.client = MagicMock()
        agent._cached_system_prompt = "You are helpful."
        agent._use_prompt_caching = False
        agent.tool_delay = 0
        agent.compression_enabled = False
        agent.save_trajectories = False
        return agent

    def test_unmarked_empty_assistant_row_is_dropped_not_filled(self):
        """[F12 merge reconciliation of upstream #96870 / #88955.]

        Upstream healed an unmarked empty non-final assistant row by substituting
        ``[response interrupted]`` on the per-call copy. The fork's P-024 contract
        (FORK_NOTES) instead DROPS a payload-less ``content == ""`` turn inside
        ``sanitize_api_messages``, and that drop runs BEFORE upstream's substitution step, so
        the row is gone from the wire rather than healed. The merged product therefore keeps
        BOTH invariants, each for the shape it owns:

        * ``content == ""`` with no payload  -> DROPPED (fork P-024) — pinned here;
        * ``content`` missing / ``None``       -> HEALED to ``[response interrupted]``
          (upstream's heal) — pinned by ``test_missing_content_row_is_still_healed`` below.

        Either way the per-turn WARNING spam stays gone (the sanitizer receives no empty
        row, so it heals 0) and durable history is never rewritten.
        """
        from unittest.mock import patch

        import agent.agent_runtime_helpers as _arh

        from tests.agent.test_run_agent import _mock_response

        agent = self._loop_agent()
        agent.client.chat.completions.create.side_effect = [
                _mock_response(content="ok", finish_reason="stop"),
        ]
        sanitizer_healed = []
        _real_repair = _arh.repair_empty_non_final_messages

        def _spy_repair(messages, *a, **k):
            empty = [
                (m.get("role"), m.get("content"))
                for m in messages
                if isinstance(m, dict)
                and m.get("role") in ("user", "assistant")
                and not (m.get("content") or "").strip()
                and not m.get("tool_calls")
            ]
            sanitizer_healed.append(empty)
            return _real_repair(messages, *a, **k)

        history = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": ""},
            {"role": "user", "content": "continue", "finish_reason": "stop"},
            {"role": "assistant", "content": "earlier reply", "finish_reason": "stop"},
        ]
        with (
            patch.object(agent, "_flush_messages_to_session_db"),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(
                _arh, "repair_empty_non_final_messages", side_effect=_spy_repair
            ),
        ):
            agent.run_conversation("next question", conversation_history=history)
        assert sanitizer_healed, "sanitizer was never invoked — test is vacuous"
        for empty in sanitizer_healed:
            assert empty == [], (
                "sanitizer still received an empty non-final row — "
                "the re-heal loop is back (#96870)"
            )
        wire = agent.client.chat.completions.create.call_args.kwargs["messages"]
        wire_assistants = [m for m in wire if m.get("role") == "assistant"]
        # [F12] P-024 dropped the empty row: it is not on the wire and no placeholder was
        # invented for it (upstream's substitution never saw an empty string).
        assert [m["content"] for m in wire_assistants] == ["earlier reply"], (
                wire_assistants
        )
        assert _INTERRUPTED_PLACEHOLDER not in [m.get("content") for m in wire]
        assert all((m.get("content") or "").strip() for m in wire_assistants)
        # ...and the durable row is untouched (no invented assistant prose, no sidecar).
        assert history[1]["content"] == ""
        assert "api_content" not in history[1]

    def test_missing_content_row_is_still_healed(self):
        """[F12] Upstream's substitution heal still owns rows that are empty the OTHER way.

        ``content`` missing (or ``None``) with no payload does not match P-024's drop
        predicate, so ``repair_empty_non_final_messages`` still rewrites it to the neutral
        placeholder instead of letting a provider-400 empty turn through. Same loop, same
        history shape, one key removed — this is the half of upstream's #96870 contract the
        merged product does keep.
        """
        from unittest.mock import patch

        from tests.agent.test_run_agent import _mock_response

        agent = self._loop_agent()
        agent.client.chat.completions.create.side_effect = [
                _mock_response(content="ok", finish_reason="stop"),
        ]
        history = [
            {"role": "user", "content": "start"},
            {"role": "assistant"},  # content key absent: empty the other way
            {"role": "user", "content": "continue", "finish_reason": "stop"},
            {"role": "assistant", "content": "earlier reply", "finish_reason": "stop"},
        ]
        with (
            patch.object(agent, "_flush_messages_to_session_db"),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
        ):
            agent.run_conversation("next question", conversation_history=history)
        wire = agent.client.chat.completions.create.call_args.kwargs["messages"]
        wire_assistants = [m for m in wire if m.get("role") == "assistant"]
        assert wire_assistants[0]["content"] == _INTERRUPTED_PLACEHOLDER
        assert history[1] == {"role": "assistant"}


    def test_pending_notice_delivered_out_of_band_not_in_context(self):
        """A queued escalation notice is emitted through _emit_warning (the
        status/delivery channel) and NEVER appears in the wire messages or
        the durable history — message-flow / caching invariants (#96870)."""
        from unittest.mock import patch

        import agent.agent_runtime_helpers as _arh
        from tests.agent.test_run_agent import _mock_response

        agent = self._loop_agent()
        agent.client.chat.completions.create.side_effect = [
            _mock_response(content="ok", finish_reason="stop"),
        ]

        set_session_context("sess-loop-notice")
        # The turn re-binds the log session context to the agent's own
        # session id at turn start, so queue the notice under that key —
        # exactly where the escalation path would have put it mid-session.
        _live_key = str(getattr(agent, "session_id", None) or "-")
        with _arh._empty_heal_log_lock:
            _arh._empty_heal_user_notified.add(_live_key)
            _arh._empty_heal_pending_notice[_live_key] = (
                "⚠️ Your session transcript required repeated repair — "
                "run /debug share or `hermes doctor`."
            )

        warned = []
        history = [
            {"role": "user", "content": "start"},
            {"role": "assistant", "content": "earlier", "finish_reason": "stop"},
        ]
        with (
            patch.object(agent, "_flush_messages_to_session_db"),
            patch.object(agent, "_persist_session"),
            patch.object(agent, "_save_trajectory"),
            patch.object(agent, "_cleanup_task_resources"),
            patch.object(agent, "_emit_warning", side_effect=warned.append),
        ):
            agent.run_conversation("next", conversation_history=history)

        assert warned and "repeated repair" in warned[0]
        # one-time: drained after delivery
        assert _arh._empty_heal_pending_notice == {}
        # never injected into the wire copy or durable history
        wire = agent.client.chat.completions.create.call_args.kwargs["messages"]
        assert all("repeated repair" not in str(m.get("content")) for m in wire)
        assert all(
            "repeated repair" not in str(m.get("content")) for m in history
        )
