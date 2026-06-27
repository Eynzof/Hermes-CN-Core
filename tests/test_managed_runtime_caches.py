"""Tests for hermes_constants.configure_managed_runtime_caches (P-026).

The desktop-managed runtime redirects third-party framework / tooling caches
under ``<HERMES_HOME>/cache`` so they follow HERMES_HOME onto the chosen install
drive instead of bloating C: on Windows. These tests pin the gating, the
``setdefault`` semantics, and the temp-dir handling.
"""

import os
from pathlib import Path

import pytest

from hermes_constants import (
    _MANAGED_CACHE_ENV_DIRS,
    _MANAGED_TMP_ENV_VARS,
    configure_managed_runtime_caches,
    get_hermes_home,
)

_ALL_VARS = tuple(_MANAGED_CACHE_ENV_DIRS) + _MANAGED_TMP_ENV_VARS


@pytest.fixture(autouse=True)
def _clear_cache_env(monkeypatch):
    """Start every test with all managed cache/temp vars unset."""
    for var in _ALL_VARS:
        monkeypatch.delenv(var, raising=False)
    yield


def test_noop_when_not_desktop_managed(monkeypatch, tmp_path):
    monkeypatch.delenv("HERMES_DESKTOP_MANAGED", raising=False)
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    configure_managed_runtime_caches()

    for var in _ALL_VARS:
        assert var not in os.environ, f"{var} must be untouched when not managed"


def test_sets_cache_vars_under_hermes_home_when_managed(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    configure_managed_runtime_caches()

    # Compare against the actually-resolved home so the test is robust to the
    # autouse hermetic fixture (which may also pin HERMES_HOME).
    cache = get_hermes_home() / "cache"
    for var, subdir in _MANAGED_CACHE_ENV_DIRS.items():
        assert Path(os.environ[var]) == cache.joinpath(*subdir.split("/"))

    tmp_dir = cache / "tmp"
    assert tmp_dir.is_dir(), "managed temp dir should be created"
    for var in _MANAGED_TMP_ENV_VARS:
        assert Path(os.environ[var]) == tmp_dir


def test_setdefault_never_overrides_explicit_value(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("HF_HOME", "/custom/hf")

    configure_managed_runtime_caches()

    assert os.environ["HF_HOME"] == "/custom/hf"


def test_temp_left_alone_when_already_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("HERMES_DESKTOP_MANAGED", "1")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setenv("TEMP", "/preset/temp")

    configure_managed_runtime_caches()

    # An already-configured temp dir is respected and the trio stays consistent
    # (we don't half-set TMPDIR/TMP around a user's TEMP).
    assert os.environ["TEMP"] == "/preset/temp"
    assert "TMPDIR" not in os.environ
    assert "TMP" not in os.environ
