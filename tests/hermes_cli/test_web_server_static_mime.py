"""[CN-fork] P-041 regression: dashboard static assets must survive a polluted
Windows MIME registry.

On Windows, ``mimetypes`` seeds its process-global map from HKEY_CLASSES_ROOT.
Third-party installers commonly rewrite the ``.js`` content type to
``text/plain`` there; Starlette's StaticFiles/FileResponse resolve
Content-Type through that same global map, and browsers refuse to execute
module scripts served as ``text/plain`` — the dashboard SPA loads as a blank
page (#81). ``hermes_cli.web_server`` pins the asset types it ships at import
time; these tests pin that behaviour.
"""
import mimetypes


def test_overrides_win_over_polluted_registry_mappings():
    import hermes_cli.web_server as ws

    # Simulate the HKCR pollution the bug report hit: .js mapped to
    # text/plain (add_type mutates the same global map the registry seeds).
    mimetypes.add_type("text/plain", ".js")
    mimetypes.add_type("text/plain", ".css")
    try:
        ws._harden_static_mime_types()
        assert mimetypes.guess_type("index-Bx1a2b3c.js")[0] == "text/javascript"
        assert mimetypes.guess_type("index-Bx1a2b3c.css")[0] == "text/css"
    finally:
        # Re-pin so the process-global map stays correct for other tests.
        ws._harden_static_mime_types()


def test_every_shipped_asset_type_resolves_to_its_pinned_mime():
    import hermes_cli.web_server as ws

    ws._harden_static_mime_types()
    for ext, expected in ws._STATIC_MIME_OVERRIDES.items():
        assert mimetypes.guess_type(f"asset{ext}")[0] == expected, ext


def test_module_import_applies_the_hardening():
    # Importing web_server (already imported above) must have registered the
    # overrides without any explicit call — the fix has to work for every
    # entry point that serves static files, not just ones that opt in.
    import hermes_cli.web_server as ws

    assert mimetypes.guess_type("chunk.mjs")[0] == ws._STATIC_MIME_OVERRIDES[".mjs"]
