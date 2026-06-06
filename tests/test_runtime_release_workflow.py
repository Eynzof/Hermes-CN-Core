"""Regression tests for the signed desktop runtime release workflow.

The desktop runtime is a frozen PyInstaller executable, so it CANNOT
lazy-install dependencies at first use (no working pip inside the binary).
Every backend the desktop exposes must therefore be pre-baked via the
``cn-desktop`` aggregate extra and collected by PyInstaller. These tests pin
that contract so a backend can't silently drop out of the build again — the
failure mode behind issue #16 (MCP) and the 飞书/钉钉/企微/微信 desktop reports.
"""

import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - CI runs 3.11+
    tomllib = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _workflow_text() -> str:
    workflow = _repo_root() / ".github" / "workflows" / "release-runtime.yml"
    return workflow.read_text(encoding="utf-8")


def _cn_desktop_extra() -> list[str]:
    if tomllib is None:  # pragma: no cover
        pytest.skip("tomllib unavailable")
    data = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["optional-dependencies"]["cn-desktop"]


def _frozen_verify_packages() -> set[str]:
    """The package names whose dist-info the workflow asserts in the frozen output.

    Parses the ``for pkg in ... ; do`` loop in the "Verify frozen runtime
    backends" step (continuation backslashes stripped) into a set of names.
    """
    text = _workflow_text()
    start = text.index("for pkg in ") + len("for pkg in ")
    body = text[start : text.index("; do", start)]
    return set(body.replace("\\", "").split())


def test_runtime_workflow_installs_cn_desktop_extra():
    """The frozen runtime installs the cn-desktop aggregate extra.

    cn-desktop is the single source of truth for "what the desktop ships".
    """
    assert 'pip install -e ".[cn-desktop]"' in _workflow_text()


def test_cn_desktop_extra_bundles_every_desktop_backend():
    """cn-desktop must pre-bake all backends the frozen runtime exposes."""
    extra = _cn_desktop_extra()
    blob = "\n".join(extra)
    # Aggregated sub-extras
    for sub in ("web", "anthropic", "mcp", "feishu", "dingtalk", "wecom"):
        assert f"[{sub}]" in blob, f"cn-desktop is missing the {sub} extra"
    # 微信 (weixin) has no dedicated extra — its adapter deps are listed directly
    assert any(e.startswith("aiohttp") for e in extra), "cn-desktop missing aiohttp (微信/feishu/wecom)"
    assert any(e.startswith("qrcode") for e in extra), "cn-desktop missing qrcode (scan-login)"
    assert any(e.startswith("cryptography") for e in extra), "cn-desktop missing cryptography (微信/wecom AES)"


def test_runtime_workflow_freezes_anthropic_sdk_for_minimax_cn():
    """MiniMax-CN rides the Anthropic Messages transport; the SDK must ship."""
    workflow = _workflow_text()
    assert any("[anthropic]" in e for e in _cn_desktop_extra())
    assert "--collect-submodules anthropic" in workflow
    assert "--copy-metadata anthropic" in workflow
    assert "anthropic" in _frozen_verify_packages()


def test_runtime_workflow_freezes_native_mcp_client():
    """Issue #16: the native MCP client SDK must ship in the frozen runtime."""
    workflow = _workflow_text()
    assert any("[mcp]" in e for e in _cn_desktop_extra())
    assert "--collect-submodules mcp" in workflow
    assert "--copy-metadata mcp" in workflow
    assert "mcp" in _frozen_verify_packages()


def test_runtime_workflow_freezes_im_platform_backends():
    """飞书/钉钉/企微/微信 adapters must ship — they can't lazy-install frozen."""
    workflow = _workflow_text()
    # Feishu / DingTalk SDKs collected by PyInstaller
    for mod in ("lark_oapi", "dingtalk_stream", "alibabacloud_dingtalk"):
        assert f"--collect-submodules {mod}" in workflow, f"missing --collect-submodules {mod}"
    # Shared adapter deps (Feishu webhook / WeCom / 微信)
    assert "--collect-submodules aiohttp" in workflow
    assert "--collect-submodules qrcode" in workflow
    # dist-info asserted in the frozen output
    verified = _frozen_verify_packages()
    for pkg in ("lark_oapi", "dingtalk_stream", "alibabacloud_dingtalk", "aiohttp", "qrcode", "defusedxml"):
        assert pkg in verified, f"verify step doesn't assert {pkg} dist-info"


def test_runtime_workflow_verifies_backends_in_build_env_and_frozen_output():
    """The workflow fails fast if a backend's SDK is missing.

    Two gates: a build-env import smoke test (catches a missing extra dep) and a
    dist-info assert over the frozen output (catches a PyInstaller collect miss).
    """
    workflow = _workflow_text()
    assert "Verify platform backends importable (build env)" in workflow
    assert "FEISHU_AVAILABLE" in workflow
    assert "Verify frozen runtime backends" in workflow
    assert ".dist-info" in workflow


def test_runtime_workflow_signs_and_preserves_macos_frameworks():
    workflow = _workflow_text()

    assert "Normalize macOS framework layout" in workflow
    assert "scripts/normalize_macos_pyinstaller_runtime.py" in workflow
    assert "Prepare macOS signing credentials" in workflow
    assert "scripts/sign_macos_runtime_payload.sh" in workflow
    assert "zip -r -y" in workflow
