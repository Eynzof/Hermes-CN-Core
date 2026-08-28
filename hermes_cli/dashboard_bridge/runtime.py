"""Thread-safe, non-replaying conversation lifecycle for one bridge session."""

from __future__ import annotations

import inspect
import logging
import threading
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any, Callable, ContextManager

_log = logging.getLogger(__name__)

MAX_HISTORY_MESSAGES = 32
MAX_HISTORY_CONTENT_CHARS = 16_384
MAX_RESPONSE_CHARS = 64 * 1024

Emit = Callable[[str, dict], None]


@dataclass
class _Turn:
    correlation_id: str
    input_text: str
    emit: Emit
    discarded: bool = False
    agent: Any = None


class ConversationRuntime:
    """Run at most one turn and keep its ownership until the worker exits.

    ``discard`` requests an interrupt but intentionally does not clear
    ``_in_flight``. A provider may return later, and accepting a new turn before
    that happens would run two conversations through the same agent/session.
    """

    def __init__(
        self,
        agent_factory: Callable[..., Any],
        *,
        context_factory: Callable[[], ContextManager[Any]] | None = None,
        history_limit: int = MAX_HISTORY_MESSAGES,
        max_history_content_chars: int = MAX_HISTORY_CONTENT_CHARS,
    ) -> None:
        self._agent_factory = agent_factory
        self._context_factory = context_factory
        self._history_limit = max(2, int(history_limit))
        self._max_history_content_chars = max(1, int(max_history_content_chars))
        self._lock = threading.RLock()
        self._idle = threading.Condition(self._lock)
        self._in_flight_turn: _Turn | None = None
        self._history: list[dict[str, str]] = []
        self._closed = False

    def submit(self, input_text: str, correlation_id: str, emit: Emit) -> bool:
        if not isinstance(input_text, str) or not input_text.strip():
            return False
        if not isinstance(correlation_id, str) or not correlation_id:
            return False
        turn = _Turn(correlation_id, input_text, emit)
        with self._lock:
            if self._closed or self._in_flight_turn is not None:
                return False
            self._in_flight_turn = turn
        threading.Thread(
            target=self._run_turn,
            args=(turn,),
            name=f"dashboard-bridge-{correlation_id[:24]}",
            daemon=True,
        ).start()
        return True

    def discard(self, correlation_id: str) -> bool:
        with self._lock:
            turn = self._in_flight_turn
            if turn is None or turn.correlation_id != correlation_id:
                return False
            turn.discarded = True
            agent = turn.agent
        if agent is not None:
            self._interrupt(agent)
        return True

    def close(self) -> None:
        with self._lock:
            self._closed = True
            turn = self._in_flight_turn
            if turn is not None:
                turn.discarded = True
                agent = turn.agent
            else:
                agent = None
        if agent is not None:
            self._interrupt(agent)

    def in_flight(self, correlation_id: str | None = None) -> bool:
        with self._lock:
            turn = self._in_flight_turn
            return turn is not None and (
                correlation_id is None or turn.correlation_id == correlation_id
            )

    def wait_idle(self, timeout: float | None = None) -> bool:
        with self._idle:
            if self._in_flight_turn is None:
                return True
            self._idle.wait_for(lambda: self._in_flight_turn is None, timeout)
            return self._in_flight_turn is None

    @property
    def history(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(message) for message in self._history]

    def _run_turn(self, turn: _Turn) -> None:
        streamed: list[str] = []
        result: Any = None
        try:
            context = (
                self._context_factory() if self._context_factory else nullcontext()
            )
            with context:
                agent = self._make_agent(turn.correlation_id)
                with self._lock:
                    turn.agent = agent
                    discarded = turn.discarded or self._closed
                if discarded:
                    self._interrupt(agent)

                def stream_callback(value: Any) -> None:
                    if value is None:
                        return
                    text = str(value)
                    if not text:
                        return
                    streamed.append(text)
                    self._emit(turn, "message.delta", {"delta": text})

                history = self._history_snapshot()
                result = agent.run_conversation(
                    user_message=turn.input_text,
                    conversation_history=history or None,
                    task_id=turn.correlation_id,
                    stream_callback=stream_callback,
                )

            final_text, status = self._result_text_and_status(result)
            if status == "completed":
                if not streamed and final_text:
                    stream_callback(final_text)
                self._emit(
                    turn,
                    "message.complete",
                    {"status": "completed"},
                )
                if not turn.discarded:
                    self._append_history(turn.input_text, "user")
                    self._append_history(final_text, "assistant")
            elif status == "interrupted":
                self._emit(turn, "task.failed", {"status": "interrupted"})
            else:
                self._emit(turn, "error", {"reason": "conversation-failed"})
        except Exception as exc:
            # Exception details may contain provider output or user data. The
            # bridge deliberately exports only a stable error class.
            _log.debug("Dashboard bridge turn failed: %s", type(exc).__name__)
            self._emit(turn, "error", {"reason": "conversation-failed"})
        finally:
            with self._idle:
                if self._in_flight_turn is turn:
                    self._in_flight_turn = None
                    self._idle.notify_all()

    def _make_agent(self, correlation_id: str) -> Any:
        try:
            signature = inspect.signature(self._agent_factory)
            accepts_argument = any(
                parameter.kind
                in (parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD)
                for parameter in signature.parameters.values()
            )
        except (TypeError, ValueError):
            accepts_argument = False
        if accepts_argument:
            return self._agent_factory(correlation_id)
        return self._agent_factory()

    def _emit(self, turn: _Turn, event_type: str, payload: dict) -> None:
        with self._lock:
            if self._in_flight_turn is not turn or turn.discarded or self._closed:
                return
            turn.emit(event_type, dict(payload))

    def _history_snapshot(self) -> list[dict[str, str]]:
        with self._lock:
            return [dict(message) for message in self._history]

    def _append_history(self, content: str, role: str) -> None:
        if not content:
            return
        bounded = str(content)[: self._max_history_content_chars]
        with self._lock:
            self._history.extend(({"role": role, "content": bounded},))
            self._history = self._history[-self._history_limit :]

    @staticmethod
    def _result_text_and_status(result: Any) -> tuple[str, str]:
        if isinstance(result, dict):
            if result.get("interrupted") is True:
                return "", "interrupted"
            if result.get("failed") is True:
                return "", "failed"
            value = result.get("final_response", "")
        else:
            value = result
        return str(value or "")[:MAX_RESPONSE_CHARS], "completed"

    @staticmethod
    def _interrupt(agent: Any) -> None:
        interrupt = getattr(agent, "interrupt", None)
        if not callable(interrupt):
            return
        try:
            interrupt()
        except Exception:
            _log.debug("Dashboard bridge interrupt failed", exc_info=True)
