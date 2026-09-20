#!/usr/bin/env bash
# PyPI has no macOS x86_64 CPython 3.14 wheel. Build the locked version from
# Microsoft's exact release commit; never substitute an older Python or omit STT.
set -euo pipefail

python_bin="${1:?usage: build_macos_intel_onnxruntime.sh <python> <wheel-output-dir>}"
output_dir="${2:?wheel output directory required}"
source_commit="8f0278c77bf44b0cc83c098c6c722b92a36ac4b5" # microsoft/onnxruntime v1.27.0
deployment_target="14.0"

"$python_bin" - <<'PY'
import platform, sys
assert sys.platform == "darwin" and platform.machine() == "x86_64", "Intel macOS only"
assert sys.version_info[:2] == (3, 14), "CPython 3.14 is required"
PY
command -v cmake >/dev/null
command -v ninja >/dev/null
mkdir -p "$output_dir"
output_dir="$(cd "$output_dir" && pwd)"
build_root="$(mktemp -d "${RUNNER_TEMP:-${TMPDIR:-/tmp}}/hermes-onnxruntime.XXXXXX")"
source_dir="$build_root/source"

git init -q "$source_dir"
git -C "$source_dir" remote add origin https://github.com/microsoft/onnxruntime.git
git -C "$source_dir" fetch --depth 1 origin "$source_commit"
git -C "$source_dir" checkout --detach FETCH_HEAD
test "$(git -C "$source_dir" rev-parse HEAD)" = "$source_commit"
# Native CPU Python builds need ONNX; Emscripten and fuzzing submodules do not.
git -C "$source_dir" submodule update --init --recursive --depth 1 cmake/external/onnx
test "$(cat "$source_dir/VERSION_NUMBER")" = "1.27.0"

# --apple_deploy_target alone only applies to framework builds in ORT.
# Set the ordinary CMake wheel target explicitly as well. A successful build
# does not replace validation on the oldest supported operating system.
export MACOSX_DEPLOYMENT_TARGET="$deployment_target"
"$python_bin" "$source_dir/tools/ci_build/build.py" \
  --build_dir "$build_root/build" \
  --config Release --update --build --build_wheel \
  --parallel 3 --skip_tests --skip_submodule_sync \
  --cmake_generator Ninja --compile_no_warning_as_error \
  --apple_deploy_target "$deployment_target" \
  --cmake_extra_defines \
    CMAKE_OSX_ARCHITECTURES=x86_64 \
    "CMAKE_OSX_DEPLOYMENT_TARGET=$deployment_target" \
    onnxruntime_BUILD_UNIT_TESTS=OFF

"$python_bin" - "$build_root/build/Release/dist" "$output_dir" "$source_commit" "$deployment_target" <<'PY'
import hashlib, json, platform, shutil, sys
from pathlib import Path
source, output = Path(sys.argv[1]), Path(sys.argv[2])
wheels = list(source.glob("onnxruntime-1.27.0-cp314-cp314-macosx_*_x86_64.whl"))
assert len(wheels) == 1, f"Expected one CPython 3.14 Intel wheel, found {wheels}"
wheel = output / wheels[0].name
shutil.copy2(wheels[0], wheel)
with wheel.open("rb") as stream:
    digest = hashlib.file_digest(stream, "sha256").hexdigest()
record = {
    "sourceRepository": "microsoft/onnxruntime", "sourceCommit": sys.argv[3],
    "version": "1.27.0", "pythonVersion": platform.python_version(),
    "requestedDeploymentTarget": sys.argv[4], "wheel": wheel.name, "sha256": digest,
}
(output / "build-record.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
print(json.dumps(record, indent=2))
PY
