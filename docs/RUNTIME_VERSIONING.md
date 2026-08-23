# Runtime versioning

Hermes Agent CN desktop runtime releases use a two-part version so the desktop
can distinguish the upstream Hermes Agent kernel from the CN runtime package we
ship around it.

## Version shape

Runtime releases are tagged and published as:

```text
runtime-v<kernelVersion>-cn.<runtimeRevision>
```

Examples:

```text
runtime-v0.14.0-cn.1
runtime-v0.14.0-cn.2
runtime-v0.15.0-cn.1
```

The parts mean:

- `kernelVersion`: the Hermes Agent package version from `pyproject.toml`.
  This tracks the upstream-compatible agent kernel version, for example
  `0.14.0`.
- `runtimeFlavor`: the package flavor. This fork uses `cn`.
- `runtimeRevision`: the CN runtime package revision for that kernel version.
  It starts at `1` for each new `kernelVersion` and increments when we rebuild
  or patch the packaged runtime without changing the Hermes Agent kernel
  version.
- `runtimeVersion`: the full install/update identity:
  `<kernelVersion>-cn.<runtimeRevision>`.

When the kernel stays at `0.14.0` but we add PyInstaller hidden imports, fix a
packaging bug, rotate runtime assets, or patch desktop-specific integration, we
ship `0.14.0-cn.2`, `0.14.0-cn.3`, and so on. When the kernel moves to
`0.15.0`, the CN revision resets to `cn.1`.

## Manifest schema (v3, with v2 transition support)

Every release asset set includes one manifest per platform at:

```text
https://github.com/Eynzof/hermes-agent-cn/releases/latest/download/stable-<platform>-<arch>.json
```

The manifest is intentionally flat so GitHub Releases can host it directly.
Current schema is **v3**: identical to v2 plus a **required, signed**
`minAppVersion` field (the desktop forced-upgrade gate). Desktop >= 0.7.0
accepts both v2 and v3; older desktops only accept v2, which is why v3
manifests must not ship to the stable channel until 0.7.0 has rolled out
(`--schema 2` on the signer exists for transition re-issues). Anything
outside {2, 3} is rejected by the desktop.

```json
{
  "schemaVersion": 3,
  "channel": "stable",
  "runtimeVersion": "0.14.0-cn.1",
  "kernelVersion": "0.14.0",
  "runtimeFlavor": "cn",
  "runtimeRevision": 1,
  "platform": "darwin",
  "arch": "arm64",
  "artifactUrl": "https://github.com/Eynzof/hermes-agent-cn/releases/download/runtime-v0.14.0-cn.1/hermes-agent-cn-runtime-darwin-arm64.zip",
  "sha256": "...",
  "signature": "...",
  "sourceRepo": "Eynzof/hermes-agent-cn",
  "sourceCommit": "...",
  "minAppVersion": "0.7.0",
  "createdAt": "2026-05-19T00:00:00Z"
}
```

## Signature payload

The Ed25519 signature covers the following fields, concatenated with `\n` in
this exact order:

```text
schemaVersion
channel
runtimeVersion
kernelVersion
runtimeFlavor
runtimeRevision
platform
arch
artifactUrl
sha256
sourceRepo
sourceCommit
minAppVersion        # v3 only — appended as field #13; absent from v2 payloads
```

Any change to that order must be made in both
`scripts/sign_runtime_manifest.py` and
`hermes-agent-cn-desktop/src/process/runtime.rs` in the same change (both
sides carry order-lock tests over the same fixture values).

## Cutting a runtime release

1. Make sure `[project].version` in `pyproject.toml` is the kernel version you
   intend to package.
2. Pick the next CN revision for that kernel version.
3. Create and push a tag:

   ```bash
   git tag runtime-v0.14.0-cn.1
   git push origin runtime-v0.14.0-cn.1
   ```

4. The `release-runtime` workflow validates that the tag's `kernelVersion`
   matches `pyproject.toml`, builds the platform runtimes, normalizes and
   Developer ID signs the macOS payload, writes schema v2 manifests, signs
   them, and publishes all assets to the GitHub Release.

Do not reuse a tag for a rebuilt runtime. Publish a new `cn.N` revision instead.
