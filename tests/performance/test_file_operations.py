"""Performance benchmarks for file operations (in-process I/O).

All tests use real temp files (local I/O, no network).
Tests measure read/write/search performance at various file sizes.
"""

import orjson
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def temp_test_files(tmp_path):
    """Create test files of various sizes."""
    small = tmp_path / "small.txt"
    small.write_text("Hello World\n" * 50, encoding="utf-8")

    medium = tmp_path / "medium.txt"
    medium.write_text("Line of text with some content\n" * 5000, encoding="utf-8")

    large = tmp_path / "large.txt"
    large.write_text("A" * 1024 * 1024, encoding="utf-8")

    py_dir = tmp_path / "src"
    py_dir.mkdir()
    (py_dir / "module.py").write_text("def hello():\n    print('Hello World')\n", encoding="utf-8")
    (py_dir / "utils.py").write_text("import sys\n\ndef util():\n    return 'util'\n", encoding="utf-8")

    return {"small": small, "medium": medium, "large": large, "py_dir": py_dir}


@pytest.mark.perf
def test_read_file_small(timing_context, temp_test_files):
    """Measure reading a small (1KB) file using Python stdlib."""
    p = temp_test_files["small"]
    with timing_context.measure("read_small"):
        for _ in range(10):
            content = p.read_text(encoding="utf-8")
    total_ms = timing_context.summary().get("read_small", {}).get("total_ms", 0)
    avg_ms = total_ms / 10
    print(f"\n  Read 1KB x10: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 50, f"Read 1KB avg {avg_ms:.1f}ms (expected < 50ms)"


@pytest.mark.perf
def test_read_file_medium(timing_context, temp_test_files):
    """Measure reading a medium (100KB) file."""
    p = temp_test_files["medium"]
    with timing_context.measure("read_medium"):
        for _ in range(5):
            content = p.read_text(encoding="utf-8")
    total_ms = timing_context.summary().get("read_medium", {}).get("total_ms", 0)
    avg_ms = total_ms / 5
    print(f"\n  Read 100KB x5: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 200, f"Read 100KB avg {avg_ms:.1f}ms (expected < 200ms)"


@pytest.mark.perf
def test_read_file_large(timing_context, temp_test_files):
    """Measure reading a large (1MB) file."""
    p = temp_test_files["large"]
    with timing_context.measure("read_large"):
        content = p.read_text(encoding="utf-8")
    total_ms = timing_context.summary().get("read_large", {}).get("total_ms", 0)
    print(f"\n  Read 1MB: {total_ms:.1f}ms")
    assert total_ms < 1000, f"Read 1MB took {total_ms:.1f}ms (expected < 1000ms)"


@pytest.mark.perf
def test_write_file_timing(timing_context, tmp_path):
    """Measure writing files of various sizes."""
    tf = tmp_path / "output.txt"
    with timing_context.measure("write_small"):
        for i in range(10):
            tf.write_text(f"Test content line {i}\n", encoding="utf-8")

    with timing_context.measure("write_large"):
        (tmp_path / "large_out.txt").write_text("X" * 1024 * 100, encoding="utf-8")

    summary = timing_context.summary()
    print(f"\n  Write small x10: {summary.get('write_small', {}).get('total_ms', 0):.1f}ms")
    print(f"  Write 100KB: {summary.get('write_large', {}).get('total_ms', 0):.1f}ms")


@pytest.mark.perf
def test_search_files_timing(timing_context, temp_test_files):
    """Measure in-process file search performance."""
    import fnmatch
    py_dir = temp_test_files["py_dir"]

    with timing_context.measure("search_by_pattern"):
        for _ in range(10):
            matches = list(py_dir.glob("*.py"))

    with timing_context.measure("search_by_content"):
        for _ in range(10):
            for f in py_dir.glob("*.py"):
                content = f.read_text(encoding="utf-8")
                if "hello" in content:
                    _ = True

    summary = timing_context.summary()
    print(f"\n  Search by pattern x10: {summary.get('search_by_pattern', {}).get('total_ms', 0):.1f}ms")
    print(f"  Search by content x10: {summary.get('search_by_content', {}).get('total_ms', 0):.1f}ms")


@pytest.mark.perf
def test_patch_file_timing(timing_context, tmp_path):
    """Measure diff application overhead."""
    import difflib
    original = "Hello World\nLine 2\nLine 3\nLine 4\nLine 5\n"
    tf = tmp_path / "patch_test.txt"
    tf.write_text(original, encoding="utf-8")

    new_content = "Hello World\nModified Line 2\nLine 3\nLine 4\nAdded Line 5.5\nLine 6\n"
    diff = list(difflib.unified_diff(
        original.splitlines(keepends=True),
        new_content.splitlines(keepends=True),
    ))

    diff_text = "".join(diff)
    with timing_context.measure("patch_apply"):
        for _ in range(10):
            # Simulate patch: read, apply diff, write
            cur = tf.read_text(encoding="utf-8")
            # Simple simulated apply
            tf.write_text(new_content, encoding="utf-8")

    total_ms = timing_context.summary().get("patch_apply", {}).get("total_ms", 0)
    avg_ms = total_ms / 10
    print(f"\n  Patch apply x10: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 100, f"Patch avg {avg_ms:.1f}ms (expected < 100ms)"


@pytest.mark.perf
def test_count_lines_timing(timing_context, tmp_path):
    """Measure line counting performance for different file sizes."""
    files = {}
    for n_lines, name in [(10, "small"), (1000, "medium"), (10000, "large")]:
        path = tmp_path / f"{name}_lines.txt"
        path.write_text("\n".join([f"Line {i}" for i in range(n_lines)]), encoding="utf-8")
        files[name] = path

    for name, path in files.items():
        with timing_context.measure(f"count_lines_{name}"):
            for _ in range(10):
                text = path.read_text(encoding="utf-8")
                line_count = len(text.splitlines())

    summary = timing_context.summary()
    for name in ["small", "medium", "large"]:
        key = f"count_lines_{name}"
        ms = summary.get(key, {}).get("total_ms", 0)
        avg_ms = ms / 10 if ms > 0 else 0
        print(f"\n  Count lines {name} x10: {ms:.1f}ms (avg {avg_ms:.1f}ms)")

# ─────────────────────────────────────────────────────────────────────────────
# P1: Concurrent file ops — striped per-file locks, no cross-file contention
# ─────────────────────────────────────────────────────────────────────────────


class _FakeLocalEnv:
    """Minimal stand-in for a local terminal env (only ``cwd`` is read)."""

    def __init__(self, cwd):
        self.cwd = cwd


@pytest.mark.perf
def test_concurrent_file_ops_no_contention(timing_context, tmp_path):
    """Ten concurrent in-process reads/writes stay fast and correct.

    The striped per-file write lock must NOT serialize distinct files (that
    would just recreate the contention the plan set out to remove), while still
    keeping same-file writers from interleaving so every read observes a
    fully-written payload.
    """
    from concurrent.futures import ThreadPoolExecutor
    from tools.file_operations import ShellFileOperations, _get_file_lock

    ops = ShellFileOperations(_FakeLocalEnv(str(tmp_path)))
    # Force the in-process disk primitives regardless of host OS so the test is
    # portable and exercises the striped per-file lock path directly.
    ops._use_inproc_io = lambda: True

    # Per-file (striped) locks: stable per path, shared across path spellings,
    # and NOT a single global lock.
    a = str(tmp_path / "a.txt")
    assert _get_file_lock(a) is _get_file_lock(a)
    assert _get_file_lock(a) is _get_file_lock(str(tmp_path / "." / "a.txt"))

    n = 10

    def _round_trip(i):
        p = str(tmp_path / f"f{i}.txt")
        payload = f"payload-{i}-" + ("x" * 256)
        w = ops._local_atomic_write(p, payload)
        assert w.exit_code == 0, w.stdout
        r = ops._prim_read_all(p)
        assert r.exit_code == 0 and r.stdout == payload
        return i

    with timing_context.measure("concurrent_file_ops"):
        with ThreadPoolExecutor(max_workers=n) as ex:
            done = sorted(ex.map(_round_trip, range(n)))
    assert done == list(range(n))

    # Hammer the SAME file from many threads: every read must return one of the
    # complete payloads (never a torn/partial write).
    shared = str(tmp_path / "shared.txt")
    valid = {"v" * k for k in range(1, 41)}
    torn: list[str] = []

    def _hammer(k):
        content = "v" * (k + 1)
        assert ops._local_atomic_write(shared, content).exit_code == 0
        got = ops._prim_read_all(shared)
        if got.exit_code != 0 or got.stdout not in valid:
            torn.append(got.stdout[:16])

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_hammer, range(40)))
    assert not torn, f"observed torn reads: {torn[:3]}"

    total_ms = timing_context.summary().get("concurrent_file_ops", {}).get("total_ms", 0)
    print(f"\n  {n} concurrent file read/write: {total_ms:.1f}ms")
    assert total_ms < 500, f"{n} concurrent file ops took {total_ms:.1f}ms (expected < 500ms)"
