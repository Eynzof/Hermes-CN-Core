# Runtime release pipeline

The hermes-cn-desktop-v2 client (Tauri desktop app) downloads a
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
https://github.com/Eynzof/hermes-agent-cn/releases/download/runtime-v0.13.0/stable-win32-x64.json
```

The manifest schema (see `src/process/runtime.rs::RuntimeUpdateManifest`
on the desktop side):

```json
{
  "channel": "stable",
  "version": "0.13.0+cn.20260516",
  "platform": "win32",
  "arch": "x64",
  "artifactUrl": "https://.../runtime-win32-x64.zip",
  "sha256": "abcdef0123...",
  "signature": "base64-encoded Ed25519 signature",
  "upstreamRepo": "Eynzof/hermes-agent-cn",
  "upstreamCommit": "01edd139...",
  "minAppVersion": "0.1.0",
  "createdAt": "2026-05-16T03:00:00Z"
}
```

The signature is over the eight canonical fields concatenated with `\n`
in this exact order:

```
channel\nplatform\narch\nversion\nartifactUrl\nsha256\nupstreamRepo\nupstreamCommit
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
  hermes-cn-desktop-v2 release workflow

Cut a new desktop release at the same time — older desktop builds carry
the old key and will reject anything signed by the new one.

## Cutting a release

1. Tag the commit you want to ship:
   ```
   git tag runtime-v0.13.0
   git push origin runtime-v0.13.0
   ```
2. The `release-runtime` workflow runs once per platform (Windows / macOS-arm64 / Linux-x64).
3. Each job:
   - Builds a self-contained executable via PyInstaller
   - Smoke-tests it (`dashboard --help` must exit 0)
   - Zips the dist directory as `hermes-agent-cn-runtime-<platform>-<arch>.zip`
   - Signs the manifest with `scripts/sign_runtime_manifest.py`
4. The aggregate `release` job downloads all artifacts and publishes
   them to a GitHub Release named `runtime-v0.13.0`.

Once the release exists, every hermes-cn-desktop-v2 install whose
manifest URL points at this base URL will pick up the update on next
launch (or via the in-app "check for updates" flow).

## Manual dry run

```
$ pip install -e ".[web]"
$ pip install pyinstaller cryptography
$ pyinstaller --noconfirm --name hermes-agent-cn-runtime-win32-x64 \
    --onedir --console \
    --collect-submodules hermes_cli --collect-submodules tui_gateway \
    --collect-submodules fastapi --collect-submodules starlette \
    --collect-submodules uvicorn --collect-submodules pydantic \
    --collect-data hermes_cli --collect-data gateway --collect-data plugins \
    --paths . hermes_cli/main.py
$ ./dist/hermes-agent-cn-runtime-win32-x64/hermes-agent-cn-runtime-win32-x64.exe dashboard --help
$ # zip + sign manually using scripts/sign_runtime_manifest.py
```

## Known gaps

* **Dashboard deps are bundled**: runtime artifacts must install `.[web]` and
  collect FastAPI/Uvicorn submodules so the frozen binary never lazy-installs
  `fastapi` or `uvicorn` on the user's machine.
* **Lazy provider deps** (`anthropic`, `firecrawl-py`, `exa-py`, ...) are
  not bundled. `tools/lazy_deps.py` can't install at runtime inside a
  PyInstaller-frozen binary, so only providers we explicitly pre-bake
  are available. Add to the workflow's `--hidden-import` list as
  needed.
* **Code signing**: PyInstaller-produced Windows .exe is often flagged
  by SmartScreen until signed with an Authenticode cert. Register one
  and add the signing step to the workflow.
* **Cross-arch builds**: x64-only for Linux today. Add arm64 matrix
  entry once we have a runner.
