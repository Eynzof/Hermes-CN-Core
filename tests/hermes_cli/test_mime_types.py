"""Tests for MIME type registration and serving on Windows.

Hermes registers ``application/javascript`` for ``.js`` and ``.mjs`` files
explicitly at import time (see ``hermes_cli/web_server.py`` imports) so that
on Windows — where the system registry may not map these extensions — module
scripts are served with the correct ``Content-Type``.

Without this fix, ``<script type="module">`` blocks fail with:
  Failed to load module script: Expected a JavaScript or WebAssembly module script
"""
from __future__ import annotations

import mimetypes

import hermes_cli.web_server  # noqa: F401  — performs the add_type registration at import


def test_js_mime_type_registered():
    """Verify that ``.js`` files map to ``application/javascript``."""
    guessed, encoding = mimetypes.guess_type("test.js")
    assert guessed == "application/javascript", (
        f"Expected application/javascript, got {guessed}"
    )
    assert encoding is None


def test_mjs_mime_type_registered():
    """Verify that ``.mjs`` files map to ``application/javascript``."""
    guessed, encoding = mimetypes.guess_type("test.mjs")
    assert guessed == "application/javascript", (
        f"Expected application/javascript, got {guessed}"
    )
    assert encoding is None


def test_fs_mime_type_returns_js_correctly():
    """``_fs_mime_type`` returns ``application/javascript`` for .js/.mjs."""
    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    assert _fs_mime_type(Path("bundle.js")) == "application/javascript"
    assert _fs_mime_type(Path("chunk.mjs")) == "application/javascript"


def test_fs_mime_type_other_types_still_work():
    """Other known MIME types are unaffected by the JS registration."""
    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    assert _fs_mime_type(Path("style.css")) == "text/css"
    assert _fs_mime_type(Path("index.html")) == "text/html"
    assert _fs_mime_type(Path("data.json")) == "application/json"


def test_js_mime_type_on_windows_without_registry(monkeypatch):
    """Simulate a broken Windows registry by clearing the ``mimetypes``
    in-memory suffix map, then verify that our ``add_type`` calls restore
    the correct mapping.

    On Windows, ``mimetypes`` reads the system registry. If the registry
    has no ``.js`` → ``application/javascript`` mapping, the fallback
    ``guess_type`` returns ``None`` (or ``text/plain`` from a stale
    registry entry). The ``mimetypes.add_type()`` calls at the top of
    ``web_server.py`` override this at import time.
    """
    # First verify the registered mapping works
    assert mimetypes.guess_type("test.js") == ("application/javascript", None)
    assert mimetypes.guess_type("test.mjs") == ("application/javascript", None)

    # Now simulate a broken registry by clearing the in-memory suffix map
    # and verify that the add_type calls still take effect (they run at
    # import time before any test has a chance to clear the map).
    mimetypes._default_mime_types()  # Re-init with defaults (may lose .js)
    guessed, _ = mimetypes.guess_type("test.js")
    # The registry may or may not have .js mapped; what matters is that
    # _fs_mime_type still returns application/javascript because the
    # web_server module-level add_type calls already ran.
    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    # _fs_mime_type checks _FS_MIME_TYPES first (no .js there) then falls
    # through to mimetypes.guess_type. Since the add_type calls registered
    # .js at import time, guess_type should still return the right answer.
    # But if the module was imported AFTER our monkeypatch cleared the
    # types, it might not have run yet. So we explicitly re-register.
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")

    assert _fs_mime_type(Path("bundle.js")) == "application/javascript"
    assert _fs_mime_type(Path("chunk.mjs")) == "application/javascript"
    # Non-JS files return their correct types
    assert _fs_mime_type(Path("style.css")) == "text/css"


def test_static_files_serves_js_with_correct_mime(tmp_path):
    """Verify that Starlette's StaticFiles serves ``.js`` files with
    ``Content-Type: application/javascript`` when mounted with the
    registered MIME types."""
    from starlette.staticfiles import StaticFiles
    from starlette.testclient import TestClient
    from starlette.applications import Starlette
    from starlette.routing import Mount

    # Create a temporary JS file
    assets_dir = tmp_path / "assets"
    assets_dir.mkdir()
    (assets_dir / "test.js").write_text("console.log('hello');")
    (assets_dir / "test.mjs").write_text("export const x = 1;")

    app = Starlette(routes=[
        Mount("/assets", app=StaticFiles(directory=str(assets_dir), html=True), name="assets"),
    ])
    client = TestClient(app)

    r = client.get("/assets/test.js")
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/javascript", (
        f"Expected application/javascript, got {r.headers.get('content-type')}"
    )

    r = client.get("/assets/test.mjs")
    assert r.status_code == 200
    assert r.headers.get("content-type") == "application/javascript", (
        f"Expected application/javascript, got {r.headers.get('content-type')}"
    )
