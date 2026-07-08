"""Tool call deduplication tracker.

Detects and stops consecutive identical tool calls across API iterations
to prevent infinite loops where the agent calls the same tool with the
same arguments repeatedly without making progress.
"""

from __future__ import annotations

import orjson
from typing import Any, Optional, NamedTuple


# ── Reminder texts ──────────────────────────────────────────────────────

_REMINDER_TEXT_1 = (
    "\n\n<system-reminder>\n"
    "You are repeating the exact same tool call with identical parameters."
    " Please carefully analyze the previous result. If the task is not yet complete,"
    " try a different method or parameters instead of repeating the same call."
    "\n</system-reminder>"
)


def _make_reminder_text_2(tool_name: str, repeat_count: int, canonical_args: str) -> str:
    """Stronger reminder that names the tool, count, and arguments."""
    return (
        "\n\n<system-reminder>\n"
        "You have repeatedly called the same tool with identical parameters many times.\n"
        "Repeated tool call detected:\n"
        f"- tool: {tool_name}\n"
        f"- repeated_times: {repeat_count}\n"
        f"- arguments: {canonical_args}\n"
        "The previous repeated calls did not make progress. Do not call this exact same tool "
        "with the exact same arguments again.\n"
        "Carefully inspect the latest tool result and choose a different next action, "
        "different parameters, or finish the task if enough evidence has been gathered."
        "\n</system-reminder>"
    )


# ── Result type ─────────────────────────────────────────────────────────

class DedupResult(NamedTuple):
    """Result of a deduplication check for a single tool call."""
    is_cross_step_dup: bool
    reminder_text: Optional[str]
    repeat_count: int


# ── Normalization helpers ───────────────────────────────────────────────

def _sort_json_value(value: object) -> object:
    """Recursively sort JSON values for canonical comparison."""
    if isinstance(value, list):
        return [_sort_json_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _sort_json_value(value[key]) for key in sorted(value)}
    return value


def _canonical_tool_arguments(arguments: Any) -> str:
    """Produce a canonical string representation of tool arguments.

    Sorts JSON keys recursively. Falls back to str() for non-JSON values.
    """
    try:
        return orjson.dumps(_sort_json_value(arguments), option=orjson.OPT_SORT_KEYS).decode('utf-8')
    except (TypeError, ValueError):
        return str(arguments)


def _canonical_tool_arguments_text(arguments: str) -> str:
    """Produce canonical args string from a JSON string."""
    try:
        return _canonical_tool_arguments(orjson.loads(arguments))
    except (orjson.JSONDecodeError, TypeError, ValueError):
        return arguments


def _normalize_call_key(tool_name: str, arguments: str | dict) -> tuple[str, str]:
    """Return a normalized call key: (tool_name, canonical_args_string)."""
    if isinstance(arguments, dict):
        return (tool_name, _canonical_tool_arguments(arguments))
    return (tool_name, _canonical_tool_arguments_text(arguments))


def _append_reminder_to_result(result: str, reminder: str) -> str:
    """Append a reminder text to a tool result string."""
    if isinstance(result, str):
        return result + reminder
    return result  # Non-string results pass through unchanged


# ── ToolCallKey type alias ──────────────────────────────────────────────
ToolCallKey = tuple[str, str]


# ── ToolDedupTracker ────────────────────────────────────────────────────

