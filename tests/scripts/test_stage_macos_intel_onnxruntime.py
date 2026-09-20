"""Exercise wheel staging independently of the long ONNX Runtime compilation."""
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest


@pytest.mark.skipif(sys.platform != "darwin" or not shutil.which("clang"), reason="needs Apple Mach-O tooling")
@pytest.mark.parametrize("architecture,metadata_arch,accepted", [
    ("x86_64", "x86_64", True),
    ("x86_64", "universal2", False),
    ("arm64", "x86_64", False),
])
def test_staging_checks_wheel_metadata_and_actual_architecture(tmp_path, architecture, metadata_arch, accepted):
    native = tmp_path / "runtime.dylib"
    subprocess.run(
        ["clang", "-x", "c", "-dynamiclib", "-arch", architecture,
         "-mmacosx-version-min=14.0", "-o", str(native), "-"],
        input="int ort_fixture(void) { return 1; }\n", text=True, check=True, capture_output=True,
    )
    source, output = tmp_path / "source", tmp_path / "output"
    source.mkdir()
    output.mkdir()
    name = "onnxruntime-1.27.0-cp314-cp314-macosx_14_0_x86_64.whl"
    with zipfile.ZipFile(source / name, "w") as archive:
        archive.writestr("onnxruntime-1.27.0.dist-info/WHEEL", f"Tag: cp314-cp314-macosx_14_0_{metadata_arch}\n")
        archive.write(native, "onnxruntime/capi/libonnxruntime.1.27.0.dylib")
    script = Path(__file__).resolve().parents[2] / "scripts/build_macos_intel_onnxruntime.sh"
    collector = script.read_text().rsplit("<<'PY'\n", 1)[1].split("\nPY", 1)[0]
    result = subprocess.run(
        [sys.executable, "-", str(source), str(output), "source-commit", "14.0"],
        input=collector, capture_output=True, text=True,
    )
    assert (result.returncode == 0) == accepted, result.stdout + result.stderr
    if accepted:
        record = json.loads((output / "build-record.json").read_text())
        assert record["verifiedMachOArchitecture"] == "x86_64"
        assert record["wheelTag"] == "cp314-cp314-macosx_14_0_x86_64"
        assert (output / name).read_bytes() == (source / name).read_bytes()
    else:
        assert not (output / "build-record.json").exists()
