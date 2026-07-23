"""Tests for the runtime manifest signer's cross-language payload contract.

The payload field order is a wire contract with the desktop client
(`Hermes-CN-Desktop/src/process/runtime.rs::signature_payload`), locked on the
Rust side by `signature_payload_has_stable_field_order` /
`signature_payload_v3_appends_min_app_version_as_field_13`. The fixtures here
use the same literal values as those Rust tests so both suites assert the same
golden payload without sharing files across repos.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "sign_runtime_manifest.py"
_SPEC = importlib.util.spec_from_file_location("sign_runtime_manifest", _SCRIPT_PATH)
assert _SPEC is not None
_module = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_module)


# Mirrors Hermes-CN-Desktop fixture_manifest() (src/process/runtime.rs tests).
_FIXTURE = {
    "schemaVersion": 2,
    "channel": "stable",
    "runtimeVersion": "1.2.3-cn.1",
    "kernelVersion": "1.2.3",
    "runtimeFlavor": "cn",
    "runtimeRevision": 1,
    "platform": "linux",
    "arch": "x64",
    "artifactUrl": "https://example.com/foo.zip",
    "sha256": "deadbeef",
    "sourceRepo": "owner/repo",
    "sourceCommit": "abc123",
    "minAppVersion": "0.7.0",
}


def _payload(schema_version: int) -> str:
    manifest = dict(_FIXTURE, schemaVersion=schema_version)
    return "\n".join(str(manifest[f]) for f in _module.payload_fields(schema_version))


def test_v2_payload_field_order_is_locked():
    assert _payload(2).split("\n") == [
        "2",
        "stable",
        "1.2.3-cn.1",
        "1.2.3",
        "cn",
        "1",
        "linux",
        "x64",
        "https://example.com/foo.zip",
        "deadbeef",
        "owner/repo",
        "abc123",
    ]


def test_v3_payload_appends_min_app_version_as_field_13():
    lines = _payload(3).split("\n")
    assert len(lines) == 13
    assert lines[0] == "3"
    assert lines[12] == "0.7.0"
    # Fields 2..12 keep the exact v2 order.
    assert lines[1:12] == _payload(2).split("\n")[1:12]


def test_v2_payload_never_includes_min_app_version():
    # v2 signatures must stay stable whether or not the optional (unsigned)
    # minAppVersion field is present in the written manifest.
    assert "minAppVersion" not in _module.payload_fields(2)
    assert len(_module.payload_fields(2)) == 12


def test_default_schema_is_v3():
    assert _module.DEFAULT_SCHEMA_VERSION == 3
    assert _module.SUPPORTED_SCHEMA_VERSIONS == (2, 3)


@pytest.mark.parametrize(
    ("value", "ok"),
    [
        ("0.7.0", True),
        ("1.0.0-rc.1", True),
        ("0.7", False),
        ("v0.7.0", False),
        ("latest", False),
        ("", False),
    ],
)
def test_min_app_version_format_gate(value: str, ok: bool):
    # The desktop gate silently ignores unparseable minAppVersion values, so
    # the signer must reject them at release time.
    assert bool(_module._MIN_APP_VERSION_RE.match(value)) is ok
