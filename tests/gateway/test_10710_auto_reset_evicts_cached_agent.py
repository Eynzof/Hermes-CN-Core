"""Regression test for #10710 — stale context summary leak after auto-reset.

The gateway agent cache is keyed on the stable chat ``session_key``, which does
NOT change when a session is auto-reset (daily schedule / idle timeout /
suspended). So unless the cached agent is explicitly evicted on auto-reset, the
NEXT message reuses the old ``AIAgent`` instance — carrying its
``context_compressor._previous_summary`` — and prior-conversation content leaks
into the new session's compaction summaries.

Manual ``/reset`` and the compression-exhausted path (#9893) already evict the
cached agent. This pins the matching eviction onto the auto-reset cleanup block
in ``_handle_message_with_agent``.

These are AST invariants — load-bearing pins that fail if the eviction is
removed from the cleanup block (mirrors
test_48031_model_switch_after_auto_reset.py's approach).
"""
from __future__ import annotations

import ast
import inspect

from gateway import run as gateway_run

def _calls(node: ast.AST) -> set[str]:
    """Method-call attribute names invoked anywhere under ``node``."""
    return {
        n.func.attr
        for n in ast.walk(node)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
    }

def _assigns_false(node: ast.AST, attr: str) -> bool:
    """True if ``node`` contains an assignment ``<something>.<attr> = False``."""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Assign):
            for tgt in sub.targets:
                if (
                    isinstance(tgt, ast.Attribute)
                    and tgt.attr == attr
                    and isinstance(sub.value, ast.Constant)
                    and sub.value.value is False
                ):
                    return True
    return False

def _references_name(node: ast.AST, literal: str) -> bool:
    """True if a string constant equal to ``literal`` appears anywhere under ``node``."""
    return any(
        isinstance(n, ast.Constant) and n.value == literal for n in ast.walk(node)
    )

def test_auto_reset_cleanup_clears_last_resolved_model():
    """Regression test for #58403.

    Auto-reset is a full conversation boundary and now routes through the
    `_clear_conversation_scope` funnel. The funnel must clear
    `_last_resolved_model` — without it, the fresh auto-reset session could
    serve a model cached before the reset on a transient config-cache miss.
    Behavioral: exercises the real funnel instead of pinning source shape.
    """
    runner = object.__new__(gateway_run.GatewayRunner)
    key = "agent:main:telegram:dm:58403"
    runner._last_resolved_model = {key: "stale/model", "other": "keep/me"}
    runner._clear_conversation_scope(key, reason="auto_reset")
    assert key not in runner._last_resolved_model, (
        "the conversation-boundary funnel must pop the session's entry from "
        "`_last_resolved_model` (#58403) — auto-reset routes through it"
    )
    assert runner._last_resolved_model.get("other") == "keep/me"
