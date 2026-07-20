"""Tests for _normalize_message_content filter in web_server.py.

Bug #1A: Model-Switch Marker Leaked as User Message
"""

import pytest
import sys
import os

# Add the repo root to path so we can import web_server
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from hermes_cli.web_server import _normalize_message_content


def test_drops_model_switch_marker():
    """User messages that are ONLY a model-switch marker must be dropped."""
    marker = "[System: The active model for this chat has changed to gpt-5 via provider openai. From this point forward, use this runtime metadata when answering questions about what model/provider is active.]"
    assert _normalize_message_content(marker) is None


def test_drops_gateway_model_note():
    """User messages that are ONLY a gateway model-switch note must be dropped."""
    note = "[Note: model was just switched from gpt-4 to gpt-5 via openai. Adjust your self-identification accordingly.]"
    assert _normalize_message_content(note) is None


def test_passes_normal_user_message():
    """Normal user messages must pass through unchanged."""
    msg = "Hello, what model are you using?"
    assert _normalize_message_content(msg) == msg


def test_passes_none_content():
    """None content must return None without crashing."""
    assert _normalize_message_content(None) is None


def test_passes_empty_string():
    """Empty string content must return empty string."""
    assert _normalize_message_content("") is ""


def test_drops_model_switch_with_whitespace():
    """Model-switch marker with leading/trailing whitespace must be dropped."""
    marker = "  [System: The active model for this chat has changed to claude-4 via provider anthropic. From this point forward, use this runtime metadata when answering questions about what model/provider is active.]  "
    assert _normalize_message_content(marker) is None


def test_drops_gateway_note_with_whitespace():
    """Gateway model-switch note with leading/trailing whitespace must be dropped."""
    note = "\n[Note: model was just switched from gpt-4 to gpt-5 via openai. Adjust your self-identification accordingly.]\n"
    assert _normalize_message_content(note) is None


def test_passes_user_message_containing_bracket_text():
    """User message that happens to contain brackets but is NOT a marker must pass."""
    msg = "I read about [System] in the docs, can you explain?"
    assert _normalize_message_content(msg) == msg


def test_drops_image_metadata_prefix():
    """Image pre-analysis metadata prefix must be stripped (returns None for metadata-only)."""
    marker = "[The user attached an image. Here's what it contains:\nA screenshot of a dashboard with charts.]\n[If you need a closer look, use vision_analyze with image_url: /tmp/img.png]"
    assert _normalize_message_content(marker) is None


def test_drops_image_metadata_short():
    """Short image fallback metadata must be dropped."""
    marker = "[The user attached an image.]\n[You can examine it with vision_analyze using image_url: /tmp/img.png]"
    assert _normalize_message_content(marker) is None


def test_drops_image_failed_analysis():
    """Failed image analysis metadata must be dropped."""
    marker = "[The user attached an image but analysis failed.]\n[You can examine it with vision_analyze using image_url: /tmp/img.png]"
    assert _normalize_message_content(marker) is None
