#!/usr/bin/env python3
r"""Sign a hermes-agent-cn runtime manifest with Ed25519.

The hermes-agent-cn-desktop client verifies the signature against the public key
it was built with. The canonical payload concatenated with ``\n`` matches what
the Rust side reconstructs in ``signature_payload()`` — keep the field order in
sync.

Runtime versions are schema v2 and follow ``<kernelVersion>-cn.<revision>``.
For example, tag ``runtime-v0.14.0-cn.1`` produces manifest
``runtimeVersion=0.14.0-cn.1``, ``kernelVersion=0.14.0``,
``runtimeFlavor=cn``, and ``runtimeRevision=1``.

Usage:
    python scripts/sign_runtime_manifest.py \
        --channel stable \
        --runtime-version 0.14.0-cn.1 \
        --kernel-version 0.14.0 \
        --runtime-flavor cn \
        --runtime-revision 1 \
        --platform win32 \
        --arch x64 \
        --artifact-url https://github.com/.../hermes-agent-cn-runtime-win32-x64.zip \
        --artifact-path dist/hermes-agent-cn-runtime-win32-x64.zip \
        --source-repo Eynzof/hermes-agent-cn \
        --source-commit "$GITHUB_SHA" \
        --min-app-version 0.1.0 \
        --output dist/stable-win32-x64.json
"""

from __future__ import annotations

import argparse
import base64
import datetime as _dt
import hashlib
import orjson
import os
import re
import sys
from pathlib import Path

try:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    from cryptography.hazmat.primitives.serialization import load_pem_private_key
except ImportError:
    raise SystemExit(
        "scripts/sign_runtime_manifest.py needs `cryptography` "
        "(pip install cryptography)."
    )


SCHEMA_VERSION = 2

# Field order MUST match `signature_payload()` in
# hermes-agent-cn-desktop/src/process/runtime.rs. Any reorder here is a
# silent verification failure on every desktop install — change both
# sides together or not at all.
_PAYLOAD_FIELDS = (
    "schemaVersion",
    "channel",
    "runtimeVersion",
    "kernelVersion",
    "runtimeFlavor",
    "runtimeRevision",
    "platform",
    "arch",
    "artifactUrl",
    "sha256",
    "sourceRepo",
    "sourceCommit",
)
_RUNTIME_VERSION_RE = re.compile(
    r"^(?P<kernel>\d+\.\d+\.\d+(?:[.-][0-9A-Za-z.-]+)?)-"
    r"(?P<flavor>[a-z][a-z0-9]*)\.(?P<revision>[1-9]\d*)$"
)


def _sha256_hex(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_private_key() -> Ed25519PrivateKey:
    pem = os.environ.get("RUNTIME_SIGN_PRIVATE_KEY_PEM")
    if not pem:
        raise SystemExit(
            "RUNTIME_SIGN_PRIVATE_KEY_PEM is not set. In GitHub Actions, wire "
            "the repository secret to the workflow env block; locally, "
            "export it from your encrypted key store. Never put the key on "
            "argv (it'd leak via process listings)."
        )
    # Unwrap "\n" → newline so secrets pasted as one-liners work.
    pem = pem.replace("\\n", "\n").encode()
    key = load_pem_private_key(pem, password=None)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("RUNTIME_SIGN_PRIVATE_KEY_PEM is not an Ed25519 key.")
    return key


def _validate_runtime_version(
    runtime_version: str,
    kernel_version: str,
    flavor: str,
    revision: int,
) -> None:
    match = _RUNTIME_VERSION_RE.match(runtime_version)
    if not match:
        raise SystemExit(
            "runtime_version must look like <kernelVersion>-<flavor>.<revision>, "
            f"got {runtime_version!r}"
        )
    if match.group("kernel") != kernel_version:
        raise SystemExit(
            f"runtime_version kernel {match.group('kernel')!r} does not match "
            f"--kernel-version {kernel_version!r}"
        )
    if match.group("flavor") != flavor:
        raise SystemExit(
            f"runtime_version flavor {match.group('flavor')!r} does not match "
            f"--runtime-flavor {flavor!r}"
        )
    if int(match.group("revision")) != revision:
        raise SystemExit(
            f"runtime_version revision {match.group('revision')!r} does not match "
            f"--runtime-revision {revision!r}"
        )


def main() -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--channel", required=True, help="stable | beta | canary | ...")
    p.add_argument(
        "--runtime-version",
        required=True,
        help="Full runtime identity, e.g. 0.14.0-cn.1",
    )
    p.add_argument(
        "--kernel-version",
        required=True,
        help="Hermes Agent kernel/package version, e.g. 0.14.0",
    )
    p.add_argument("--runtime-flavor", required=True, help="Runtime flavor, e.g. cn")
    p.add_argument(
        "--runtime-revision",
        required=True,
        type=int,
        help="Positive runtime revision for this kernel",
    )
    p.add_argument("--platform", required=True, choices=("win32", "darwin", "linux"))
    p.add_argument("--arch", required=True, choices=("x64", "arm64"))
    p.add_argument("--artifact-url", required=True, help="HTTPS URL clients fetch")
    p.add_argument(
        "--artifact-path",
        required=True,
        type=Path,
        help="Local path to the zip — used to compute sha256",
    )
    p.add_argument("--source-repo", required=True, help="org/name slug")
    p.add_argument("--source-commit", required=True, help="commit SHA")
    p.add_argument("--min-app-version", default=None, help="desktop client floor")
    p.add_argument("--output", required=True, type=Path)
    args = p.parse_args()

    if args.runtime_revision < 1:
        raise SystemExit("--runtime-revision must be >= 1")
    _validate_runtime_version(
        args.runtime_version,
        args.kernel_version,
        args.runtime_flavor,
        args.runtime_revision,
    )

    if not args.artifact_path.is_file():
        raise SystemExit(f"artifact zip not found: {args.artifact_path}")

    if not args.artifact_url.startswith("https://"):
        # Rust side rejects non-https; fail fast here so CI doesn't ship
        # a manifest the client will refuse.
        raise SystemExit(f"artifact_url must be https:, got {args.artifact_url!r}")

    sha256 = _sha256_hex(args.artifact_path)
    print(f"sha256({args.artifact_path.name}) = {sha256}", file=sys.stderr)

    manifest = {
        "schemaVersion": SCHEMA_VERSION,
        "channel": args.channel,
        "runtimeVersion": args.runtime_version,
        "kernelVersion": args.kernel_version,
        "runtimeFlavor": args.runtime_flavor,
        "runtimeRevision": args.runtime_revision,
        "platform": args.platform,
        "arch": args.arch,
        "artifactUrl": args.artifact_url,
        "sha256": sha256,
        "sourceRepo": args.source_repo,
        "sourceCommit": args.source_commit,
    }
    if args.min_app_version:
        manifest["minAppVersion"] = args.min_app_version
    manifest["createdAt"] = (
        _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )

    payload = "\n".join(str(manifest[f]) for f in _PAYLOAD_FIELDS).encode()
    key = _load_private_key()
    signature = key.sign(payload)
    manifest["signature"] = base64.standard_b64encode(signature).decode()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(orjson.dumps(manifest, option=orjson.OPT_INDENT_2).decode('utf-8') + "\n", encoding="utf-8")
    print(f"wrote {args.output}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
