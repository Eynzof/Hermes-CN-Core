#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${1:?usage: scripts/sign_macos_runtime_payload.sh <runtime-dir>}"
runtime_dir="${runtime_dir%/}"
identity="${APPLE_SIGNING_IDENTITY:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
entitlements_file="$script_dir/macos-runtime.entitlements.plist"

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

sign_path() {
  local path="$1"
  local path_sign_args=("${sign_args[@]}")
  if [[ "$path" == "$main_binary" ]]; then
    # Python 3.14's _ctypes uses libffi closures. On macOS 13, a hardened
    # runtime without this entitlement can spin in ffi_closure_alloc before
    # argparse reaches even `dashboard --help`. Entitlements belong on the
    # loading executable; bundled dylibs/frameworks should not receive them.
    path_sign_args+=(--entitlements "$entitlements_file")
  fi
  codesign "${path_sign_args[@]}" "$path"
}

verify_path() {
  local path="$1"
  codesign --verify --strict --verbose=2 "$path"
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

echo "Verifying entitlements on main runtime binary: $main_binary"
if ! codesign -d --entitlements - "$main_binary" 2>/dev/null \
     | grep -q 'com.apple.security.cs.allow-unsigned-executable-memory'; then
  echo "ERROR: Main runtime binary is missing the allow-unsigned-executable-memory entitlement" >&2
  exit 1
fi
echo "Main runtime binary entitlements OK."

echo "macOS runtime payload signing verification passed."
