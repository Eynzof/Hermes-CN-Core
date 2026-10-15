"""Toolset metadata is complete before import and respects scoped overrides."""
import sys

from hermes_constants import reset_hermes_home_override, set_hermes_home_override
from tools.registry import ToolRegistry


def test_cold_toolset_membership_preserves_profile_overrides(tmp_path):
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    (tools_dir / "unimported_probe.py").write_text(
        'from tools.registry import registry\n'
        'registry.register(name="probe", toolset="cold", schema={}, handler=str)\n',
        encoding="utf-8",
    )
    registry = ToolRegistry()
    registry.enable_lazy_builtins(tools_dir)
    assert registry.get_tool_names_for_toolset("cold") == ["probe"]
    assert "tools.unimported_probe" not in sys.modules

    profile = tmp_path / "other-profile"
    registry.register(name="probe", toolset="override", schema={}, handler=str, scope=str(profile))
    assert registry.get_tool_names_for_toolset("cold") == ["probe"]
    token = set_hermes_home_override(profile)
    try:
        assert registry.get_tool_names_for_toolset("cold") == []
        assert registry.get_tool_names_for_toolset("override") == ["probe"]
    finally:
        reset_hermes_home_override(token)
    assert registry.get_tool_names_for_toolset("cold") == ["probe"]
    assert "tools.unimported_probe" not in sys.modules
