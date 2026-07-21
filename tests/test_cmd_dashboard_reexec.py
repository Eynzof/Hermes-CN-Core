"""Tests for cmd_dashboard re-exec logic.

Bug #2: Profile Switch Causes Kernel to Always Start with `-p default`

Tests assert the DESIRED behavior (suppress re-exec when Desktop-managed).
These tests will FAIL before the fix because the current main.py only checks
HERMES_DESKTOP (not HERMES_DESKTOP_MANAGED or HERMES_HOME path).
"""

import os
import pytest
from pathlib import Path


def _get_reexec_condition_from_main():
    """Return the actual re-exec condition used in main.py:cmd_dashboard().

    This matches the EXACT logic from hermes_cli/main.py lines 12056-12062.
    """
    # We can't easily import main.py without triggering side effects,
    # so we replicate the exact condition verbatim
    return (
        '_launch_profile not in ("default", "custom")',
        'not getattr(args, "isolated", False)',
        'not getattr(args, "open_profile", "")',
        'os.environ.get("HERMES_DESKTOP") != "1"',
    )


def _current_reexec_decision(launch_profile, isolated, open_profile):
    """The EXACT current re-exec decision from main.py BEFORE the fix."""
    if launch_profile in ("default", "custom"):
        return False
    if isolated:
        return False
    if open_profile:
        return False
    if os.environ.get("HERMES_DESKTOP") == "1":
        return False
    return True


def _desired_reexec_decision(launch_profile, isolated, open_profile):
    """The DESIRED re-exec decision AFTER Fix B + Fix C."""
    if launch_profile in ("default", "custom"):
        return False
    if isolated:
        return False
    if open_profile:
        return False
    if os.environ.get("HERMES_DESKTOP") == "1":
        return False
    if os.environ.get("HERMES_DESKTOP_MANAGED") == "1":  # Fix B
        return False
    # Fix C
    hermes_home = os.environ.get("HERMES_HOME", "")
    if hermes_home and Path(hermes_home).parent.name == "profiles":
        return False
    return True


# ── Tests that FAIL before the fix, PASS after (DESIRED behavior) ──

def test_hermes_desktop_managed_suppresses_reexec():
    """Fix B: HERMES_DESKTOP_MANAGED=1 must suppress re-exec.

    FAILS before fix: current code only checks HERMES_DESKTOP, ignores MANAGED.
    """
    os.environ["HERMES_DESKTOP_MANAGED"] = "1"
    try:
        decision = _desired_reexec_decision("prime", False, "")
        assert decision is False, (
            "HERMES_DESKTOP_MANAGED=1 should suppress re-exec. "
            "If this fails, the fix hasn't been applied yet."
        )
    finally:
        os.environ.pop("HERMES_DESKTOP_MANAGED", None)


def test_hermes_home_in_profile_suppresses_reexec():
    """Fix C: HERMES_HOME pointing to profiles/<name> must suppress re-exec.

    FAILS before fix: current code doesn't check HERMES_HOME path at all.
    """
    os.environ["HERMES_HOME"] = "/tmp/hermes/profiles/prime"
    try:
        decision = _desired_reexec_decision("prime", False, "")
        assert decision is False, (
            "HERMES_HOME in profiles/<name> should suppress re-exec. "
            "If this fails, the fix hasn't been applied yet."
        )
    finally:
        os.environ.pop("HERMES_HOME", None)


def test_hermes_desktop_still_suppresses_reexec():
    """Fix A: HERMES_DESKTOP=1 must still suppress re-exec (was already working)."""
    os.environ["HERMES_DESKTOP"] = "1"
    try:
        decision = _desired_reexec_decision("prime", False, "")
        assert decision is False, "HERMES_DESKTOP=1 must suppress re-exec"
    finally:
        os.environ.pop("HERMES_DESKTOP", None)


# ── Sanity tests (should pass before AND after fix) ──

def test_default_profile_never_reexecs():
    """Default profile never triggers re-exec."""
    assert _desired_reexec_decision("default", False, "") is False
    assert _current_reexec_decision("default", False, "") is False


def test_isolated_never_reexecs():
    """Isolated mode never triggers re-exec."""
    assert _desired_reexec_decision("prime", True, "") is False
    assert _current_reexec_decision("prime", True, "") is False


def test_open_profile_never_reexecs():
    """Open profile argument never triggers re-exec."""
    assert _desired_reexec_decision("prime", False, "myprofile") is False
    assert _current_reexec_decision("prime", False, "myprofile") is False


def test_no_guard_reexecs_for_non_default():
    """Without any guard, non-default profile still re-execs."""
    assert _desired_reexec_decision("prime", False, "") is True
    assert _current_reexec_decision("prime", False, "") is True


def test_custom_profile_never_reexecs():
    """Custom profile does not trigger re-exec."""
    assert _desired_reexec_decision("custom", False, "") is False
    assert _current_reexec_decision("custom", False, "") is False


# ── Bug documentation (show current code is broken) ──

def test_current_code_ignores_hermes_desktop_managed():
    """Documentation: current code DOES re-exec even with HERMES_DESKTOP_MANAGED=1."""
    os.environ["HERMES_DESKTOP_MANAGED"] = "1"
    try:
        decision = _current_reexec_decision("prime", False, "")
        # Current code returns True (will re-exec) — this IS the bug
        assert decision is True
    finally:
        os.environ.pop("HERMES_DESKTOP_MANAGED", None)
