"""Regression for portable v0.7.0: macOS runtime payload must carry entitlements.

Python 3.14's _ctypes / libffi calls mmap(PROT_EXEC) during startup. Under the
Hardened Runtime this is denied unless the runtime binary is signed with the
``com.apple.security.cs.allow-unsigned-executable-memory`` entitlement. The
``scripts/sign_macos_runtime_payload.sh`` script applies
``scripts/hermes-runtime.entitlements`` to every Mach-O and framework in the
payload; this test pins the contents of that entitlements file.
"""

import plistlib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
ENTITLEMENTS_PATH = REPO_ROOT / "scripts" / "hermes-runtime.entitlements"

REQUIRED_ENTITLEMENTS = {
    "com.apple.security.cs.allow-unsigned-executable-memory",
    "com.apple.security.cs.allow-jit",
    "com.apple.security.cs.disable-library-validation",
}


def test_runtime_entitlements_file_exists():
    assert ENTITLEMENTS_PATH.is_file(), f"missing {ENTITLEMENTS_PATH}"


def test_runtime_entitlements_contain_required_keys():
    with ENTITLEMENTS_PATH.open("rb") as f:
        data = plistlib.load(f)

    assert isinstance(data, dict), "entitlements should be a plist dict"
    for key in REQUIRED_ENTITLEMENTS:
        assert data.get(key) is True, f"required entitlement missing or false: {key}"
