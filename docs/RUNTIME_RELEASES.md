# Runtime release pipeline

The hermes-agent-cn-desktop client (Tauri desktop app) downloads a
hermes-agent-cn runtime on first launch and uses it to spawn the
dashboard subprocess. This document describes how that runtime is built,
signed, and published.

## Wire shape

The client expects a per-platform manifest JSON at:

```
${HERMES_RUNTIME_UPDATE_BASE_URL}/${channel}-${platform}-${arch}.json
```

The filename is flat (no subdirectories) so GitHub Releases — where all
assets for a tag share one directory — works out of the box. Pointing
the base URL at `releases/latest/download` keeps the desktop on the
newest published release automatically:

```
https://github.com/Eynzof/hermes-agent-cn/releases/latest/download/stable-win32-x64.json
```

For the community production channel, manifests and archives use the release
mirror. The workflow fixes the immutable download URL **before signing**:

```
https://hot-update-download.hermesagent.org.cn/runtime-v0.21.0-cn.18/stable-win32-x64.json
https://hot-update-download.hermesagent.org.cn/runtime-v0.21.0-cn.18/hermes-agent-cn-runtime-win32-x64.zip
```

`RUNTIME_ARTIFACT_BASE_URL` is an optional repository variable for a custom
HTTPS mirror root. Do not rewrite `artifactUrl` after signing: the desktop
signature check covers this field. Updating the channel's manifest pointer
must preserve the original signed JSON bytes.

The canonical versioning contract is documented in `docs/RUNTIME_VERSIONING.md`.
The manifest schema (see `src/process/runtime.rs::RuntimeUpdateManifest`
on the desktop side) is schema v2:

```json
{
  "schemaVersion": 2,
  "channel": "stable",
  "runtimeVersion": "0.14.0-cn.1",
  "kernelVersion": "0.14.0",
  "runtimeFlavor": "cn",
  "runtimeRevision": 1,
  "platform": "win32",
  "arch": "x64",
  "artifactUrl": "https://.../hermes-agent-cn-runtime-win32-x64.zip",
  "sha256": "abcdef0123...",
  "signature": "base64-encoded Ed25519 signature",
  "sourceRepo": "Eynzof/hermes-agent-cn",
  "sourceCommit": "01edd139...",
  "minAppVersion": "0.1.0",
  "createdAt": "2026-05-16T03:00:00Z"
}
```

The signature is over the twelve canonical schema v2 fields concatenated with `\n`
in this exact order:

```
schemaVersion\nchannel\nruntimeVersion\nkernelVersion\nruntimeFlavor\nruntimeRevision\nplatform\narch\nartifactUrl\nsha256\nsourceRepo\nsourceCommit
```

`scripts/sign_runtime_manifest.py` builds this payload identically to
how the desktop verifies it (`signature_payload()` in `runtime.rs`).
**Any field-order change must be made on both sides simultaneously.**

## Keys

* Algorithm: Ed25519 (32-byte raw public key, SPKI-DER-wrapped PEM).
* The desktop binary embeds the public key at build time via the
  `HERMES_RUNTIME_UPDATE_PUBLIC_KEY_PEM_DEFAULT` build env var.
* The private key is held only as the `RUNTIME_SIGN_PRIVATE_KEY_PEM`
  GitHub Actions secret — never written to disk in CI, never in source.

### Current public key

```
-----BEGIN PUBLIC KEY-----
MCowBQYDK2VwAyEAqPkLQ4o67G2GMTgkQQQZXWwDBZM/4hqq5thSZSNhoC0=
-----END PUBLIC KEY-----
```

If you need to rotate, generate a new pair, swap both:

* GitHub secret `RUNTIME_SIGN_PRIVATE_KEY_PEM` in this repo
* Build env `HERMES_RUNTIME_UPDATE_PUBLIC_KEY_PEM_DEFAULT` in the
  hermes-agent-cn-desktop release workflow

macOS runtime releases also require the same Apple Developer ID signing
secrets used by the desktop release workflow:

* `APPLE_CERTIFICATE` — base64-encoded Developer ID Application `.p12`
* `APPLE_CERTIFICATE_PASSWORD`
* `APPLE_SIGNING_IDENTITY` — for example `Developer ID Application: ...`

Cut a new desktop release at the same time — older desktop builds carry
the old key and will reject anything signed by the new one.

## Cutting a release

