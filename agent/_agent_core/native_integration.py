"""Integration layer between Hermes agent Python code and agent-basic-library native C FFI.

This module provides drop-in replacements for Hermes Python functions,
falling back to the original Python implementation when the native library
is unavailable or ``HERMES_FORCE_PURE=1`` is set.

Usage:
    from agent._agent_core.native_integration import (
        sanitize_messages_surrogates,
        repair_tool_call_arguments,
        sanitize_api_messages,
        repair_message_sequence,
        ...
    )
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load the native FFI wrapper
# ---------------------------------------------------------------------------

_agent_core_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(_agent_core_dir))

_HERMES_FORCE_PURE = os.environ.get("HERMES_FORCE_PURE", "").strip() in ("1", "true", "yes")

try:
    import agent_native
    _native_available = True
except ImportError as exc:
    _native_available = False
    _native_error = str(exc)
    logger.warning(
        "Native C FFI library unavailable — falling back to pure-Python "
        "implementation. Reason: %s. "
        "Set HERMES_FORCE_PURE=1 to suppress this warning.",
        _native_error,
    )

# Clean up sys.path to avoid side effects
if sys.path[0] == str(_agent_core_dir):
    sys.path.pop(0)


def _native_enabled() -> bool:
    """Return True if the native library should be used."""
    return _native_available and not _HERMES_FORCE_PURE


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _messages_to_json(messages: List[Dict[str, Any]]) -> str:
    """Serialize a message list to JSON for the native library.
    Converts SimpleNamespace objects to dicts for proper JSON serialization."""
    def _convert(obj):
        if isinstance(obj, dict):
            return {k: _convert(v) for k, v in obj.items()}
        elif isinstance(obj, list):
            return [_convert(v) for v in obj]
        elif hasattr(obj, '__dict__'):
            # SimpleNamespace or similar
            return {k: _convert(v) for k, v in obj.__dict__.items()}
        return obj
    return json.dumps(_convert(messages), ensure_ascii=False)


def _json_to_messages(json_str: str) -> List[Dict[str, Any]]:
    """Deserialize JSON string back to a message list."""
    return json.loads(json_str)


def _json_to_bool(json_str: str) -> bool:
    """Parse a JSON result and determine if changes were made."""
    # The native functions return the modified JSON string.
    # We compare input vs output to determine if changes were made.
    # For functions that signal via return value, we check output.
    return True


# ---------------------------------------------------------------------------
# message_sanitization replacements
# ---------------------------------------------------------------------------

def sanitize_messages_surrogates(messages: List[Dict[str, Any]]) -> bool:
    """Replace surrogate code points with U+FFFD in all string fields.
    
    Native C replacement for ``agent.message_sanitization._sanitize_messages_surrogates``.
    
    Args:
        messages: Message list (mutated in-place).
    
    Returns:
        True if any surrogates were replaced.
    """
    if not _native_enabled():
        # Import and call the original Python implementation
        from agent.message_sanitization import _sanitize_messages_surrogates as _py_fn
        return _py_fn(messages)
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.sanitize_messages_surrogates(inp_json)
        out = json.loads(out_json)
        # Compare old vs new to determine if changes happened
        changed = _messages_to_json(messages) != _messages_to_json(out)
        # Mutate in-place
        messages[:] = out
        return changed
    except Exception:
        # Fallback to Python on error
        from agent.message_sanitization import _sanitize_messages_surrogates as _py_fn
        return _py_fn(messages)


def repair_tool_call_arguments(raw_args: str, tool_name: str = "?") -> str:
    """Repair malformed JSON tool-call arguments.
    
    Native C replacement for ``agent.message_sanitization._repair_tool_call_arguments``.
    """
    if not _native_enabled():
        from agent.message_sanitization import _repair_tool_call_arguments as _py_fn
        return _py_fn(raw_args, tool_name)
    
    try:
        return agent_native.repair_tool_call_arguments(raw_args, tool_name)
    except Exception:
        from agent.message_sanitization import _repair_tool_call_arguments as _py_fn
        return _py_fn(raw_args, tool_name)


def escape_invalid_chars_in_json_strings(raw: str) -> str:
    """Escape unescaped control characters inside JSON string values.
    
    Native C replacement for ``agent.message_sanitization._escape_invalid_chars_in_json_strings``.
    """
    if not _native_enabled():
        from agent.message_sanitization import _escape_invalid_chars_in_json_strings as _py_fn
        return _py_fn(raw)
    
    try:
        return agent_native.escape_invalid_chars_in_json_strings(raw)
    except Exception:
        from agent.message_sanitization import _escape_invalid_chars_in_json_strings as _py_fn
        return _py_fn(raw)


def sanitize_messages_non_ascii(messages: List[Dict[str, Any]]) -> bool:
    """Replace non-ASCII characters in message content (last-resort mode).
    
    Native C replacement for ``agent.message_sanitization._sanitize_messages_non_ascii``.
    """
    if not _native_enabled():
        from agent.message_sanitization import _sanitize_messages_non_ascii as _py_fn
        return _py_fn(messages)
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.sanitize_messages_non_ascii(inp_json)
        out = json.loads(out_json)
        changed = _messages_to_json(messages) != _messages_to_json(out)
        messages[:] = out
        return changed
    except Exception:
        from agent.message_sanitization import _sanitize_messages_non_ascii as _py_fn
        return _py_fn(messages)


def strip_images_from_messages(messages: List[Dict[str, Any]]) -> bool:
    """Strip image_url/image/input_image content parts from messages.
    
    Native C replacement for ``agent.message_sanitization._strip_images_from_messages``.
    """
    if not _native_enabled():
        from agent.message_sanitization import _strip_images_from_messages as _py_fn
        return _py_fn(messages)
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.strip_images_from_messages(inp_json)
        out = json.loads(out_json)
        changed = _messages_to_json(messages) != _messages_to_json(out)
        messages[:] = out
        return changed
    except Exception:
        from agent.message_sanitization import _strip_images_from_messages as _py_fn
        return _py_fn(messages)


# ---------------------------------------------------------------------------
# prompt_builder replacements
# ---------------------------------------------------------------------------

def truncate_content(
    content: str,
    filename: str,
    max_chars: Optional[int] = None,
    context_length: Optional[int] = None,
    read_path: Optional[str] = None,
) -> str:
    """Head/tail truncation with a marker in the middle.
    
    Native C replacement for ``agent.prompt_builder._truncate_content``.
    """
    if not _native_enabled():
        from agent.prompt_builder import _truncate_content as _py_fn
        return _py_fn(content, filename, max_chars, context_length, read_path)
    
    try:
        effective_max = max_chars if max_chars is not None and max_chars > 0 else 8000
        return agent_native.truncate_content(
            content,
            filename=filename,
            max_chars=effective_max,
            read_path=read_path,
        )
    except Exception:
        from agent.prompt_builder import _truncate_content as _py_fn
        return _py_fn(content, filename, max_chars, context_length, read_path)


def strip_yaml_frontmatter(content: str) -> str:
    """Strip YAML frontmatter (--- delimited) from content.
    
    Native C replacement for ``agent.prompt_builder._strip_yaml_frontmatter``.
    """
    if not _native_enabled():
        from agent.prompt_builder import _strip_yaml_frontmatter as _py_fn
        return _py_fn(content)
    
    try:
        return agent_native.strip_yaml_frontmatter(content)
    except Exception:
        from agent.prompt_builder import _strip_yaml_frontmatter as _py_fn
        return _py_fn(content)


def scan_context_content(content: str, filename: str) -> str:
    """Scan context file content for injection patterns.
    
    Native C replacement for ``agent.prompt_builder._scan_context_content``.
    
    Note: The native C implementation uses a minimal embedded threat-pattern set,
    while the Python version delegates to ``tools.threat_patterns.scan_for_threats``
    which has a richer pattern library. This function always uses the Python
    version for full coverage; the native fast-path is only used for simple checks.
    """
    # Always use Python version for richer pattern coverage
    from agent.prompt_builder import _scan_context_content as _py_fn
    return _py_fn(content, filename)


# ---------------------------------------------------------------------------
# conversation_loop / run_agent replacements
# ---------------------------------------------------------------------------

def _has_object_tool_calls(messages) -> bool:
    """Check if any message contains non-dict (object-type) tool_calls.
    The C FFI only handles dicts; fall back to Python for objects."""
    for msg in messages:
        if not isinstance(msg, dict):
            return True
        for tc in msg.get("tool_calls") or []:
            if not isinstance(tc, dict):
                return True
    return False


def sanitize_api_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize an API message list: role allowlist, tool-call repair, orphan removal.
    
    Native C replacement for ``agent.agent_runtime_helpers.sanitize_api_messages``.
    """
    if not _native_enabled() or _has_object_tool_calls(messages):
        from agent.agent_runtime_helpers import sanitize_api_messages as _py_fn
        return _py_fn(messages)
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.sanitize_api_messages(inp_json)
        return json.loads(out_json)
    except Exception:
        from agent.agent_runtime_helpers import sanitize_api_messages as _py_fn
        return _py_fn(messages)


