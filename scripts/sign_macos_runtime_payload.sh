#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${1:?usage: scripts/sign_macos_runtime_payload.sh <runtime-dir>}"
runtime_dir="${runtime_dir%/}"
identity="${APPLE_SIGNING_IDENTITY:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
entitlements_file="$script_dir/macos-runtime.entitlements.plist"
node_entitlements="$script_dir/macos-node.entitlements.plist"

if [[ ! -d "$runtime_dir" ]]; then
  echo "macOS runtime payload not found: $runtime_dir" >&2
  exit 1
fi

if [[ ! -f "$entitlements_file" ]]; then
  echo "macOS runtime entitlements not found: $entitlements_file" >&2
  exit 1
fi

if [[ -z "$identity" ]]; then
  identity="$(
    security find-identity -v -p codesigning 2>/dev/null \
      | sed -n 's/.*"\(Developer ID Application:[^"]*\)".*/\1/p' \
      | head -n 1
  )"
fi

if [[ -z "$identity" ]]; then
  echo "APPLE_SIGNING_IDENTITY is empty and no Developer ID Application identity was found" >&2
  exit 1
fi

sign_args=(--force --sign "$identity")
if [[ "$identity" != "-" ]]; then
  sign_args+=(--timestamp --options runtime)
fi

is_macho() {
  file "$1" | grep -q 'Mach-O'
}

inside_framework() {
  [[ "$1" == *".framework/"* ]]
}

find_main_runtime_binary() {
  local basename_candidate="$runtime_dir/$(basename "$runtime_dir")"
  if [[ -f "$basename_candidate" ]] && is_macho "$basename_candidate"; then
    printf '%s\n' "$basename_candidate"
    return 0
  fi

  local candidate
  for candidate in "$runtime_dir"/hermes-agent-cn-runtime-darwin-*; do
    if [[ -f "$candidate" ]] && is_macho "$candidate"; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done

  return 1
}

main_binary="$(find_main_runtime_binary || true)"
if [[ -z "$main_binary" ]]; then
  echo "macOS runtime main executable not found under: $runtime_dir" >&2
  exit 1
fi
node_binary="$runtime_dir/node/bin/node"
if [[ ! -f "$node_binary" || ! -x "$node_binary" || -L "$node_binary" ]] || ! is_macho "$node_binary"; then
  echo "Missing bundled Node executable: $node_binary" >&2
  exit 1
fi

sign_path() {
  local path="$1"
  local path_sign_args=("${sign_args[@]}")
  if [[ "$path" == "$main_binary" ]]; then
    # Python 3.14's _ctypes uses libffi closures. On macOS 13, a hardened
    # runtime without this entitlement can spin in ffi_closure_alloc before
    # argparse reaches even `dashboard --help`. Entitlements belong on the
    # loading executable; bundled dylibs/frameworks should not receive them.
    path_sign_args+=(--entitlements "$entitlements_file")
  elif [[ "$path" == "$node_binary" ]]; then
    # Exercise V8 under the hardened runtime with a local diagnostic identity.
    if [[ "$identity" == "-" ]]; then
      path_sign_args+=(--options runtime)
    fi
    path_sign_args+=(--entitlements "$node_entitlements")
  fi
  codesign "${path_sign_args[@]}" "$path"
}

verify_path() {
  local path="$1"
  codesign --verify --strict --verbose=2 "$path"
  local actual expected
  actual="$(codesign -d --entitlements - --xml "$path" 2>/dev/null)"
  if [[ "$path" == "$node_binary" ]]; then
    expected="$node_entitlements"
  elif [[ "$path" == "$main_binary" ]]; then
    expected="$entitlements_file"
  elif [[ -z "$actual" ]]; then
    return
  else
    echo "Unexpected entitlements on $path" >&2
    return 1
  fi
  python3 -c '
import plistlib, sys
from pathlib import Path
actual = sys.stdin.buffer.read().strip()
if not actual or plistlib.loads(actual) != plistlib.loads(Path(sys.argv[1]).read_bytes()):
    sys.exit(f"Incorrect entitlements on {sys.argv[2]}")
' "$expected" "$path" <<< "$actual"
}

echo "Signing macOS runtime payload with identity: $identity"
echo "Runtime payload: $runtime_dir"
echo "Runtime main executable: $main_binary"
echo "Entitlements: $entitlements_file"

macho_count=0
while IFS= read -r -d '' path; do
  if inside_framework "$path"; then
    continue
  fi
  if is_macho "$path"; then
    sign_path "$path"
    macho_count=$((macho_count + 1))
  fi
done < <(find "$runtime_dir" -type f -print0)

echo "Signed $macho_count non-framework Mach-O files."

framework_count=0
while IFS= read -r -d '' framework; do
  sign_path "$framework"
  framework_count=$((framework_count + 1))
done < <(find "$runtime_dir" -type d -name '*.framework' -print0 | sort -z -r)

echo "Signed $framework_count framework bundles."

while IFS= read -r -d '' framework; do
  verify_path "$framework"
done < <(find "$runtime_dir" -type d -name '*.framework' -print0 | sort -z)

while IFS= read -r -d '' path; do
  if is_macho "$path"; then
    verify_path "$path"
  fi
done < <(find "$runtime_dir" -type f -print0)

# --version does not start V8. Exercise the signed executable and its launchers.
"$node_binary" <<'JS'
const assert = require('node:assert/strict')
const crypto = require('node:crypto')
let count = 0
for (let i = 0; i < 1_000_000; i++) count += 1
assert.equal(count, 1_000_000)
assert.match('Hermes-42', /^Hermes-\d+$/)
assert.equal(Buffer.from('48', 'hex').toString(), 'H')
assert.equal(crypto.createHash('sha256').update('abc').digest('hex'),
  'ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad')
// A WebAssembly function returning i32.const 42.
const wasm = Buffer.from('0061736d010000000105016000017f030201000707010372756e00000a06010400412a0b', 'hex')
assert.equal(new WebAssembly.Instance(new WebAssembly.Module(wasm)).exports.run(), 42)
JS
node_bin_dir="$(cd "$runtime_dir/node/bin" && pwd)"
PATH="$node_bin_dir" "$node_bin_dir/npm" --version
PATH="$node_bin_dir" "$node_bin_dir/npx" --version

echo "macOS runtime payload signing verification passed."
