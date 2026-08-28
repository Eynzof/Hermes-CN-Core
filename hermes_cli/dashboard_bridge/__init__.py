"""Typed, local-only bridge from DLR to the Dashboard agent runtime."""

from .backend import DashboardConversationBackend, bridge_agent_factory
from .listener import DashboardBridgeListener, pipe_name_for_current_user
from .runtime import ConversationRuntime

__all__ = [
    "ConversationRuntime",
    "DashboardBridgeListener",
    "DashboardConversationBackend",
    "bridge_agent_factory",
    "pipe_name_for_current_user",
]
