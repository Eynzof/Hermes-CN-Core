"""CompactMode enum, mode guidance, and tool schema factories for context engine tools.

This module keeps schema definitions and mode guidance logic out of the
2972-line ContextCompressor and provides a single import point for both
the context_usage and compact tools.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Dict


class CompactMode(str, Enum):
    """High-level compaction style presets for the ``compact`` tool.

    Each mode controls how aggressively the summarizer condenses context
    and what kinds of information to prioritise.
    """

    BALANCED = "balanced"
    AGGRESSIVE = "aggressive"
    RETENTIVE = "retentive"
    TECHNICAL = "technical"


_MODE_GUIDANCE: Dict[CompactMode, str] = {
    CompactMode.BALANCED: (
        "**Compaction Style Guidance:** Be balanced. Preserve essential context "
        "while condensing redundant information. Keep current task state, errors "
        "and solutions, code state, design decisions, and TODO items."
    ),
    CompactMode.AGGRESSIVE: (
        "**Compaction Style Guidance:** Be aggressive. Prioritise brevity, drop "
        "intermediate attempts, exploratory dead-ends, and low-priority details. "
        "Keep only essential facts, decisions, and current state."
    ),
    CompactMode.RETENTIVE: (
        "**Compaction Style Guidance:** Be retentive. Preserve more verbatim detail, "
        "especially recent reasoning steps, exact values, file paths, and user "
        "preferences. Do not over-compress."
    ),
    CompactMode.TECHNICAL: (
        "**Compaction Style Guidance:** Focus on technical specifics. Prioritise "
        "code snippets, file paths, error messages, stack traces, architectural "
        "decisions, and current implementation state. Summarise conversational filler."
    ),
}


def get_guidance(mode: str) -> str:
    """Return the guidance text for a mode string, or empty string for unknown modes."""
    try:
        return _MODE_GUIDANCE.get(CompactMode(mode), "")
    except (ValueError, TypeError):
        return ""


def get_context_usage_schema() -> Dict[str, Any]:
    """Return the JSON tool schema for the ``context_usage`` tool."""
    return {
        "name": "context_usage",
        "description": (
            "Report current conversation context usage: percentage, used tokens, "
            "and maximum context size. Use this to decide whether to call Compact."
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


def get_compact_schema() -> Dict[str, Any]:
    """Return the JSON tool schema for the ``compact`` tool."""
    return {
        "name": "compact",
        "description": (
            "Compact/summarise the conversation context to reduce token usage. "
            "Optionally pass an instruction and a compaction mode (balanced, "
            "aggressive, retentive, technical) to control the summary style."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "instruction": {
                    "type": "string",
                    "description": (
                        "Optional instruction guiding what to preserve during "
                        "compaction."
                    ),
                },
                "mode": {
                    "type": "string",
                    "enum": ["balanced", "aggressive", "retentive", "technical"],
                    "description": (
                        "High-level compaction style presets. Default: balanced."
                    ),
                },
            },
            "required": [],
        },
    }
