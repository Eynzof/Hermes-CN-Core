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

Pinning to a specific tag works too:

```
https://github.com/Eynzof/hermes-agent-cn/releases/download/runtime-v0.14.0-cn.1/stable-win32-x64.json
```

The canonical versioning contract is documented in `docs/RUNTIME_VERSIONING.md`.
The manifest schema (see `src/process/runtime.rs::RuntimeUpdateManifest`
on the desktop side) is schema v3 — v2 plus a required, signed
`minAppVersion` (desktop >= 0.7.0 accepts both; `--schema 2` exists for
transition re-issues to older clients):

```json
{
  "schemaVersion": 3,
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
  "minAppVersion": "0.7.0",
  "createdAt": "2026-05-16T03:00:00Z"
}
```

The signature is over the canonical fields concatenated with `\n` in this
exact order — twelve fields for v2, plus `minAppVersion` as field #13 for v3:

```
schemaVersion\nchannel\nruntimeVersion\nkernelVersion\nruntimeFlavor\nruntimeRevision\nplatform\narch\nartifactUrl\nsha256\nsourceRepo\nsourceCommit[\nminAppVersion]
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
3. The `release-runtime` workflow validates the tag against `pyproject.toml` and runs once per platform (Windows / macOS-arm64 / Linux-x64).
4. Each job:
   - Builds a self-contained executable via PyInstaller
   - On macOS, normalizes PyInstaller-collected `.framework` directories back
     into standard symlink framework layouts
   - On macOS, signs the full runtime payload with Developer ID before packaging
   - Smoke-tests it (`dashboard --help` must exit 0)
   - Zips the dist directory as `hermes-agent-cn-runtime-<platform>-<arch>.zip`
     and preserves symlinks for macOS artifacts
   - Signs the manifest with `scripts/sign_runtime_manifest.py`
5. The aggregate `release` job downloads all artifacts and publishes
   them to a GitHub Release named `runtime-v0.14.0-cn.1`.

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
  they are zipped. PyInstaller-produced Windows `.exe` files are still often
  flagged by SmartScreen until signed with an Authenticode cert. Register one
  and add the Windows signing step to the workflow.
* **Cross-arch builds**: x64-only for Linux today. Add arm64 matrix
  entry once we have a runner.
