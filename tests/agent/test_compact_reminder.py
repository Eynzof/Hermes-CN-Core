"""Tests for agent/compact_reminder.py — CompactReminderProvider logic."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Dict
from unittest.mock import MagicMock

import pytest

from agent.compact_reminder import CompactReminderProvider
from agent.system_reminder import SystemReminder


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def provider():
    return CompactReminderProvider(threshold=0.70, cooldown_steps=5)


def make_fake_agent(context_usage_percent: float) -> SimpleNamespace:
    """Create a fake agent with a mock context_compressor."""
    used = int(200000 * context_usage_percent / 100)
    cc = MagicMock()
    cc.get_usage_status.return_value = {
        "usage_percent": context_usage_percent,  # 0-100
        "used_tokens": used,
        "max_context_tokens": 200000,
        "threshold_tokens": 150000,
        "compression_count": 0,
    }
    return SimpleNamespace(context_compressor=cc)


# ── Threshold logic ───────────────────────────────────────────────────


class TestThreshold:
    def test_below_threshold_returns_empty(self, provider):
        agent = make_fake_agent(65.0)
        assert provider.get_reminders(agent, 1) == []

    def test_at_threshold_injects(self, provider):
        agent = make_fake_agent(70.0)
        result = provider.get_reminders(agent, 1)
        assert len(result) == 1
        assert result[0].type == "compact_reminder"

    def test_above_threshold_injects(self, provider):
        agent = make_fake_agent(85.0)
        result = provider.get_reminders(agent, 1)
        assert len(result) == 1
        assert result[0].type == "compact_reminder"

    def test_no_compressor_returns_empty(self, provider):
        agent = SimpleNamespace(context_compressor=None)
        assert provider.get_reminders(agent, 1) == []


# ── Reminder content format ───────────────────────────────────────────


class TestReminderContent:
    def test_contains_usage_percentage(self, provider):
        agent = make_fake_agent(75.0)
        result = provider.get_reminders(agent, 1)
        assert "75%" in result[0].content

    def test_contains_token_numbers(self, provider):
        agent = make_fake_agent(75.0)
        result = provider.get_reminders(agent, 1)
        assert "150000" in result[0].content
        assert "200000" in result[0].content

    def test_contains_compact_tool_reference(self, provider):
        agent = make_fake_agent(85.0)
        result = provider.get_reminders(agent, 1)
        assert "Compact" in result[0].content

    def test_type_is_correct(self, provider):
        agent = make_fake_agent(85.0)
        result = provider.get_reminders(agent, 1)
        assert result[0].type == "compact_reminder"


# ── Throttling — cooldown steps ───────────────────────────────────────


class TestCooldown:
    def test_first_injection_no_cooldown(self, provider):
        agent = make_fake_agent(80.0)
        result = provider.get_reminders(agent, 1)
        assert len(result) == 1

    def test_injection_within_cooldown_skipped(self, provider):
        agent = make_fake_agent(80.0)
        # Inject at step 1
        provider.get_reminders(agent, 1)
        # Call again at step 3 (steps_since=2 < 5)
        result = provider.get_reminders(agent, 3)
        assert result == []

    def test_injection_after_cooldown_allowed(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(90.0)
        # Step 10, steps_since=9 >= 5, growth=10% >= 5%
        result = provider.get_reminders(agent2, 10)
        assert len(result) == 1

    def test_cooldown_exact_boundary_below(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        # Step 6, steps_since=5 <= 5 → still in cooldown
        result = provider.get_reminders(agent, 6)
        assert result == []

    def test_cooldown_exact_boundary_above(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        # Step 7, steps_since=6 > 5 → cooldown elapsed, but growth check may block
        agent2 = make_fake_agent(90.0)
        result = provider.get_reminders(agent2, 7)
        # growth=10% >= 5%, cooldown elapsed → injects
        assert len(result) == 1


# ── Throttling — usage growth guard ───────────────────────────────────


class TestUsageGrowth:
    def test_no_growth_skips(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        # Same usage at step 10
        result = provider.get_reminders(agent, 10)
        assert result == []

    def test_small_growth_skips(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(83.0)
        # growth=3% < 5%
        result = provider.get_reminders(agent2, 10)
        assert result == []

    def test_exact_growth_boundary_below(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(84.99)
        # growth=4.99% < 5%
        result = provider.get_reminders(agent2, 10)
        assert result == []

    def test_sufficient_growth_allowed(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(86.0)
        # growth=6% >= 5%, cooldown elapsed
        result = provider.get_reminders(agent2, 10)
        assert len(result) == 1

    def test_growth_exact_boundary(self, provider):
        # 85.1% → context_usage=0.851, growth=0.051 >= 0.05 (avoids floating
        # point where 0.85 - 0.80 = 0.049999... < 0.05)
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(85.1)
        # growth=5.1% >= 5%
        result = provider.get_reminders(agent2, 10)
        assert len(result) == 1


# ── Throttling — combined ─────────────────────────────────────────────


class TestCombined:
    def test_cooldown_blocks_even_with_enough_growth(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(90.0)
        # step 3: growth=10% >= 5% but steps_since=2 <= 5
        result = provider.get_reminders(agent2, 3)
        assert result == []

    def test_growth_blocks_even_after_cooldown(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(81.0)
        # step 10: steps_since=9 >= 5 but growth=1% < 5%
        result = provider.get_reminders(agent2, 10)
        assert result == []

    def test_both_conditions_must_pass(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 1)
        agent2 = make_fake_agent(86.0)
        # step 10: steps_since=9 >= 5 AND growth=6% >= 5%
        result = provider.get_reminders(agent2, 10)
        assert len(result) == 1


# ── on_context_compacted ──────────────────────────────────────────────


class TestOnContextCompacted:
    def test_resets_state(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 5)
        assert provider._last_injected_step is not None
        provider.on_context_compacted(agent)
        assert provider._last_injected_step is None
        assert provider._last_injected_usage == 0.0

    def test_allows_fresh_injection_after_reset(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 5)
        provider.on_context_compacted(agent)
        # After reset, even same usage should inject
        result = provider.get_reminders(agent, 6)
        assert len(result) == 1

    def test_multi_call_reset(self, provider):
        agent = make_fake_agent(80.0)
        provider.get_reminders(agent, 5)
        agent2 = make_fake_agent(90.0)
        provider.get_reminders(agent2, 15)
        provider.on_context_compacted(agent)
        assert provider._last_injected_step is None

    def test_accepts_any_agent(self, provider):
        provider.on_context_compacted(None)  # should not raise


# ── Edge cases ────────────────────────────────────────────────────────


class TestEdgeCases:
    def test_zero_context_length(self, provider):
        cc = MagicMock()
        cc.get_usage_status.return_value = {
            "usage_percent": 0.0,
            "used_tokens": 0,
            "max_context_tokens": 0,
            "threshold_tokens": 0,
            "compression_count": 0,
        }
        agent = SimpleNamespace(context_compressor=cc)
        assert provider.get_reminders(agent, 1) == []

    def test_usage_above_100_percent(self, provider):
        """125% should still inject."""
        agent = make_fake_agent(125.0)
        result = provider.get_reminders(agent, 1)
        assert len(result) == 1
        assert "125%" in result[0].content

    def test_negative_usage(self, provider):
        """Negative usage_percent (<0) should be below threshold."""
        cc = MagicMock()
        cc.get_usage_status.return_value = {
            "usage_percent": -1.0,
            "used_tokens": 0,
            "max_context_tokens": 200000,
            "threshold_tokens": 150000,
            "compression_count": 0,
        }
        agent = SimpleNamespace(context_compressor=cc)
        assert provider.get_reminders(agent, 1) == []

    def test_zero_tokens(self, provider):
        """0/200000 = 0% < 70% → no injection."""
        cc = MagicMock()
        cc.get_usage_status.return_value = {
            "usage_percent": 0.0,
            "used_tokens": 0,
            "max_context_tokens": 200000,
            "threshold_tokens": 150000,
            "compression_count": 0,
        }
        agent = SimpleNamespace(context_compressor=cc)
        assert provider.get_reminders(agent, 1) == []

    def test_very_large_numbers(self, provider):
        """50% < 70% → no injection."""
        agent = make_fake_agent(50.0)
        assert provider.get_reminders(agent, 1) == []


# ── Constructor defaults ──────────────────────────────────────────────


class TestConstructorDefaults:
    def test_default_threshold(self):
        p = CompactReminderProvider()
        assert p._threshold == 0.70

    def test_default_cooldown_steps(self):
        p = CompactReminderProvider()
        assert p._cooldown_steps == 5

    def test_custom_constructor_values(self):
        p = CompactReminderProvider(threshold=0.80, cooldown_steps=3)
        assert p._threshold == 0.80
        assert p._cooldown_steps == 3

    def test_initial_throttle_state(self):
        p = CompactReminderProvider()
        assert p._last_injected_step is None
        assert p._last_injected_usage == 0.0