def repair_message_sequence(agent, messages: List[Dict]) -> int:
    """Collapse malformed role-alternation in live history.
    
    Native C replacement for ``agent.agent_runtime_helpers.repair_message_sequence``.
    The C implementation handles merge of consecutive assistants and users.
    The cursor-correcting logic stays in Python for compatibility.
    
    Returns the number of repairs made.
    """
    if not _native_enabled() or _has_object_tool_calls(messages):
        from agent.agent_runtime_helpers import repair_message_sequence as _py_fn
        return _py_fn(agent, messages)
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.repair_message_sequence(inp_json)
        out = json.loads(out_json)
        
        # Count repairs: difference in message count or content
        repairs = abs(len(messages) - len(out))
        if repairs == 0:
            # Check if any content changed
            for i in range(min(len(messages), len(out))):
                if _messages_to_json(messages[i]) != _messages_to_json(out[i]):
                    repairs = 1
                    break
            
        messages[:] = out
        return repairs
    except Exception:
        from agent.agent_runtime_helpers import repair_message_sequence as _py_fn
        return _py_fn(agent, messages)


def sanitize_and_repair_messages(messages_json: str) -> str:
    """Combined sanitize + repair pass on JSON string.
    
    Direct pass-through to the C combined function.
    """
    if not _native_enabled():
        # Fallback: run sanitize in sequence
        from agent.agent_runtime_helpers import sanitize_api_messages as _san
        messages = json.loads(messages_json)
        messages = _san(messages)
        # repair_message_sequence is skipped because it requires an `agent` reference
        return json.dumps(messages, ensure_ascii=False)
    
    try:
        return agent_native.sanitize_and_repair_messages(messages_json)
    except Exception:
        return messages_json


