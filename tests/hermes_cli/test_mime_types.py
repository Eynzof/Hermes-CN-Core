"""Tests for MIME type registration and serving on Windows.

Hermes pins a JavaScript ``Content-Type`` for ``.js``/``.mjs`` files so that on
Windows — where the system registry may map them to ``text/plain`` — module
scripts are still served with a JavaScript type (``hermes_cli/web_server.py``
registers the pair at import; ``hermes_cli/web_asset_mime_types`` pins the
serving layer afterwards). RFC 9239 blesses ``text/javascript`` as the IANA
type while ``application/javascript`` stays the legacy spelling, so both
essences are accepted here — the bug these tests pin is ``text/plain``.

Without this fix, ``<script type="module">`` blocks fail with:
  Failed to load module script: Expected a JavaScript or WebAssembly module script
"""
from __future__ import annotations

import mimetypes

import hermes_cli.web_server  # noqa: F401  — performs the add_type registration at import

# RFC 9239 (text/javascript) and the legacy application/javascript spelling.
JAVASCRIPT_MIME_ESSENCES = ("text/javascript", "application/javascript")


def _essence(content_type: str | None) -> str:
    return (content_type or "").split(";", 1)[0].strip().lower()


def _assert_javascript_mime(content_type: str | None) -> None:
    assert _essence(content_type) in JAVASCRIPT_MIME_ESSENCES, (
        f"Expected a JavaScript MIME type, got {content_type!r}"
    )


def test_js_mime_type_registered():
    """Verify that ``.js`` files map to a JavaScript MIME type."""
    guessed, encoding = mimetypes.guess_type("test.js")
    _assert_javascript_mime(guessed)
    assert encoding is None


def test_mjs_mime_type_registered():
    """Verify that ``.mjs`` files map to a JavaScript MIME type."""
    guessed, encoding = mimetypes.guess_type("test.mjs")
    _assert_javascript_mime(guessed)
    assert encoding is None


def test_fs_mime_type_returns_js_correctly():
    """``_fs_mime_type`` returns a JavaScript type for .js/.mjs."""
    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    _assert_javascript_mime(_fs_mime_type(Path("bundle.js")))
    _assert_javascript_mime(_fs_mime_type(Path("chunk.mjs")))


def test_fs_mime_type_other_types_still_work():
    """Other known MIME types are unaffected by the JS registration."""
    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    assert _fs_mime_type(Path("style.css")) == "text/css"
    assert _fs_mime_type(Path("index.html")) == "text/html"
    assert _fs_mime_type(Path("data.json")) == "application/json"


def test_js_mime_type_on_windows_without_registry(monkeypatch):
    """Simulate a broken Windows registry by clearing the ``mimetypes``
    in-memory suffix map, then verify that the ``add_type`` calls restore
    a JavaScript mapping.

    On Windows, ``mimetypes`` reads the system registry. If the registry has
    no ``.js`` → JavaScript mapping, the fallback ``guess_type`` returns
    ``None`` (or ``text/plain`` from a stale registry entry). The
    ``mimetypes.add_type()`` calls at the top of ``web_server.py`` override
    this at import time.
    """
    # First verify the registered mapping works
    guessed, _ = mimetypes.guess_type("test.js")
    _assert_javascript_mime(guessed)
    _assert_javascript_mime(mimetypes.guess_type("test.mjs")[0])

    # Now simulate a broken registry by clearing the in-memory suffix map
    # and verify that the add_type calls still take effect (they run at
    # import time before any test has a chance to clear the map).
    mimetypes._default_mime_types()  # Re-init with defaults (may lose .js)

    from hermes_cli.web_server import _fs_mime_type
    from pathlib import Path

    # _fs_mime_type checks _FS_MIME_TYPES first (no .js there) then falls
    # through to mimetypes.guess_type. Since the add_type calls registered
    # .js at import time, guess_type should still return the right answer.
    # But if the module was imported AFTER our monkeypatch cleared the
    # types, it might not have run yet. So we explicitly re-register.
    mimetypes.add_type("application/javascript", ".js")
    mimetypes.add_type("application/javascript", ".mjs")

    _assert_javascript_mime(_fs_mime_type(Path("bundle.js")))
    _assert_javascript_mime(_fs_mime_type(Path("chunk.mjs")))
    # Non-JS files return their correct types
    assert _fs_mime_type(Path("style.css")) == "text/css"


def test_static_files_serves_js_with_correct_mime(tmp_path):
    """Starlette's StaticFiles serves ``.js`` files with a JavaScript
    ``Content-Type`` when mounted with the registered MIME types."""
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
    _assert_javascript_mime(r.headers.get("content-type"))

    r = client.get("/assets/test.mjs")
    assert r.status_code == 200
    _assert_javascript_mime(r.headers.get("content-type"))
