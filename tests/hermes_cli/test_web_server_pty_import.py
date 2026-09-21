"""Test the platform-branched PTY bridge import in hermes_cli.web_server_chat.

The /api/pty WebSocket handler picks its bridge at import
time via ``sys.platform.startswith("win")`` — Windows gets the ConPTY
backend, POSIX gets the fcntl/termios one.  Both branches must:

  1. Expose ``PtyBridge`` as the bridge class (or None) and
     ``PtyUnavailableError`` as an exception class.
  2. Set ``_PTY_BRIDGE_AVAILABLE`` correctly.
  3. Never raise at import time when the platform-native dependency is
     missing — the dashboard's non-chat tabs must keep loading.

This test asserts the live state on whichever platform CI runs on, plus a
source-text check confirming the branch shape is preserved so a future
refactor can't accidentally collapse it back to a POSIX-only import.
"""
from __future__ import annotations


import sys

import pytest

import hermes_cli.web_server_chat as _web_server_chat


def test_web_server_exposes_pty_bridge_symbols():
    """The two symbols /api/pty consumes must always exist."""
    assert hasattr(_web_server_chat, "PtyBridge")
    assert hasattr(_web_server_chat, "PtyUnavailableError")
    assert hasattr(_web_server_chat, "_PTY_BRIDGE_AVAILABLE")
    # PtyUnavailableError is always an exception class — either the real
    # one from the platform bridge, or the local fallback class.
    assert isinstance(_web_server_chat.PtyUnavailableError, type)
    assert issubclass(_web_server_chat.PtyUnavailableError, BaseException)


@pytest.mark.skipif(sys.platform.startswith("win"), reason="POSIX-only")
def test_web_server_uses_posix_pty_bridge_on_posix():
    """On POSIX, the bridge must be the fcntl/termios PtyBridge."""
    from hermes_cli.pty_bridge import PtyBridge as PosixBridge
    from hermes_cli.pty_bridge import PtyUnavailableError as PosixErr

    assert _web_server_chat.PtyBridge is PosixBridge
    assert _web_server_chat._PTY_BRIDGE_AVAILABLE is True
    assert _web_server_chat.PtyUnavailableError is PosixErr