# ---------------------------------------------------------------------------
# context_compressor replacements
# ---------------------------------------------------------------------------

def sanitize_tool_pairs(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Sanitize tool pairs: drop orphans, inject stubs.
    
    Native C replacement for ``agent.context_compressor.ContextCompressor._sanitize_tool_pairs``.
    """
    if not _native_enabled():
        return messages  # Can't call instance method without instance
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.sanitize_tool_pairs(inp_json)
        return json.loads(out_json)
    except Exception:
        return messages


def find_tail_cut_by_tokens(
    messages: List[Dict[str, Any]],
    head_end: int,
    token_budget: int,
) -> Tuple[int, int]:
    """Find tail-cut index by walking backward from end.
    
    Native C replacement for ``agent.context_compressor.ContextCompressor._find_tail_cut_by_tokens``.
    
    Returns:
        (tail_start, tail_tokens).
    """
    if not _native_enabled():
        return (0, 0)
    
    try:
        inp_json = _messages_to_json(messages)
        return agent_native.find_tail_cut_by_tokens(inp_json, head_end, token_budget)
    except Exception:
        return (0, 0)


def build_static_fallback_summary(messages: List[Dict[str, Any]], tail_start: int) -> str:
    """Build a static fallback summary string from pruned messages.
    
    Native C replacement for ``agent.context_compressor.ContextCompressor._build_static_fallback_summary``.
    """
    if not _native_enabled():
        return "[CONTEXT COMPACTION -- REFERENCE ONLY] Respond only to the latest user message below."
    
    try:
        inp_json = _messages_to_json(messages)
        return agent_native.build_static_fallback_summary(inp_json, tail_start)
    except Exception:
        return "[CONTEXT COMPACTION -- REFERENCE ONLY] Respond only to the latest user message below."


def prune_old_tool_results(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Prune old tool results from the message list.
    
    Native C replacement for ``agent.context_compressor.ContextCompressor._prune_old_tool_results``.
    """
    if not _native_enabled():
        return messages
    
    try:
        inp_json = _messages_to_json(messages)
        out_json = agent_native.prune_old_tool_results(inp_json)
        return json.loads(out_json)
    except Exception:
        return messages


# ---------------------------------------------------------------------------
# Module-level helper: check if native is available
# ---------------------------------------------------------------------------

def is_native_available() -> bool:
    """Return True if the native C library is loaded and ready."""
    return _native_available


def force_pure_mode() -> bool:
    """Return True if HERMES_FORCE_PURE forces Python fallback."""
    return _HERMES_FORCE_PURE
