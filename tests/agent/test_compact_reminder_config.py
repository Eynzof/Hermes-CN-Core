"""Tests for compact_reminder config loading from config.yaml."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
import yaml

from hermes_cli.config import DEFAULT_CONFIG, load_config, get_config_path
from hermes_constants import get_hermes_home


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def temp_config_dir():
    """Create a temporary directory with a config.yaml."""
    with tempfile.TemporaryDirectory() as tmpdir:
        old_home = os.environ.get("HERMES_HOME")
        os.environ["HERMES_HOME"] = tmpdir
        yield Path(tmpdir)
        if old_home is not None:
            os.environ["HERMES_HOME"] = old_home
        else:
            os.environ.pop("HERMES_HOME", None)


def _write_config(config_dir: Path, overrides: dict):
    """Write config.yaml to config_dir with the given overrides merged into defaults."""
    import copy
    config = copy.deepcopy(DEFAULT_CONFIG)
    _deep_merge(config, overrides)
    config_path = config_dir / "config.yaml"
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False)
    return config_path


def _deep_merge(base: dict, overrides: dict):
    """Recursively merge overrides into base."""
    for key, value in overrides.items():
        if key in base and isinstance(base[key], dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


# ── Tests ─────────────────────────────────────────────────────────────


class TestCompactReminderConfig:
    def test_default_values_in_default_config(self):
        """DEFAULT_CONFIG includes compact_reminder section with expected defaults."""
        cr = DEFAULT_CONFIG.get("compact_reminder", {})
        assert cr.get("enabled") is True
        assert cr.get("threshold") == 0.70
        assert cr.get("cooldown_steps") == 5

    def test_config_loaded_from_yaml(self, temp_config_dir):
        """YAML compact_reminder.enabled: false should produce enabled=False."""
        _write_config(temp_config_dir, {"compact_reminder": {"enabled": False}})
        config = load_config()
        cr = config.get("compact_reminder", {})
        assert cr.get("enabled") is False

    def test_config_threshold_read(self, temp_config_dir):
        """YAML compact_reminder.threshold: 0.50 should be read correctly."""
        _write_config(temp_config_dir, {"compact_reminder": {"threshold": 0.50}})
        config = load_config()
        cr = config.get("compact_reminder", {})
        assert cr.get("threshold") == 0.50

    def test_config_cooldown_steps_read(self, temp_config_dir):
        """YAML compact_reminder.cooldown_steps: 3 should be read correctly."""
        _write_config(temp_config_dir, {"compact_reminder": {"cooldown_steps": 3}})
        config = load_config()
        cr = config.get("compact_reminder", {})
        assert cr.get("cooldown_steps") == 3

    def test_missing_config_uses_defaults(self, temp_config_dir):
        """No compact_reminder section should yield default values."""
        _write_config(temp_config_dir, {})
        config = load_config()
        cr = config.get("compact_reminder", {})
        assert cr.get("enabled") is True, f"Expected True, got {cr}"
        assert cr.get("threshold") == 0.70, f"Expected 0.70, got {cr}"
        assert cr.get("cooldown_steps") == 5, f"Expected 5, got {cr}"

    def test_partial_config_leaves_others_default(self, temp_config_dir):
        """Only compact_reminder.enabled: false, others should stay at defaults."""
        _write_config(temp_config_dir, {"compact_reminder": {"enabled": False}})
        config = load_config()
        cr = config.get("compact_reminder", {})
        assert cr.get("enabled") is False
        assert cr.get("threshold") == 0.70, f"Expected 0.70, got {cr}"
        assert cr.get("cooldown_steps") == 5, f"Expected 5, got {cr}"
