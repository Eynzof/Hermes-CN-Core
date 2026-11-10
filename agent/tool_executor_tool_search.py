"""Deferred tool-call unwrapping and session-scope checks."""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

def _tool_search_scoped_names(agent) -> frozenset:
    """Deferrable tool names the session may invoke via ``tool_call``; the unwrap bypasses
    the bridge's scope check in ``model_tools.handle_function_call``, so restricted sessions
    validate against this set. Read through the ``get_tool_definitions`` memo on every call
    and never copied onto the agent: the memo expires with the check_fn verdicts it was
    filtered on, a per-agent copy keyed on registry generation would not, and a recovered
    MCP server's tools would stay uncallable for the agent's lifetime."""
    try:
        import model_tools
        from tools import tool_search as _ts
    except Exception:
        logger.debug("Deferred tool scope lookup failed", exc_info=True)
        return frozenset()
    try:
        return _ts.scoped_deferrable_names(model_tools.get_tool_definitions(
            enabled_toolsets=getattr(agent, "enabled_toolsets", None),
            disabled_toolsets=getattr(agent, "disabled_toolsets", None),
            quiet_mode=True, skip_tool_search_assembly=True,
        ) or [])
    except Exception:
        logger.debug("Deferred tool scope lookup failed", exc_info=True)
        return frozenset()



def _unwrap_tool_search_call(
    agent, function_name: str, function_args: dict, *, flatten_probe: bool = False
) -> tuple[str, dict, Optional[str]]:
    """Peel the ``tool_call`` bridge so downstream hooks (checkpointing, guardrails, plugin
    hooks, activity feed) see the underlying tool; ``tool_call.function`` stays untouched for
    the transcript and tool_call_id pairing.

    The unwrap bypasses handle_function_call's scope check, so session toolset scope is
    enforced HERE. Returns ``(name, args, scope_block)``; ``scope_block`` is the block
    message when the underlying tool is out of scope or its args fail the deferred-schema
    probe (``flatten_probe`` collapses the probe's JSON payload to one plain string for
    callers that wrap the message in ``{"error": ...}``).
    """
    scope_block: Optional[str] = None
    try:
        from tools import tool_search as _ts
        if function_name != _ts.TOOL_CALL_NAME:
            return function_name, function_args, None
        underlying, underlying_args, err = _ts.resolve_underlying_call(function_args)
        if err or not underlying:
            return function_name, function_args, None
        if underlying == _ts.CONNECTOR_BATCH_SENTINEL:
            # Both executors retain the wrapper: scope/probe/hooks run per entry
            # in the batch dispatcher, not against a synthetic registry name.
            return function_name, function_args, None
        if underlying not in _tool_search_scoped_names(agent):
            # Session-gated GUI tools name their missing surface (#120413);
            # anything else keeps the generic block.
            return function_name, function_args, _ts.scope_block_reason(underlying)
        # Validate before unwrapping: the generic bridge hides the concrete
        # parameter schema from provider-native tool-call validation.
        scope_block = _ts.validate_deferred_call_args(underlying, underlying_args)
        if scope_block is None:
            return underlying, underlying_args, None
        if flatten_probe:
            probe = json.loads(scope_block)
            scope_block = (
                f"{probe.get('error', '')} Parameters schema: "
                f"{json.dumps(probe.get('parameters', {}), ensure_ascii=False)}. "
                f"{probe.get('hint', '')}"
            ).strip()
    except Exception:
        logger.debug("Deferred tool unwrapping failed", exc_info=True)
    return function_name, function_args, scope_block
