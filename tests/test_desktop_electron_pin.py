"""Regression: the desktop Electron dependency must be an exact, consistent pin.

The Windows desktop install failed at "Building desktop app" because Electron
changed its install mechanism mid patch-series:

    electron 40.9.3 .. 40.10.2  -> @electron/get@^2 + extract-zip@^2  (pure JS)
    electron 40.10.3 / 40.10.4  -> @electron/get@^5 +
                                   @electron-internal/extract-zip@^1 (native napi)

``apps/desktop/package.json`` declared ``electronVersion: 40.9.3`` (the tested,
JS-extract build) but pinned the dependency loosely as ``electron: ^40.9.3``.
``npm ci`` then resolved 40.10.3/40.10.4 — the new *native* extract-zip whose
win32-x64 binding fails to ``dlopen`` on some Windows hosts
(``ERR_DLOPEN_FAILED loading index.win32-x64-msvc.node``).

Upstream then moved the whole electron-builder configuration out of the
``build`` field of ``package.json`` into ``apps/desktop/electron-builder.config.cjs``
(``run-electron-builder.mjs`` passes it with ``--config``, so a stray
``package.json`` field would be silently ignored). That module now *derives*
``electronVersion`` from the pinned dependency, which is where the packaged
version is read: the test evaluates the real config rather than re-reading a
copy of the value.

These tests lock the contract that prevents that drift, without hard-coding the
specific version (which is allowed to move):

1. the Electron dependency is an *exact* version (Electron Builder needs the
   installed binary to match the config's ``electronVersion`` / ``electronDist``),
2. the dependency, the builder config's ``electronVersion``, and the resolved
   lockfile entry all agree — so ``npm ci`` installs exactly what the build
   packages.
"""
from __future__ import annotations


import orjson
from agent.re_compat import re
from pathlib import Path
import shutil
import subprocess

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DESKTOP_PKG = REPO_ROOT / "apps" / "desktop" / "package.json"
BUILDER_CONFIG = REPO_ROOT / "apps" / "desktop" / "electron-builder.config.cjs"
ROOT_LOCK = REPO_ROOT / "package-lock.json"

# An exact semver: digits.digits.digits with an optional prerelease/build tag,
# but NO range operators (^ ~ > < = * x || spaces || -range).
_EXACT_SEMVER = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")


def _desktop_pkg() -> dict:
    assert DESKTOP_PKG.is_file(), f"missing {DESKTOP_PKG}"
    return orjson.loads(DESKTOP_PKG.read_text(encoding="utf-8", errors="replace"))


def _electron_spec(pkg: dict) -> str:
    for section in ("dependencies", "devDependencies"):
        spec = pkg.get(section, {}).get("electron")
        if spec:
            return spec
    pytest.fail("electron is not listed in apps/desktop dependencies")


def _builder_electron_version() -> str:
    """The ``electronVersion`` electron-builder actually packages.

    Read by evaluating the real config module (node), not by grepping it: the value
    is derived at require time from the pinned dependency, and a copy in the test
    would be the drift this file exists to prevent.
    """
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required to evaluate the electron-builder config")
    result = subprocess.run(
        [node, "-e",
         "process.stdout.write(require('./electron-builder.config.cjs').electronVersion)"],
        cwd=str(BUILDER_CONFIG.parent), capture_output=True, text=True, timeout=120,
        stdin=subprocess.DEVNULL,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_electron_dependency_is_exactly_pinned():
    """A loose range lets npm drift onto an Electron with a different installer."""
    spec = _electron_spec(_desktop_pkg())
    assert _EXACT_SEMVER.match(spec), (
        f"electron must be pinned to an exact version, got {spec!r}. "
        "A range (^/~) lets npm ci resolve a newer Electron whose postinstall "
        "may differ from the one the build was validated against."
    )


def test_electron_dependency_matches_electron_version():
    """electron-builder packages the config's electronVersion against the installed binary."""
    pkg = _desktop_pkg()
    spec = _electron_spec(pkg)
    builder_version = _builder_electron_version()
    assert builder_version, "electron-builder.config.cjs declares no electronVersion"
    assert spec == builder_version, (
        f"electron dependency ({spec!r}) must equal the builder config's "
        f"electronVersion ({builder_version!r}); otherwise electron-builder packages a "
        "different version than npm installs into electronDist."
    )


def test_package_json_carries_no_second_electron_pin():
    """One source for the packaged version.

    electron-builder is always invoked with ``--config apps/desktop/electron-builder.config.cjs``,
    so a ``build.electronVersion`` in package.json is silently ignored — exactly the
    kind of stale second pin that shipped 40.9.3 while npm installed 40.10.3.
    """
    build = _desktop_pkg().get("build", {})
    assert "electronVersion" not in build, (
        "package.json build.electronVersion is ignored by the build "
        "(run-electron-builder.mjs passes --config for electron-builder.config.cjs); "
        "keep the electron version in apps/desktop/electron-builder.config.cjs only."
    )


def test_lockfile_resolves_the_pinned_electron():
    """npm ci installs from the lockfile, so it must agree with the pin."""
    if not ROOT_LOCK.is_file():
        pytest.skip("root package-lock.json not present")
    spec = _electron_spec(_desktop_pkg())
    lock = orjson.loads(ROOT_LOCK.read_text(encoding="utf-8", errors="replace"))
    packages = lock.get("packages", {})
    resolved = [
        meta.get("version")
        for path, meta in packages.items()
        if path.endswith("node_modules/electron") and meta.get("version")
    ]
    assert resolved, "no electron entry found in package-lock.json"
    assert all(v == spec for v in resolved), (
        f"package-lock.json resolves electron to {sorted(set(resolved))}, "
        f"but the pin is {spec!r}; run `npm install --package-lock-only` so "
        "`npm ci` stays consistent."
    )