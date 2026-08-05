"""Regression tests for the signed desktop runtime release workflow.

The desktop runtime is a frozen PyInstaller executable, so it CANNOT
lazy-install dependencies at first use (no working pip inside the binary).
Every backend the desktop exposes must therefore be pre-baked via the
``cn-desktop`` aggregate extra and collected by PyInstaller. These tests pin
that contract so a backend can't silently drop out of the build again — the
failure mode behind issue #16 (MCP) and the 飞书/钉钉/企微/微信 desktop reports.
"""

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from packaging.specifiers import SpecifierSet
from packaging.version import Version

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - CI runs 3.11+
    tomllib = None


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _workflow_text() -> str:
    workflow = _repo_root() / ".github" / "workflows" / "release-runtime.yml"
    return workflow.read_text(encoding="utf-8", errors="replace")


def _cn_desktop_extra() -> list[str]:
    if tomllib is None:  # pragma: no cover
        pytest.skip("tomllib unavailable")
    data = tomllib.loads((_repo_root() / "pyproject.toml").read_text(encoding="utf-8", errors="replace"))
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


def test_runtime_workflow_python_satisfies_project_requirement():
    """The release builder must run a Python accepted by pyproject.toml."""
    if tomllib is None:  # pragma: no cover
        pytest.skip("tomllib unavailable")

    project = tomllib.loads(
        (_repo_root() / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    )["project"]
    match = re.search(
        r"python-version:\s*[\"']([^\"']+)[\"']",
        _workflow_text(),
    )

    assert match, "release-runtime.yml must configure actions/setup-python"
    workflow_python = Version(match.group(1))
    requires_python = SpecifierSet(project["requires-python"])
    assert workflow_python in requires_python, (
        f"release-runtime.yml uses Python {workflow_python}, which does not "
        f"satisfy pyproject.toml requires-python {requires_python}"
    )


def test_cn_desktop_extra_bundles_every_desktop_backend():
    """cn-desktop must pre-bake all backends the frozen runtime exposes."""
    extra = _cn_desktop_extra()
    blob = "\n".join(extra)
    # Aggregated sub-extras
    for sub in (
        "web",
        "anthropic",
        "mcp",
        "computer-use",
        "exa",
        "firecrawl",
        "parallel-web",
        "ddgs",
        "feishu",
        "dingtalk",
        "wecom",
    ):
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


def test_runtime_workflow_builds_and_verifies_web_dist():
    """Issue #116: the frozen runtime must ship the dashboard SPA bundle."""
    workflow = _workflow_text()
    assert "Build web dashboard" in workflow
    assert "hermes_cli/web_dist/index.html not built" in workflow
    assert "--collect-data hermes_cli" in workflow
    assert "Verify frozen runtime resources" in workflow
    assert "hermes_cli/web_dist/index.html" in workflow


def test_runtime_workflow_freezes_computer_use_tooling():
    """Issue #106: Desktop-visible computer_use must be importable frozen."""
    workflow = _workflow_text()
    assert any("[computer-use]" in e for e in _cn_desktop_extra())
    assert "--collect-submodules tools" in workflow
    for mod in (
        "tools.computer_use_tool",
        "tools.computer_use.tool",
        "tools.computer_use.doctor",
        "tools.computer_use.cua_backend",
    ):
        assert mod in workflow
    assert "tools/computer_use_tool.py" in workflow
    assert "tools/computer_use/tool.py" in workflow
    assert "computer-use status" in workflow
    assert "cua-driver" not in "\n".join(_cn_desktop_extra())


def test_runtime_workflow_freezes_web_search_providers():
    """Issue #111: bundled Web providers and SDK metadata must ship frozen."""
    workflow = _workflow_text()
    extra = _cn_desktop_extra()
    for sub in ("exa", "firecrawl", "parallel-web", "ddgs"):
        assert any(f"[{sub}]" in e for e in extra), f"cn-desktop missing {sub}"
    for mod in ("plugins.web", "ddgs", "exa_py", "firecrawl", "parallel"):
        assert f"--collect-submodules {mod}" in workflow
    for dist in ("ddgs", "exa-py", "firecrawl-py", "parallel-web"):
        assert f"--copy-metadata {dist}" in workflow
    for pkg in ("ddgs", "exa_py", "firecrawl_py", "parallel_web"):
        assert pkg in _frozen_verify_packages()
    for plugin in ("brave_free", "ddgs", "exa", "firecrawl", "parallel", "searxng", "tavily", "xai"):
        assert f"plugins.web.{plugin}" in workflow
        assert f"plugins/web/$p/plugin.yaml" in workflow


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


def test_release_workflow_imports_migrated_platform_adapters_from_plugins():
    """飞书/钉钉/企微 adapters migrated from gateway/platforms/*.py to bundled
    plugins (plugins/platforms/<name>/) in the upstream sync — see P-035. The
    build-env gate must import them from the new location; the old
    gateway.platforms.<name> modules are gone. weixin (微信个人号, CN-only) stayed
    in gateway.platforms. Guards the exact drift that broke runtime-v0.17.0-cn.3
    (``ModuleNotFoundError: No module named 'gateway.platforms.feishu'``).
    """
    root = _repo_root()

    # Filesystem reality: migrated out of gateway.platforms, into plugins.
    for name in ("feishu", "dingtalk"):
        assert (root / "plugins" / "platforms" / name / "adapter.py").exists()
        assert not (root / "gateway" / "platforms" / f"{name}.py").exists()
    assert (root / "plugins" / "platforms" / "wecom" / "adapter.py").exists()
    assert (root / "plugins" / "platforms" / "wecom" / "callback_adapter.py").exists()
    assert not (root / "gateway" / "platforms" / "wecom_callback.py").exists()
    # weixin is CN-fork-only and was NOT migrated.
    assert (root / "gateway" / "platforms" / "weixin.py").exists()

    # The build-env gate must target the live locations, not the removed modules.
    workflow = _workflow_text()
    for dead in (
        "gateway.platforms.feishu",
        "gateway.platforms.dingtalk",
        "gateway.platforms.wecom_callback",
    ):
        assert dead not in workflow, f"release-runtime.yml still imports removed module {dead}"
    for live in (
        "plugins.platforms.feishu.adapter",
        "plugins.platforms.dingtalk.adapter",
        "plugins.platforms.wecom.adapter",
    ):
        assert live in workflow, f"release-runtime.yml does not import {live}"

    # The adapters import without the optional SDKs (deps wrapped in try/except)
    # and expose the SDK-availability flags the gate reads. Asserting the flag
    # attributes exist — not their truthiness — keeps this honest in any test env.
    import importlib

    fs = importlib.import_module("plugins.platforms.feishu.adapter")
    dt = importlib.import_module("plugins.platforms.dingtalk.adapter")
    wcm = importlib.import_module("plugins.platforms.wecom.adapter")
    wc = importlib.import_module("plugins.platforms.wecom.callback_adapter")
    wx = importlib.import_module("gateway.platforms.weixin")
    assert hasattr(fs, "FEISHU_AVAILABLE")
    assert hasattr(dt, "DINGTALK_STREAM_AVAILABLE") and hasattr(dt, "CARD_SDK_AVAILABLE")
    assert hasattr(wcm, "AIOHTTP_AVAILABLE")
    assert hasattr(wc, "DEFUSEDXML_AVAILABLE") and hasattr(wc, "AIOHTTP_AVAILABLE")
    assert hasattr(wx, "AIOHTTP_AVAILABLE") and hasattr(wx, "CRYPTO_AVAILABLE")


def test_runtime_workflow_verifies_backends_in_build_env_and_frozen_output():
    """The workflow fails fast if a backend's SDK is missing.

    Two gates: a build-env import smoke test (catches a missing extra dep) and a
    dist-info assert over the frozen output (catches a PyInstaller collect miss).
    """
    workflow = _workflow_text()
    assert "Verify platform backends importable (build env)" in workflow
    assert "FEISHU_AVAILABLE" in workflow
    # The gate must not import IM adapters from their pre-migration location.
    assert "gateway.platforms.feishu" not in workflow
    assert "Verify frozen runtime backends" in workflow
    assert ".dist-info" in workflow


def test_runtime_workflow_bundles_openviking_provider_without_server_sdk():
    """OpenViking ships as an HTTP provider, not as the heavy server SDK.

    The provider implementation talks to an existing OpenViking service via
    httpx. The CN desktop runtime already ships httpx as a core dependency, so
    bundling the provider must not pull in the full openviking package or the
    local openviking-server dependency tree.
    """
    workflow = _workflow_text()
    extra = _cn_desktop_extra()
    pyproject = (_repo_root() / "pyproject.toml").read_text(encoding="utf-8", errors="replace")
    provider = (
        _repo_root() / "plugins" / "memory" / "openviking" / "__init__.py"
    ).read_text(encoding="utf-8", errors="replace")
    manifest = (
        _repo_root() / "plugins" / "memory" / "openviking" / "plugin.yaml"
    ).read_text(encoding="utf-8", errors="replace")

    assert "name: openviking" in manifest
    assert "pip_dependencies:" in manifest
    assert "  - httpx" in manifest
    assert "uses httpx to avoid requiring the openviking SDK" in provider
    assert "import httpx" in provider
    assert "import openviking" not in provider
    assert "from openviking" not in provider

    # The runtime must include bundled provider files, but not the full
    # OpenViking server/CLI wheel. Pulling that package into cn-desktop would
    # add hundreds of MB of transitive dependencies to every runtime artifact.
    assert "--collect-data plugins" in workflow
    assert 'httpx[socks]==0.28.1' in pyproject
    assert not any("openviking" in dep.lower() for dep in extra), (
        "cn-desktop should not install the full openviking server SDK; "
        "the bundled provider only needs the core httpx dependency."
    )
    assert "--collect-submodules openviking" not in workflow
    assert "--copy-metadata openviking" not in workflow


def test_runtime_workflow_freezes_hindsight_client_for_long_term_memory():
    """The CN desktop frozen runtime pre-bakes the Hindsight memory client.

    mirrors test_runtime_workflow_freezes_native_mcp_client: cn-desktop extra
    pulls in [hindsight], the PyInstaller build collects hindsight_client and
    copies its dist metadata, and the frozen-output verify step asserts the
    hindsight_client-*.dist-info directory is present. A missing piece here
    means hermes_recall/reflect/retain fail with ``ModuleNotFoundError`` inside
    the frozen binary (issue: hindsight-client not in PyInstaller bundle).
    """
    workflow = _workflow_text()
    extra = _cn_desktop_extra()

    # 1. cn-desktop must transitively pull in the [hindsight] sub-extra.
    assert any("[hindsight]" in e for e in extra), (
        "cn-desktop extra is missing hermes-agent[hindsight] "
        "(frozen runtime cannot lazy-install hindsight-client)"
    )

    # 2. Build-env import smoke test must include hindsight_client.
    #    Re-parse the same for-tuple the existing helper does, so a future
    #    re-shuffle of the import list still keeps this assertion honest.
    text = workflow
    start = text.index("for m in (") + len("for m in (")
    body = text[start : text.index("):", start)]
    import_list = body.replace("\n", " ").replace('"', " ").split()
    assert "hindsight_client" in import_list, (
        "release-runtime.yml build-env import smoke test does not "
        "check hindsight_client; PyInstaller may bundle a missing SDK."
    )

    # 3. PyInstaller collect / metadata lines.
    assert "--collect-submodules hindsight_client" in workflow, (
        "PyInstaller invocation missing --collect-submodules hindsight_client"
    )
    assert "--copy-metadata hindsight-client" in workflow, (
        "PyInstaller invocation missing --copy-metadata hindsight-client "
        "(pip distribution name with hyphen, not the underscored module name)"
    )

    # 4. Frozen-output verify list must assert the dist-info directory.
    verified = _frozen_verify_packages()
    assert "hindsight_client" in verified, (
        "frozen-output verify list does not assert hindsight_client dist-info"
    )


def test_runtime_workflow_freezes_hindsight_client_api_and_aiohttp_retry():
    """hindsight-client==0.6.1 ships hindsight_client_api as a bundled top-level
    package and depends on aiohttp-retry as an independent distribution. The
    previous PR (#39 round 1) only collected hindsight_client itself, which
    proved insufficient in a real frozen runtime smoke test: the recall tool
    returned HTTP 500 with
        No module named 'hindsight_client_api'
    after the first round, and
        No module named 'aiohttp_retry'
    after the second round. Both transitive pieces must be explicitly
    collected and the independent aiohttp-retry dist-info must be copied.
    """
    workflow = _workflow_text()
    extra = _cn_desktop_extra()

    # 1. cn-desktop extra still pulls in [hindsight] (round 1 contract).
    assert any("[hindsight]" in e for e in extra)

    # 2. Build-env import smoke test must cover all three modules.
    text = workflow
    start = text.index("for m in (") + len("for m in (")
    body = text[start : text.index("):", start)]
    import_list = body.replace("\n", " ").replace('"', " ").split()
    for mod in ("hindsight_client", "hindsight_client_api", "aiohttp_retry"):
        assert mod in import_list, (
            f"release-runtime.yml build-env import smoke test does not "
            f"check {mod}; PyInstaller may bundle a missing dependency."
        )

    # 3. PyInstaller must collect all three submodules.
    #    hindsight_client_api is bundled inside the same wheel as
    #    hindsight_client (verified by importlib.metadata.packages_distributions()
    #    mapping both modules to the same "hindsight-client" distribution) but
    #    PyInstaller static analysis does not automatically pick up sibling
    #    top-level packages, so it must be named explicitly.
    for mod in ("hindsight_client", "hindsight_client_api", "aiohttp_retry"):
        assert f"--collect-submodules {mod}" in workflow, (
            f"PyInstaller invocation missing --collect-submodules {mod}"
        )

    # 4. PyInstaller must copy the independent dist-info.
    #    - hindsight-client: required (covers hindsight_client + hindsight_client_api)
    #    - aiohttp-retry: required (independent distribution)
    #    - hindsight-client-api: NOT a real distribution, must NOT be added
    assert "--copy-metadata hindsight-client" in workflow, (
        "PyInstaller invocation missing --copy-metadata hindsight-client"
    )
    assert "--copy-metadata aiohttp-retry" in workflow, (
        "PyInstaller invocation missing --copy-metadata aiohttp-retry "
        "(aiohttp-retry is an independent distribution, not bundled inside "
        "hindsight-client)"
    )
    assert "--copy-metadata hindsight-client-api" not in workflow, (
        "release-runtime.yml must NOT pass --copy-metadata hindsight-client-api: "
        "hindsight_client_api has no independent distribution metadata; it "
        "shares hindsight_client-*.dist-info with the main hindsight_client "
        "package (verified via importlib.metadata.packages_distributions()). "
        "Adding a non-existent --copy-metadata is a build-time typo."
    )

    # 5. Frozen-output verify list must assert the dist-info directory of
    #    every independent distribution. aiohttp_retry is independent, so
    #    its aiohttp_retry-*.dist-info must be asserted; hindsight_client_api
    #    shares the hindsight_client-*.dist-info, so the single hindsight_client
    #    entry already covers it.
    # Frozen-output verify list asserts dist-info directories of INDEPENDENT
    # distributions only. hindsight_client_api has no independent distribution;
    # it shares hindsight_client-*.dist-info with the main hindsight_client
    # package (verified by importlib.metadata.packages_distributions()), so
    # putting "hindsight_client_api" in the verify list would either be a
    # silent no-op (find would not match anything) or, worse, mask a real
    # build break. Only hindsight_client and aiohttp_retry are asserted here.
    verified = _frozen_verify_packages()
    for pkg in ("hindsight_client", "aiohttp_retry"):
        assert pkg in verified, (
            f"frozen-output verify list does not assert {pkg} dist-info"
        )
    assert "hindsight_client_api" not in verified, (
        "frozen-output verify list must NOT contain hindsight_client_api: "
        "it has no independent distribution metadata. Listing it here is "
        "either a silent no-op (the find pattern would never match) or a "
        "future regression hiding the real dist-info directory. The "
        "hindsight_client entry already covers it because both modules share "
        "hindsight_client-*.dist-info."
    )


def test_runtime_workflow_signs_and_preserves_macos_frameworks():
    workflow = _workflow_text()

    assert "Normalize macOS framework layout" in workflow
    assert "scripts/normalize_macos_pyinstaller_runtime.py" in workflow
    assert "Prepare macOS signing credentials" in workflow
    assert "scripts/sign_macos_runtime_payload.sh" in workflow
    assert "zip -r -y" in workflow


def test_macos_runtime_signing_grants_ctypes_executable_memory_to_main_only():
    """The hardened Python 3.14 executable must permit libffi trampolines.

    macOS 13 otherwise spins in ``ffi_closure_alloc`` before even
    ``dashboard --help`` returns. The exception belongs on the loading main
    executable, not on every bundled dylib/framework.
    """
    root = _repo_root()
    script = (root / "scripts" / "sign_macos_runtime_payload.sh").read_text(
        encoding="utf-8", errors="replace"
    )
    entitlements_path = root / "scripts" / "macos-runtime.entitlements.plist"
    with entitlements_path.open("rb") as handle:
        entitlements = plistlib.load(handle)

    assert entitlements == {
        "com.apple.security.cs.allow-unsigned-executable-memory": True,
    }
    assert "find_main_runtime_binary()" in script
    assert 'main_binary="$(find_main_runtime_binary || true)"' in script
    assert 'if [[ "$path" == "$main_binary" ]]; then' in script
    assert 'path_sign_args+=(--entitlements "$entitlements_file")' in script
    assert 'codesign -d --entitlements - "$main_binary"' in script
    assert "grep -q 'com.apple.security.cs.allow-unsigned-executable-memory'" in script
    assert "<key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/>" not in script


def test_macos_runtime_signing_script_applies_entitlement_to_main_only(tmp_path):
    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is required to exercise the signing script")

    root = _repo_root()
    runtime_dir = tmp_path / "runtime"
    runtime_dir.mkdir()
    main_binary = runtime_dir / "hermes-agent-cn-runtime-darwin-arm64"
    helper_dylib = runtime_dir / "libhelper.dylib"
    framework = runtime_dir / "Helper.framework"
    main_binary.write_text("main", encoding="utf-8")
    helper_dylib.write_text("helper", encoding="utf-8")
    framework.mkdir()

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_path = tmp_path / "codesign.log"
    fake_file = bin_dir / "file"
    fake_file.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s: Mach-O 64-bit executable\\n' \"$1\"\n",
        encoding="utf-8",
    )
    fake_codesign = bin_dir / "codesign"
    fake_codesign.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$*\" >> \"$CODESIGN_LOG\"\n"
        "if [[ \"$1\" == '-d' ]]; then\n"
        "  printf '<plist><dict><key>com.apple.security.cs.allow-unsigned-executable-memory</key><true/></dict></plist>\\n'\n"
        "fi\n",
        encoding="utf-8",
    )
    fake_file.chmod(0o755)
    fake_codesign.chmod(0o755)

    env = {
        "APPLE_SIGNING_IDENTITY": "-",
        "CODESIGN_LOG": str(log_path),
        "PATH": f"{bin_dir}:{os.environ.get('PATH', '')}",
    }
    result = subprocess.run(
        [bash, str(root / "scripts" / "sign_macos_runtime_payload.sh"), str(runtime_dir)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    calls = log_path.read_text(encoding="utf-8").splitlines()
    signing_calls = [call for call in calls if call.startswith("--force --sign -")]
    entitlement_signing_calls = [
        call for call in signing_calls if "--entitlements" in call
    ]
    expected_entitlements = root / "scripts" / "macos-runtime.entitlements.plist"
    assert entitlement_signing_calls == [
        f"--force --sign - --entitlements {expected_entitlements} {main_binary}"
    ]
    assert not any(
        "--entitlements" in call and str(helper_dylib) in call
        for call in signing_calls
    )
    assert not any(
        "--entitlements" in call and str(framework) in call
        for call in signing_calls
    )
    assert f"-d --entitlements - {main_binary}" in calls