1. Pick the next CN runtime revision for the current `[project].version`; see `docs/RUNTIME_VERSIONING.md`.
2. Tag the commit you want to ship:
   ```
   git tag runtime-v0.14.0-cn.1
   git push origin runtime-v0.14.0-cn.1
   ```
3. The `release-runtime` workflow validates the tag against `pyproject.toml` and runs once per platform (Windows-x64 / macOS-arm64 / macOS-x64 / Linux-x64). The manifest signing key is required even for manual candidate builds; an unsigned build fails before packaging.
4. Each job:
   - Builds a self-contained executable via PyInstaller
   - On macOS, normalizes PyInstaller-collected `.framework` directories back
     into standard symlink framework layouts
   - On macOS, signs the full runtime payload with Developer ID before packaging
   - Smoke-tests it (`dashboard --help` must exit 0)
   - Zips the dist directory as `hermes-agent-cn-runtime-<platform>-<arch>.zip`
     and preserves symlinks for macOS artifacts
   - Signs the manifest with `scripts/sign_runtime_manifest.py`
5. The aggregate `release` job verifies all four archives against their signed
   manifest hashes and checks that their version, channel and source commit
   agree. Tag-triggered builds create a **draft** GitHub Release. They do not
   automatically publish or change GitHub's latest release.
6. Accept the exact final archives, then publish that draft without rebuilding.
   Verify the mirror's full downloads, hashes and range responses before
   promoting the stable channel's manifest pointers. Keep the previous
   release available for rollback.

For a build without creating a GitHub Release, dispatch `release-runtime` on
the frozen source branch with `version=0.21.0-cn.18` and `channel=stable`.
`artifact_tag` defaults to `runtime-v<version>` and may be explicitly overridden
for an isolated test release. The signed archives and manifests are retained as
Actions artifacts; the same bytes can subsequently be uploaded to a draft.

### Intel macOS local speech dependency

PyPI does not publish an ONNX Runtime CPython 3.14 wheel for Intel macOS.
The Intel job keeps the locked `onnxruntime==1.27.0` dependency but defers its
installation until `scripts/build_macos_intel_onnxruntime.sh` builds a CPU wheel
from Microsoft's `v1.27.0` commit `8f0278c77bf44b0cc83c098c6c722b92a36ac4b5`.
Only this platform uses the source wheel; other platforms keep their locked
PyPI packages. Local speech recognition remains included. The wheel builder
explicitly targets `macosx-14.0-x86_64`, because the CI Python interpreter is
universal2. Staging verifies the internal WHEEL tag and every native library's
actual Mach-O architecture; renaming a wheel cannot satisfy these checks.

The wheel cache is scoped to the source commit, Python ABI, architecture,
requested deployment target and build script. A build record contains its
SHA-256. GitHub also isolates caches by ref, so different release tags do not
automatically share this cache. Both the build environment and frozen runtime
execute local Silero VAD inference without downloading a model, so import
success alone is insufficient.

Intel cryptography 50 is also built from its locked source distribution. Before
dependency installation, CI builds OpenSSL 3.5.8 LTS from its official tarball,
checks its fixed SHA-256, and targets macOS 14 with `no-shared no-module`.
`OPENSSL_STATIC=1` and `OPENSSL_DIR` keep cryptography independent of the runner's
Homebrew libraries; the legacy provider is built in and tested with ARC4.
AES, legacy-provider operation, the actual cryptography/Python SSL links and
Intel deployment targets are checked before the long ORT compilation.
The verified ORT wheel is immediately saved and uploaded as a separate
`dependency-onnxruntime-darwin-x64` artifact, even if a later packaging step fails.

The v0.9.0 release targets macOS 14.0 on both architectures. Each macOS CI job uploads a
`diagnostics-runtime-darwin-<arch>` artifact recording every Mach-O deployment
target and the highest declared minimum, and rejects any binary requiring a
version above 14.0. The deployment target is not a substitute for actual
oldest-system acceptance. Do not reuse the
minimum of a local Homebrew-built RC as evidence for the CI-built release.

Once the release exists, every hermes-agent-cn-desktop install whose
manifest URL points at this base URL will pick up the update on next
launch (or via the in-app "check for updates" flow).

## Manual dry run

