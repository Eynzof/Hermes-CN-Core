#!/usr/bin/env bash
set -euo pipefail

runtime_dir="${1:?usage: scripts/sign_macos_runtime_payload.sh <runtime-dir>}"
identity="${APPLE_SIGNING_IDENTITY:-}"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
entitlements_file="${script_dir}/hermes-runtime.entitlements"

if [[ ! -d "$runtime_dir" ]]; then
  echo "macOS runtime payload not found: $runtime_dir" >&2
  exit 1
fi

if [[ ! -f "$entitlements_file" ]]; then
  echo "Runtime entitlements file not found: $entitlements_file" >&2
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

sign_args=(--force --sign "$identity" --entitlements "$entitlements_file")
if [[ "$identity" != "-" ]]; then
  sign_args+=(--timestamp --options runtime)
fi

is_macho() {
  file "$1" | grep -q 'Mach-O'
}

inside_framework() {
  [[ "$1" == *".framework/"* ]]
}

sign_path() {
  local path="$1"
  codesign "${sign_args[@]}" "$path"
}

verify_path() {
  local path="$1"
  codesign --verify --strict --verbose=2 "$path"
}

echo "Signing macOS runtime payload with identity: $identity"
echo "Runtime payload: $runtime_dir"
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

# Verify the main runtime binary actually carries the critical entitlement.
# This catches a codesign invocation that silently dropped the entitlements
# plist (e.g. mismatched identity/options), which would leave Python 3.14's
# libffi stuck in an mmap(PROT_EXEC) retry loop under Hardened Runtime.
main_binary=""
for candidate in "$runtime_dir"/hermes-agent-cn-runtime-darwin-*; do
  if [[ -f "$candidate" ]] && is_macho "$candidate"; then
    main_binary="$candidate"
    break
  fi
done

if [[ -n "$main_binary" ]]; then
  echo "Verifying entitlements on main runtime binary: $main_binary"
  if ! codesign -d --entitlements - "$main_binary" 2>/dev/null \
       | grep -q 'com.apple.security.cs.allow-unsigned-executable-memory'; then
    echo "ERROR: Main runtime binary is missing the allow-unsigned-executable-memory entitlement" >&2
    exit 1
  fi
  echo "Main runtime binary entitlements OK."
else
  echo "WARNING: Could not locate main runtime binary to verify entitlements" >&2
fi

echo "macOS runtime payload signing verification passed."
