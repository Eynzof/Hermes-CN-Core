"""Tests for tui_gateway/entry.py sys.path hardening (issues #15989, #51286).

When the TUI backend is spawned by Node.js, the launch directory may shadow
Hermes's own top-level modules (``utils``, ``proxy``, ``ui``).  entry.py must
neutralize this before any non-stdlib import is resolved, by delegating to the
shared ``hermes_bootstrap.harden_import_path`` guard.

These tests assert the entry point wires up the real guard (rather than
re-implementing it inline) and that the guard's behavior covers both the
relative-cwd form and the absolute-cwd-path form that was the actual #51286
failure.
"""

import hermes_bootstrap


def test_guard_handles_absolute_cwd_path():
    """The #51286 case: the launch dir is on sys.path as its own absolute
    path, ahead of the Hermes root.  harden_import_path must relocate the
    Hermes root to the front so ``from utils import ...`` resolves to Hermes."""
    import sys

    original = sys.path[:]
    try:
        sys.path[:] = ["/home/user/tg-ws-proxy", "/opt/hermes", "/usr/lib"]
        hermes_bootstrap.harden_import_path(src_root="/opt/hermes")
        assert sys.path[0] == "/opt/hermes"
        assert sys.path.index("/opt/hermes") < sys.path.index(
            "/home/user/tg-ws-proxy"
        )
    finally:
        sys.path[:] = original
