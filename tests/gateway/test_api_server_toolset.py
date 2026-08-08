"""Tests for hermes-api-server toolset and API server tool availability."""
from unittest.mock import patch, MagicMock

from toolsets import resolve_toolset, get_toolset, validate_toolset

class TestHermesApiServerToolset:
    """Tests for the hermes-api-server toolset definition."""

    def test_toolset_exists(self):
        ts = get_toolset("hermes-api-server")
        assert ts is not None

    def test_toolset_validates(self):
        assert validate_toolset("hermes-api-server")

    def test_toolset_includes_web_tools(self):
        tools = resolve_toolset("hermes-api-server")
        assert "web_search" in tools
        assert "web_extract" in tools

    def test_toolset_includes_core_tools(self):
        tools = resolve_toolset("hermes-api-server")
        expected = [
            "terminal", "process",
            "read_file", "write_file", "patch", "search_files",
            "vision_analyze", "image_generate",
            "execute_code", "delegate_task",
            "todo", "memory", "session_search", "cronjob",
        ]
        for tool in expected:
            assert tool in tools, f"Missing expected tool: {tool}"

    def test_toolset_includes_browser_tools(self):
        tools = resolve_toolset("hermes-api-server")
        for tool in ["browser_navigate", "browser_snapshot", "browser_click",
                      "browser_type", "browser_scroll", "browser_back",
                      "browser_press"]:
            assert tool in tools, f"Missing browser tool: {tool}"

    def test_toolset_includes_homeassistant_tools(self):
        tools = resolve_toolset("hermes-api-server")
        for tool in ["ha_list_entities", "ha_get_state", "ha_list_services", "ha_call_service"]:
            assert tool in tools, f"Missing HA tool: {tool}"

    def test_toolset_excludes_clarify(self):
        tools = resolve_toolset("hermes-api-server")
        assert "clarify" not in tools

    def test_toolset_excludes_send_message(self):
        tools = resolve_toolset("hermes-api-server")
        assert "send_message" not in tools

    def test_toolset_excludes_text_to_speech(self):
        tools = resolve_toolset("hermes-api-server")
        assert "text_to_speech" not in tools

class TestApiServerPlatformConfig:
    def test_platforms_dict_includes_api_server(self):
        from hermes_cli.tools_config import PLATFORMS
        assert "api_server" in PLATFORMS
        assert PLATFORMS["api_server"]["default_toolset"] == "hermes-api-server"

    def test_default_api_server_includes_terminal_toolset(self):
        """Regression #49622: desktop-only read_terminal is registered into the
        'terminal' toolset (ships in-repo), so resolve_toolset('terminal') grows
        to include it after discovery. read_terminal is NOT in the
        hermes-api-server composite, so the old all-tools subset test dropped
        'terminal' entirely. Its static membership (terminal, process) IS in the
        composite, so it must stay enabled."""
        from tools.registry import discover_builtin_tools
        from hermes_cli.tools_config import _get_platform_tools
        discover_builtin_tools()
        assert "terminal" in _get_platform_tools({}, "api_server")

    def test_registering_tool_into_toolset_does_not_drop_toolset_from_inference(self):
        """Class invariant (covers the delegate_cli overlay case): registering a
        NEW tool into an existing configurable toolset must never remove that
        toolset from a platform whose composite lists the toolset's static
        tools. Synthetic registration keeps the test hermetic in CI."""
        from tools.registry import registry
        from hermes_cli.tools_config import _get_platform_tools

        sentinel = "test_sentinel_delegation_tool"
        registry.register(
            name=sentinel,
            toolset="delegation",
            schema={"name": sentinel, "description": "test",
                    "parameters": {"type": "object", "properties": {}}},
            handler=lambda args, **kw: "{}",
        )
        try:
            # delegation's static membership (delegate_task) is in the composite,
            # so the toolset must survive inference despite the extra registry tool.
            assert "delegation" in _get_platform_tools({}, "api_server"), (
                "registering a tool into 'delegation' dropped it from api_server"
            )
        finally:
            registry.deregister(sentinel)

    def test_default_off_and_restricted_toolsets_stay_off_on_api_server(self):
        """Negative contract: the static-membership comparison must NOT newly
        enable default-off or platform-restricted toolsets."""
        import os
        from unittest.mock import patch
        from hermes_cli.tools_config import _get_platform_tools
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("HASS_TOKEN", None)
            os.environ.pop("XAI_API_KEY", None)
            enabled = _get_platform_tools({}, "api_server")
        assert "homeassistant" not in enabled
        assert "discord" not in enabled
        assert "discord_admin" not in enabled
        assert "x_search" not in enabled

