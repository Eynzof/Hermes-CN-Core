"""Tests for agent.context_tools — CompactMode enum, mode guidance, schema factories."""

import orjson
import pytest
from agent.context_tools import (
    CompactMode,
    _MODE_GUIDANCE,
    get_guidance,
    get_context_usage_schema,
    get_compact_schema,
)


class TestCompactMode:
    """Verify the CompactMode enum values."""

    def test_enum_values(self):
        assert CompactMode.BALANCED.value == "balanced"
        assert CompactMode.AGGRESSIVE.value == "aggressive"
        assert CompactMode.RETENTIVE.value == "retentive"
        assert CompactMode.TECHNICAL.value == "technical"

    def test_enum_is_str_enum(self):
        """CompactMode inherits from str so values are directly usable in comparisons."""
        assert isinstance(CompactMode.BALANCED, str)
        assert CompactMode("balanced") is CompactMode.BALANCED
        assert CompactMode("aggressive") is CompactMode.AGGRESSIVE
        assert CompactMode("retentive") is CompactMode.RETENTIVE
        assert CompactMode("technical") is CompactMode.TECHNICAL

    def test_invalid_mode_raises_value_error(self):
        with pytest.raises(ValueError):
            CompactMode("nonexistent")
        with pytest.raises(ValueError):
            CompactMode("")

    def test_all_modes_have_guidance(self):
        """Every enum member must have a non-empty guidance string."""
        for mode in CompactMode:
            guidance = _MODE_GUIDANCE.get(mode, "")
            assert guidance, f"Mode {mode!r} is missing guidance text"
            assert len(guidance) > 20, f"Guidance for {mode!r} is too short"


class TestGetGuidance:
    """Verify the get_guidance helper."""

    def test_returns_guidance_for_valid_mode(self):
        guidance = get_guidance("balanced")
        assert "Be balanced" in guidance
        assert len(guidance) > 30

    def test_returns_guidance_for_technical(self):
        guidance = get_guidance("technical")
        assert "technical" in guidance.lower() or "code" in guidance.lower()

    def test_returns_empty_string_for_invalid_mode(self):
        assert get_guidance("invalid_mode") == ""
        assert get_guidance("") == ""
        assert get_guidance("unknown") == ""

    def test_returns_empty_string_for_none(self):
        """None should be handled gracefully (TypeError caught)."""
        assert get_guidance(None) == ""


class TestGetContextUsageSchema:
    """Verify the context_usage tool schema."""

    def test_schema_has_correct_name(self):
        schema = get_context_usage_schema()
        assert schema["name"] == "context_usage"

    def test_schema_description_is_non_empty(self):
        schema = get_context_usage_schema()
        assert len(schema["description"]) > 20

    def test_schema_has_no_required_params(self):
        schema = get_context_usage_schema()
        assert schema["parameters"]["required"] == []
        assert schema["parameters"]["properties"] == {}

    def test_schema_is_valid_json_serializable(self):
        schema = get_context_usage_schema()
        dumped = orjson.dumps(schema).decode('utf-8')
        loaded = orjson.loads(dumped)
        assert loaded["name"] == "context_usage"

    def test_schema_type_is_object(self):
        schema = get_context_usage_schema()
        assert schema["parameters"]["type"] == "object"


class TestGetCompactSchema:
    """Verify the compact tool schema."""

    def test_schema_has_correct_name(self):
        schema = get_compact_schema()
        assert schema["name"] == "compact"

    def test_schema_description_is_non_empty(self):
        schema = get_compact_schema()
        assert len(schema["description"]) > 20

    def test_schema_has_instruction_param(self):
        schema = get_compact_schema()
        props = schema["parameters"]["properties"]
        assert "instruction" in props
        assert props["instruction"]["type"] == "string"

    def test_schema_has_mode_param_with_all_four_values(self):
        schema = get_compact_schema()
        props = schema["parameters"]["properties"]
        assert "mode" in props
        assert props["mode"]["type"] == "string"
        assert props["mode"]["enum"] == ["balanced", "aggressive", "retentive", "technical"]

    def test_schema_no_required_params(self):
        """Both instruction and mode are optional."""
        schema = get_compact_schema()
        assert schema["parameters"]["required"] == []

    def test_schema_is_valid_json_serializable(self):
        schema = get_compact_schema()
        dumped = orjson.dumps(schema).decode('utf-8')
        loaded = orjson.loads(dumped)
        assert loaded["name"] == "compact"

    def test_schema_type_is_object(self):
        schema = get_compact_schema()
        assert schema["parameters"]["type"] == "object"
