"""Lightweight broker for observing foreground terminal output.

Kept separate from ``terminal_tool`` so the gateway can install its sink during
session setup without importing every terminal environment backend.
"""

from collections.abc import Callable


_sink: Callable[[str, str], None] | None = None


def set_foreground_output_sink(
    sink: Callable[[str, str], None] | None,
) -> None:
    global _sink
    _sink = sink


def has_foreground_output_sink() -> bool:
    return _sink is not None


def emit_foreground_output(tool_call_id: str, chunk: str) -> None:
    sink = _sink
    if sink is None or not tool_call_id or not chunk:
        return
    try:
        sink(tool_call_id, chunk)
    except Exception:
        # Observability must never be able to fail the command itself.
        pass
