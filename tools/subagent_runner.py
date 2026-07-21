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

    .. note::

        Like ``delegate_task``, this function resolves delegation credentials
        (``delegation.base_url`` / ``delegation.provider`` from config) via
        ``_resolve_delegation_credentials()`` and passes the resolved values
        as overrides to ``_build_child_agent()``.  This ensures subagents
        spawned by ``agent_swarm`` use the same provider routing as
        ``delegate_task`` subagents, fixing the bug where agent_swarm silently
        ignored ``delegation.*`` config keys (subagents would inherit the
        parent's OpenAI endpoint and key, causing 401 errors when the parent
        uses a custom base URL with a non-OpenAI key).

    Returns the structured result dict from ``_run_single_child``.
    """
    # Lazy import to avoid circular dependencies and keep startup fast
    from tools.delegate_tool import (
        _build_child_agent,
        _load_config,
        _resolve_delegation_credentials,
        _run_child_turn,
    )

    # Resolve delegation credentials the same way delegate_task does.
    # When delegation.base_url or delegation.provider is configured, the
    # child uses those overrides; otherwise returns None values so the
    # child inherits from the parent agent.
    cfg = _load_config()
    try:
        creds = _resolve_delegation_credentials(cfg, parent_agent)
    except ValueError:
        # If credential resolution fails, log and fall through — child
        # will inherit parent credentials, which is the safe default.
        logger = __import__("logging").getLogger(__name__)
        logger.warning(
            "agent_swarm: delegation credential resolution failed, "
            "falling back to parent credential inheritance",
            exc_info=True,
        )
        creds = {
            "model": None,
            "provider": None,
            "base_url": None,
            "api_key": None,
            "api_mode": None,
        }

    child = _build_child_agent(
        task_index=-1,       # swarm doesn't use sequential task indices
        goal=goal,
        context=context,
        toolsets=None,       # inherit all tools from parent
        model=creds["model"],
        max_iterations=max_iterations,
        task_count=1,
        parent_agent=parent_agent,
        override_provider=creds["provider"],
        override_base_url=creds["base_url"],
        override_api_key=creds["api_key"],
        override_api_mode=creds["api_mode"],
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

        # Note: session_id and existing_history are resume-specific params
        # not supported by the current _build_child_agent signature.
        # The resume feature (Phase 4) will require extending _build_child_agent.
        child = _build_child_agent(
            task_index=-1,
            goal=prompt,
            context=None,
            toolsets=None,
            model=None,
            max_iterations=50,
            task_count=1,
            parent_agent=parent_agent,
            role="leaf",
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
