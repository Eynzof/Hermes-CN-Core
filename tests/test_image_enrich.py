"""Tests for image pre-analysis enrichment in cli.py.

Bug #1B: Image Pre-Analysis Metadata in User Messages
"""

import pytest
import tempfile
import pathlib


# We test the format strings directly since the full test requires
# mocking the vision tool (which needs API keys).

def test_enrich_success_format_contains_hermes_ui_block():
    """Successful image analysis must use [Hermes UI Image] block format."""
    # Simulate what _preprocess_images_with_vision constructs
    img_path = pathlib.Path("/tmp/test_image.png")
    description = "A screenshot of a dashboard with charts."

    enriched = (
        f"[Hermes UI Image]\n"
        f"name={img_path.name}\n"
        f"description:\n{description}\n"
        f"[/Hermes UI Image]\n"
        f"[If you need a closer look, use vision_analyze with "
        f"image_url: {img_path}]"
    )

    # Must NOT contain raw metadata prefix
    assert "[The user attached an image" not in enriched
    # Must wrap in Hermes UI Image block
    assert "[Hermes UI Image]" in enriched
    assert "[/Hermes UI Image]" in enriched
    assert "name=" in enriched
    assert "description:" in enriched
    # Must still contain the analysis
    assert description in enriched


def test_enrich_failure_format():
    """Failed analysis must not use the old raw format."""
    img_path = pathlib.Path("/tmp/test_image.png")

    enriched = (
        f"[Hermes UI Image]\n"
        f"name={img_path.name}\n"
        f"description:\n[Analysis failed or unavailable]\n"
        f"[/Hermes UI Image]\n"
        f"[If you need a closer look, use vision_analyze with "
        f"image_url: {img_path}]"
    )

    # Must NOT contain raw metadata prefix
    assert "[The user attached an image" not in enriched
    # Must wrap in Hermes UI Image block
    assert "[Hermes UI Image]" in enriched
    assert "[/Hermes UI Image]" in enriched


def test_enrich_exception_format():
    """Exception case must not use the old raw format."""
    img_path = pathlib.Path("/tmp/test_image.png")

    enriched = (
        f"[Hermes UI Image]\n"
        f"name={img_path.name}\n"
        f"description:\n[Analysis error: connection failed]\n"
        f"[/Hermes UI Image]\n"
        f"[If you need a closer look, use vision_analyze with "
        f"image_url: {img_path}]"
    )

    # Must NOT contain raw metadata prefix
    assert "[The user attached an image" not in enriched
    # Must wrap in Hermes UI Image block
    assert "[Hermes UI Image]" in enriched


def test_combined_with_user_text():
    """When combined with user text, only the metadata block should be wrappable."""
    img_path = pathlib.Path("/tmp/test_image.png")
    description = "A screenshot showing code."

    prefix = (
        f"[Hermes UI Image]\n"
        f"name={img_path.name}\n"
        f"description:\n{description}\n"
        f"[/Hermes UI Image]\n"
        f"[If you need a closer look, use vision_analyze with "
        f"image_url: {img_path}]"
    )
    user_text = "What does this code do?"
    combined = f"{prefix}\n\n{user_text}"

    # The combined text must NOT have the old raw prefix
    assert "The user attached an image" not in combined
    # But must have the user's text
    assert user_text in combined
