"""Regression for macOS runtime executable-memory entitlement.

Python 3.14's _ctypes / libffi calls mmap(PROT_EXEC) during startup. Under the
Hardened Runtime this is denied unless the runtime binary is signed with the
``com.apple.security.cs.allow-unsigned-executable-memory`` entitlement. The
``scripts/sign_macos_runtime_payload.sh`` script applies the narrow entitlement
plist only to the main PyInstaller executable, not to bundled dylibs/frameworks.
"""

import plistlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
ENTITLEMENTS_PATH = REPO_ROOT / "scripts" / "macos-runtime.entitlements.plist"

EXPECTED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory": True,
}


def test_runtime_entitlements_file_exists():
    assert ENTITLEMENTS_PATH.is_file(), f"missing {ENTITLEMENTS_PATH}"


def test_runtime_entitlements_are_minimal():
    with ENTITLEMENTS_PATH.open("rb") as f:
        data = plistlib.load(f)

    assert isinstance(data, dict), "entitlements should be a plist dict"
    assert data == EXPECTED_ENTITLEMENTS
