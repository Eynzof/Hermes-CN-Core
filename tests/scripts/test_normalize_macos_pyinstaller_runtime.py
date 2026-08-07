"""Tests for macOS PyInstaller runtime framework normalization."""
from __future__ import annotations


import importlib.util
import os
from pathlib import Path


_SCRIPT_PATH = (
    Path(__file__).resolve().parents[2] / "scripts" / "normalize_macos_pyinstaller_runtime.py"
)
_SPEC = importlib.util.spec_from_file_location("normalize_macos_pyinstaller_runtime", _SCRIPT_PATH)
assert _SPEC is not None
_module = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(_module)


def test_normalize_runtime_replaces_copied_python_framework_layout(tmp_path: Path):
    runtime = tmp_path / "runtime"
    framework = runtime / "_internal" / "Python.framework"
    version = framework / "Versions" / "3.11"
    copied_current = framework / "Versions" / "Current"

    (framework / "Resources").mkdir(parents=True)
    (framework / "Python").write_bytes(b"top-level duplicate")
    (framework / "Resources" / "Info.plist").write_bytes(b"top-level plist duplicate")
    (version / "Resources").mkdir(parents=True)
    (version / "Python").write_bytes(b"real python dylib")
    (version / "Resources" / "Info.plist").write_bytes(b"real plist")
    (copied_current / "Resources").mkdir(parents=True)
    (copied_current / "Python").write_bytes(b"current duplicate")
    (copied_current / "Resources" / "Info.plist").write_bytes(b"current plist duplicate")

    assert _module.normalize_runtime(runtime) == 1

    assert (framework / "Python").is_symlink()
    assert os.readlink(framework / "Python") == "Versions/Current/Python"
    assert (framework / "Resources").is_symlink()
    assert os.readlink(framework / "Resources") == "Versions/Current/Resources"
    assert (framework / "Versions" / "Current").is_symlink()
    assert os.readlink(framework / "Versions" / "Current") == "3.11"
    assert (framework / "Versions" / "3.11" / "Python").read_bytes() == b"real python dylib"
    assert (
        framework / "Versions" / "3.11" / "Resources" / "Info.plist"
    ).read_bytes() == b"real plist"
