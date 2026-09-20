#!/usr/bin/env bash
# cryptography 50 has no Intel macOS wheel. Supply a static, macOS 14 build
# instead of letting its source build link the runner's Homebrew OpenSSL.
set -euo pipefail
output_dir="${1:?usage: build_macos_intel_openssl.sh <openssl-prefix>}"
test "$(uname -s)" = Darwin
version=3.5.8
source_sha256=a8f84a39918ec6415ce765d9b429d313ba97b8143169c172e734b9514464f5b2
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
build_root="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hermes-openssl.XXXXXX")"
archive="$build_root/openssl.tar.gz"
curl --fail --location --output "$archive" "https://github.com/openssl/openssl/releases/download/openssl-$version/openssl-$version.tar.gz"
test "$(shasum -a 256 "$archive" | cut -d ' ' -f 1)" = "$source_sha256"
tar -xzf "$archive" -C "$build_root"
cd "$build_root/openssl-$version"
export MACOSX_DEPLOYMENT_TARGET=14.0
# no-module embeds the legacy provider in libcrypto; it does not remove it.
# Do not add no-legacy: existing imported keys/ciphers still need that provider.
./Configure darwin64-x86_64-cc no-shared no-module no-tests \
  --prefix="$output_dir" --openssldir="$output_dir/ssl" --libdir=lib \
  -mmacosx-version-min=14.0
make -j3
make install_sw install_ssldirs
"$output_dir/bin/openssl" version
"$output_dir/bin/openssl" list -providers -provider default -provider legacy > "$output_dir/providers.txt"
grep -F 'OpenSSL Legacy Provider' "$output_dir/providers.txt"
test -f "$output_dir/lib/libcrypto.a"
test -f "$output_dir/lib/libssl.a"
python3 - "$output_dir" "$version" "$source_sha256" <<'PY'
import json, sys
from pathlib import Path
record = {"version": sys.argv[2], "sourceSha256": sys.argv[3], "platform": "darwin-x64",
          "deploymentTarget": "14.0", "linkage": "static", "legacyProvider": "built-in"}
(Path(sys.argv[1]) / "build-record.json").write_text(json.dumps(record, indent=2) + "\n")
PY