```
$ pip install -e ".[cn-desktop]"
$ pip install pyinstaller cryptography
$ pyinstaller --noconfirm --name hermes-agent-cn-runtime-win32-x64 \
    --onedir --console \
    --collect-submodules hermes_cli --collect-submodules tui_gateway \
    --collect-submodules fastapi --collect-submodules starlette \
    --collect-submodules uvicorn --collect-submodules pydantic \
    --collect-submodules anthropic --collect-submodules mcp \
    --collect-submodules lark_oapi --collect-submodules dingtalk_stream \
    --collect-submodules alibabacloud_dingtalk \
    --collect-submodules alibabacloud_tea_openapi \
    --collect-submodules alibabacloud_tea_util \
    --collect-submodules aiohttp --collect-submodules qrcode \
    --copy-metadata anthropic --copy-metadata mcp \
    --copy-metadata lark_oapi --copy-metadata dingtalk_stream \
    --copy-metadata alibabacloud_dingtalk \
    --collect-data hermes_cli --collect-data gateway --collect-data plugins \
    --paths . hermes_cli/main.py
$ ./dist/hermes-agent-cn-runtime-win32-x64/hermes-agent-cn-runtime-win32-x64.exe dashboard --help
$ # zip + sign manually using scripts/sign_runtime_manifest.py
```

For a macOS dry run after PyInstaller has produced `dist/hermes-agent-cn-runtime-darwin-arm64`:

```bash
$ python scripts/normalize_macos_pyinstaller_runtime.py dist/hermes-agent-cn-runtime-darwin-arm64
$ APPLE_SIGNING_IDENTITY="Developer ID Application: ..." \
    scripts/sign_macos_runtime_payload.sh dist/hermes-agent-cn-runtime-darwin-arm64
$ (cd dist && zip -r -y ../out/hermes-agent-cn-runtime-darwin-arm64.zip hermes-agent-cn-runtime-darwin-arm64)
```

## Known gaps

* **Dashboard deps are bundled**: runtime artifacts must install `.[web]` and
  collect FastAPI/Uvicorn submodules so the frozen binary never lazy-installs
  `fastapi` or `uvicorn` on the user's machine.
* **Backends are bundled via the `cn-desktop` extra**: the runtime installs
  `.[cn-desktop]`, an aggregate extra that pre-bakes every backend the desktop
  exposes — dashboard (`web`), Anthropic transport, the native MCP client
  (`mcp`), and the 飞书 / 钉钉 / 企业微信 / 微信 IM adapters. The frozen
  PyInstaller binary cannot lazy-install via `tools/lazy_deps.py` (no working
  pip), so anything not in `cn-desktop` is unavailable at runtime, and a
  `pip install <pkg>` on the host does not help (the frozen runtime uses its own
  bundled interpreter + packages). This is why a build that installed only
  `.[web,anthropic]` shipped without the MCP SDK (`_MCP_AVAILABLE=False`,
  `discover_mcp_tools()` silently registered nothing — issue #16) and without
  `lark-oapi` (Feishu adapter degraded to "unavailable"). When adding a new
  desktop backend, add it to the `cn-desktop` extra **and** to the
  `--collect-submodules` list + the "Verify frozen runtime backends" assert in
  `release-runtime.yml`; the verify step fails the build if any bundled
  package's `dist-info` is missing from the frozen output.
* **`alibabacloud_*` collection is fragile**: the DingTalk SDK pulls a chain of
  small namespace packages (`alibabacloud_dingtalk`, `alibabacloud_tea_openapi`,
  `alibabacloud_tea_util`, `alibabacloud_credentials`, `alibabacloud_tea`, …),
  all pure-Python sdists. They are explicitly collected, but the first release
  that bundles DingTalk should be smoke-tested against a live bot to confirm no
  submodule was missed.
* **Lazy provider deps** (`anthropic`, `firecrawl-py`, `exa-py`, ...) are
  not bundled. `tools/lazy_deps.py` can't install at runtime inside a
  PyInstaller-frozen binary, so only providers we explicitly pre-bake
  are available. Add to the workflow's `--hidden-import` list as
  needed.
* **Code signing**: macOS runtime payloads are Developer ID signed in CI before
  they are zipped; the Desktop workflow notarizes the complete macOS app that
  embeds them. Independent runtime ZIPs are not separately notarized by this
  workflow. Windows Authenticode is optional and is not a v0.9.0 release gate.
  The project-owned Ed25519 manifest signature and archive hash are mandatory
  on every platform; these signatures do not establish Windows publisher trust.
* **Cross-arch builds**: x64-only for Linux today. Add arm64 matrix
  entry once we have a runner.