class ToolDedupTracker:
    """Tracks tool calls across API iterations to detect and stop
    consecutive identical tool calls (infinite loops).

    Usage:
        # Per-API-iteration lifecycle:
        tracker.begin_step(previous_calls=..., step_no=1, turn_id="...")
        # ... during each tool execution:
        dedup = tracker.check_and_register(tool_name, arguments)
        if dedup.reminder_text:
            result = _append_reminder_to_result(result, dedup.reminder_text)
        # ... after all tool results collected:
        previous_calls = tracker.end_step()
    """

    def __init__(self) -> None:
        # Cross-step state
        self._previous_step_calls: list[ToolCallKey] = []
        self._current_step_calls: list[ToolCallKey] = []
        self._seen_call_keys: set[ToolCallKey] = set()
        self._consecutive_key: ToolCallKey | None = None
        self._consecutive_count: int = 0
        self._step_closed: bool = False
        self._dedup_triggered: bool = False
        self._step_no: int = 0
        self._turn_id: str = ""
        # Thread-safety: protect check_and_register for concurrent tool execution
        import threading
        self._lock = threading.Lock()

    # ── Step lifecycle ──────────────────────────────────────────────────

    def begin_step(
        self,
        previous_calls: Optional[list[tuple[str, str]]] = None,
        step_no: int = 0,
        turn_id: str = "",
    ) -> None:
        """Called BEFORE each API call. Seeds state from previous step.

        Args:
            previous_calls: list of (tool_name, canonical_args) from the
                previous step, as returned by end_step(). If None, uses
                the internally tracked previous step calls.
            step_no: current API call iteration number.
            turn_id: conversation turn identifier.
        """
        if previous_calls is not None:
            self._previous_step_calls = [
                _normalize_call_key(tool_name, arguments)
                for tool_name, arguments in previous_calls
            ]
        self._current_step_calls = []
        self._step_closed = False
        self._dedup_triggered = False
        self._step_no = step_no
        self._turn_id = turn_id
        if not self._previous_step_calls:
            self._seen_call_keys = set()
            self._consecutive_key = None
            self._consecutive_count = 0
        else:
            self._seen_call_keys.update(self._previous_step_calls)
            if self._consecutive_key is None and self._consecutive_count == 0:
                self._advance_consecutive_streak(self._previous_step_calls)

    def end_step(self) -> list[tuple[str, str]]:
        """Called AFTER all tool results are collected for this step.

        Returns:
            The list of (tool_name, canonical_args) calls made in this step,
            for passing to the next begin_step().
        """
        if not self._step_closed:
            self._advance_consecutive_streak(self._current_step_calls)
            self._seen_call_keys.update(self._current_step_calls)
            self._step_closed = True
        return list(self._current_step_calls)

    # ── Streak tracking ─────────────────────────────────────────────────

    def _advance_consecutive_streak(self, calls: list[ToolCallKey]) -> None:
        """Update consecutive_key and consecutive_count from a list of calls."""
        for call_key in calls:
            if call_key == self._consecutive_key:
                self._consecutive_count += 1
            else:
                self._consecutive_key = call_key
                self._consecutive_count = 1

    def _projected_streak_for_call(self, call_index: int) -> int:
        """Predict what the streak count will be for the call at the given index.

        Simulates _advance_consecutive_streak through the current step's calls
        up to and including call_index.
        """
        consecutive_key = self._consecutive_key
        consecutive_count = self._consecutive_count
        for call_key in self._current_step_calls[: call_index + 1]:
            if call_key == consecutive_key:
                consecutive_count += 1
            else:
                consecutive_key = call_key
                consecutive_count = 1
        return consecutive_count

    # ── Dedup check ─────────────────────────────────────────────────────

    def check_and_register(
        self,
        tool_name: str,
        arguments: dict,
    ) -> DedupResult:
        """Check for cross-step duplicates and register this call.

        Call this during tool execution, before the result is finalized.
        When a cross-step duplicate is detected at repeat counts 3, 5, or 8,
        a reminder text is returned that should be appended to the tool result.

        Args:
            tool_name: The tool/function name.
            arguments: The parsed tool arguments dict.

        Returns:
            DedupResult with is_cross_step_dup, reminder_text, and repeat_count.
        """
        canonical_args = _canonical_tool_arguments(arguments)
        call_key = (tool_name, canonical_args)

        with self._lock:
            call_index = len(self._current_step_calls)
            self._current_step_calls.append(call_key)

            is_cross_step_dup = call_key in self._seen_call_keys
            reminder_text: Optional[str] = None

            if is_cross_step_dup:
                self._dedup_triggered = True
                repeat_count = self._projected_streak_for_call(call_index)
                if repeat_count == 3:
                    reminder_text = _REMINDER_TEXT_1
                elif repeat_count in (5, 8):
                    reminder_text = _make_reminder_text_2(
                        tool_name, repeat_count, canonical_args
                    )

                return DedupResult(
                    is_cross_step_dup=True,
                    reminder_text=reminder_text,
                    repeat_count=repeat_count,
                )

            return DedupResult(
                is_cross_step_dup=False,
                reminder_text=None,
                repeat_count=0,
            )

    @property
    def dedup_triggered(self) -> bool:
        """Whether cross-step dedup was triggered in the current step."""
        return self._dedup_triggered
