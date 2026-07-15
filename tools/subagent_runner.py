"""Shared subagent runner for both ``delegate_task`` and ``agent_swarm``.

Extracted from ``tools/delegate_tool.py`` to provide a single code path for
building, running, and resuming subagents.
"""

import json
import threading
from typing import Any, Optional


def build_and_run_subagent(
    goal: str,
    parent_agent: Any,
    context: Optional[str] = None,
    max_iterations: int = 50,
    role: str = "leaf",
    swarm_index: Optional[int] = None,
    swarm_item: Optional[str] = None,
    abort_signal: Optional[threading.Event] = None,
) -> dict:
    """Build a child ``AIAgent`` and run it against *goal*.

    This is the shared entry point for both ``delegate_task`` and
    ``agent_swarm``.  Delegates to ``tools.delegate_tool`` internals for
    the actual agent construction and execution.

    Returns the structured result dict from ``_run_single_child``.
    """
    # Lazy import to avoid circular dependencies and keep startup fast
    from tools.delegate_tool import (
        _build_child_agent,
        _run_child_turn,
    )

    child = _build_child_agent(
        goal=goal,
        context=context,
        parent_agent=parent_agent,
        max_iterations=max_iterations,
        role=role,
    )

    result = _run_child_turn(child, goal, abort_signal)

    # Attach swarm metadata
    if isinstance(result, dict):
        if swarm_index is not None:
            result["swarm_index"] = swarm_index
        if swarm_item is not None:
            result["item"] = swarm_item

    return result


def resume_subagent(
    session_id: str,
    prompt: str,
    parent_agent: Any,
    abort_signal: Optional[threading.Event] = None,
) -> dict:
    """Load an existing subagent session and run a continuation turn.

    Phase 4 implementation — loads the persisted session from SQLite,
    reconstructs the child agent with its conversation history, and
    runs one additional turn with *prompt*.

    Args:
        session_id: The ``session_id`` from a previous subagent run.
        prompt: Continuation prompt (e.g. "continue", "fix the error").
        parent_agent: The parent ``AIAgent`` instance.
        abort_signal: Optional ``threading.Event`` to signal cancellation.

    Returns:
        Structured result dict matching the shape from ``_run_single_child``.
    """
    if not session_id:
        return {
            "status": "error",
            "error": "No session_id provided for resume.",
        }

    try:
        from hermes_state import HermesState

        state = HermesState(parent_agent.hermes_home)
        session = state.get_session_by_session_id(session_id)
        if not session:
            return {
                "status": "error",
                "error": f"Session {session_id} not found",
            }

        # Reconstruct conversation history from persisted messages
        history = state.get_session_messages(session_id)

        from tools.delegate_tool import _build_child_agent, _run_child_turn

        child = _build_child_agent(
            goal=prompt,
            parent_agent=parent_agent,
            session_id=session_id,
            existing_history=history,
        )

        result = _run_child_turn(child, prompt, abort_signal)
        return result if isinstance(result, dict) else {
            "status": "completed",
            "summary": str(result)[:500],
        }
    except Exception as e:
        return {
            "status": "error",
            "error": f"Resume failed: {e}",
        }
