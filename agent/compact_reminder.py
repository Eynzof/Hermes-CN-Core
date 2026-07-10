"""Compact reminder provider — injects a hint to call Compact when context > threshold."""

from __future__ import annotations

from typing import Any, Dict, List

from agent.system_reminder import SystemReminder, SystemReminderProvider

_COMPACT_REMINDER_TYPE = "compact_reminder"

_COMPACT_REMINDER_TEMPLATE = (
    "Context {usage:.0%} full ({tokens}/{max_tokens} tokens). "
    "Call `Compact` to free space before auto-compaction forces it. "
    "Optionally pass an instruction to guide what to preserve."
)


class CompactReminderProvider(SystemReminderProvider):
    """Injects a context-compaction reminder when context usage exceeds a threshold."""

    def __init__(
        self,
        threshold: float = 0.70,
        cooldown_steps: int = 5,
    ) -> None:
        self._threshold = threshold
        self._cooldown_steps = cooldown_steps
        self._last_injected_step: int | None = None
        self._last_injected_usage: float = 0.0

    def get_reminders(
        self,
        agent: Any,
        api_call_count: int,
    ) -> List[SystemReminder]:
        # 1. Skip if context compressor not available
        cc = getattr(agent, "context_compressor", None)
        if cc is None:
            return []

        # 2. Get current usage status
        status: Dict[str, Any] = cc.get_usage_status()
        usage_percent: float = status.get("usage_percent", 0.0)  # 0-100
        context_usage: float = usage_percent / 100.0  # convert to 0-1 ratio
        used_tokens: int = status.get("used_tokens", 0)
        max_tokens: int = status.get("max_context_tokens", 0)

        # 3. Check threshold
        if context_usage < self._threshold:
            return []

        # 4. Throttle: skip if cooldown hasn't elapsed or usage hasn't grown
        if self._last_injected_step is not None:
            steps_since = api_call_count - self._last_injected_step
            usage_growth = context_usage - self._last_injected_usage
            if steps_since <= self._cooldown_steps or usage_growth < 0.05:
                return []

        # 5. Record injection and return reminder
        self._last_injected_step = api_call_count
        self._last_injected_usage = context_usage

        content = _COMPACT_REMINDER_TEMPLATE.format(
            usage=context_usage,
            tokens=used_tokens,
            max_tokens=max_tokens,
        )
        return [SystemReminder(type=_COMPACT_REMINDER_TYPE, content=content)]

    def on_context_compacted(self, agent: Any) -> None:
        """Reset throttling so the reminder can fire again after compaction."""
        self._last_injected_step = None
        self._last_injected_usage = 0.0
