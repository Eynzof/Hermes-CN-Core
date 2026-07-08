#!/usr/bin/env python3
"""
Context Engine Tools — ``context_usage`` and ``compact``

These tools are exposed by the active context engine on the agent instance.
They are registered in the central ToolRegistry so they follow the standard
registration-to-discover pipeline, consistent with every other tool.

Dispatch is intercepted at the agent-runtime layer (``invoke_tool`` in
``agent/agent_runtime_helpers.py`` and the sequential path in
``agent/tool_executor.py``) and routed to the per-agent
``context_compressor.handle_tool_call()``.  The registry handler below is a
thin stub that delegates through ``**kwargs`` for the concurrent path, but
the actual execution always flows through the agent-level interceptor.
"""

import orjson
from typing import Any, Dict

from tools.registry import registry, tool_error


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def _handle_context_engine_tool(name: str, args: dict, **kwargs) -> str:
    """Dispatch a context engine tool call via the agent's context compressor.

    Called by ``registry.dispatch()``.  The agent runtime passes
    ``context_compressor=agent.context_compressor`` in ``**kwargs`` when
    the tool is invoked through ``invoke_tool()``; if absent, fall back
    to an error message.
    """
    cc = kwargs.get("context_compressor")
    if cc is None:
        return tool_error(
            "Context engine not available. "
            "Make sure a context engine is active on this agent."
        )
    return cc.handle_tool_call(name, args, **kwargs)


def _context_usage_handler(args: dict, **kwargs) -> str:
    """Handler for the ``context_usage`` tool."""
    return _handle_context_engine_tool("context_usage", args, **kwargs)


def _compact_handler(args: dict, **kwargs) -> str:
    """Handler for the ``compact`` tool.

    Note: the actual compression lifecycle is performed by the agent's
    sequential tool-executor path (``tool_executor.py``), which detects
    ``function_name == "compact"`` and calls ``_compress_context()`` inline.
    This handler only validates and acknowledges the request.
    """
    return _handle_context_engine_tool("compact", args, **kwargs)


# ---------------------------------------------------------------------------
# Availability check
# ---------------------------------------------------------------------------

def check_context_engine_requirements() -> bool:
    """Context engine tools have no external requirements — always available.

    Availability is gated at the toolset level by ``enabled_toolsets``
    in the agent's configuration, not by external environmental probes.
    """
    return True


# ---------------------------------------------------------------------------
# Schemas (imported from agent/context_tools for single-source-of-truth)
# ---------------------------------------------------------------------------

def _get_schemas() -> Dict[str, Dict[str, Any]]:
    """Return schema dicts from the canonical source."""
    from agent.context_tools import get_compact_schema, get_context_usage_schema
    return {
        "context_usage": get_context_usage_schema(),
        "compact": get_compact_schema(),
    }


# ---------------------------------------------------------------------------
# Registry registration
# ---------------------------------------------------------------------------

_schemas = _get_schemas()

registry.register(
    name="context_usage",
    toolset="context_engine",
    schema=_schemas["context_usage"],
    handler=_context_usage_handler,
    check_fn=check_context_engine_requirements,
    emoji="📊",
)

registry.register(
    name="compact",
    toolset="context_engine",
    schema=_schemas["compact"],
    handler=_compact_handler,
    check_fn=check_context_engine_requirements,
    emoji="🗜️",
)
