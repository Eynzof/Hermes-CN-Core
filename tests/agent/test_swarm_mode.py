"""Unit tests for agent/swarm_mode.py."""

from agent.swarm_mode import SwarmMode, SwarmTrigger


class TestSwarmMode:
    """Test SwarmMode lifecycle, queries, and reminder injection."""

    def test_initial_state(self):
        mode = SwarmMode()
        assert not mode.is_active
        assert mode.trigger is None
        assert mode.trigger_name() == "inactive"

    def test_enter_manual(self):
        mode = SwarmMode()
        mode.enter(SwarmTrigger.MANUAL)
        assert mode.is_active
        assert mode.trigger == SwarmTrigger.MANUAL
        assert mode.trigger_name() == "manual"
        assert not mode.should_auto_exit
        mode.exit()
        assert not mode.is_active

    def test_enter_task(self):
        mode = SwarmMode()
        mode.enter(SwarmTrigger.TASK)
        assert mode.is_active
        assert mode.trigger == SwarmTrigger.TASK
        assert mode.should_auto_exit  # TASK auto-exits
        mode.exit()
        assert not mode.is_active

    def test_enter_tool(self):
        mode = SwarmMode()
        mode.enter(SwarmTrigger.TOOL)
        assert mode.is_active
        assert mode.trigger == SwarmTrigger.TOOL
        assert mode.should_auto_exit  # TOOL auto-exits
        mode.exit()
        assert not mode.is_active

    def test_double_enter_noop(self):
        mode = SwarmMode()
        mode.enter(SwarmTrigger.MANUAL)
        mode.enter(SwarmTrigger.TASK)  # Should be a no-op
        assert mode.trigger == SwarmTrigger.MANUAL  # Still MANUAL
        mode.exit()

    def test_exit_when_not_active(self):
        """exit() should be safe when not active."""
        mode = SwarmMode()
        mode.exit()  # Should not raise
        assert not mode.is_active

    def test_reminder_text_returned(self):
        """_inject_enter_reminder and _inject_exit_reminder return strings."""
        mode = SwarmMode()
        enter_text = mode._inject_enter_reminder()
        if enter_text is not None:
            assert "Swarm Mode" in enter_text
            assert "{{item}}" in enter_text

        exit_text = mode._inject_exit_reminder()
        if exit_text is not None:
            assert "Swarm Mode" in exit_text
