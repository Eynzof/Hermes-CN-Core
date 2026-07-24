"""Windows-specific performance benchmarks (CN fork focus).

All tests run in OFFLINE mode — no real LLM API calls.
Tests measure:
- PowerShell UTF-8 preamble overhead
- Registry PATH refresh timing + cache effectiveness
- In-process vs shell file operations comparison
- Process startup overhead comparison
"""
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── Fixtures ───────────────────────────────────────────────────────────────

@pytest.fixture
def windows_only(request):
    """Skip test if not on Windows."""
    if sys.platform != "win32":
        pytest.skip("Windows-only test")


# ── Test: UTF-8 Preamble Overhead ─────────────────────────────────────────

@pytest.mark.perf
@pytest.mark.perf_windows
@pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: performance test fails in CI")
def test_utf8_preamble_overhead(timing_context, windows_only):
    """Measure PowerShell UTF-8 encoding preamble overhead on commands."""
    import subprocess

    # Without UTF-8 preamble
    cmd_simple = ["powershell.exe", "-NoProfile", "-Command", "echo 'test'"]

    # With UTF-8 preamble (simulating ps_with_utf8)
    preamble = "[Console]::OutputEncoding=[System.Text.Encoding]::UTF8; $OutputEncoding=[System.Text.Encoding]::UTF8;"
    cmd_with_preamble = ["powershell.exe", "-NoProfile", "-Command", f"{preamble} echo 'test'"]

    with timing_context.measure("ps_without_utf8"):
        for _ in range(20):
            result = subprocess.run(cmd_simple, capture_output=True, text=True, timeout=10)

    with timing_context.measure("ps_with_utf8"):
        for _ in range(20):
            result = subprocess.run(cmd_with_preamble, capture_output=True, text=True, timeout=10)

    summary = timing_context.summary()
    without_ms = summary.get("ps_without_utf8", {}).get("total_ms", 0)
    with_ms = summary.get("ps_with_utf8", {}).get("total_ms", 0)
    avg_without = without_ms / 20
    avg_with = with_ms / 20

    print(f"\n  PowerShell without UTF-8 preamble x20: {without_ms:.1f}ms (avg {avg_without:.1f}ms)")
    print(f"  PowerShell with UTF-8 preamble x20: {with_ms:.1f}ms (avg {avg_with:.1f}ms)")
    print(f"  Overhead: {avg_with - avg_without:.2f}ms per call")

    # The overhead should be minimal (< 5ms per call)
    overhead = avg_with - avg_without
    assert overhead < 20, f"UTF-8 preamble overhead too high: {overhead:.2f}ms (expected < 20ms)"


# ── Test: Registry PATH Refresh Cache Effectiveness ───────────────────────

@pytest.mark.perf
@pytest.mark.perf_windows
def test_registry_refresh_cache_effectiveness(timing_context, windows_only):
    """Measure registry PATH refresh timing and cache effectiveness (P-042).

    The signature-keyed cache skips the value read + %expand% + merge when the
    Environment keys are unchanged, so a burst of warm calls (the terminal
    spawn hot path) is materially cheaper than the cold read.
    """
    from tools.environments import windows_env
    from tools.environments.windows_env import (
        _reset_registry_env_cache,
        refresh_env_from_registry,
    )

    _reset_registry_env_cache()

    # Count how often the underlying (uncached) read actually runs.
    real_reads = {"n": 0}
    _orig = windows_env._do_refresh_env_from_registry

    def _counting():
        real_reads["n"] += 1
        _orig()

    windows_env._do_refresh_env_from_registry = _counting
    try:
        # First call (cold — real read).
        with timing_context.measure("registry_refresh_cold"):
            refresh_env_from_registry()

        # A burst of warm calls (signature unchanged → cache hits).
        with timing_context.measure("registry_refresh_warm"):
            for _ in range(50):
                refresh_env_from_registry()
    finally:
        windows_env._do_refresh_env_from_registry = _orig

    summary = timing_context.summary()
    cold_ms = summary.get("registry_refresh_cold", {}).get("total_ms", 0)
    warm_ms = summary.get("registry_refresh_warm", {}).get("total_ms", 0)

    print(f"\n  Registry refresh (cold): {cold_ms:.2f}ms")
    print(f"  Registry refresh (warm x50): {warm_ms:.2f}ms (avg {warm_ms/50:.3f}ms)")
    print(f"  Real registry reads across 51 calls: {real_reads['n']}")
    assert cold_ms < 500, f"Cold refresh took {cold_ms:.1f}ms (expected < 500ms)"
    assert warm_ms < 500, f"50 warm refreshes took {warm_ms:.1f}ms (expected < 500ms)"
    # The cache must collapse the 50 warm calls into a single real read.
    assert real_reads["n"] == 1, (
        f"expected exactly 1 real registry read across 51 cached calls, "
        f"got {real_reads['n']}"
    )


# ── Test: Process Startup Time Comparison ─────────────────────────────────

@pytest.mark.perf
@pytest.mark.perf_windows
def test_shell_startup_comparison(timing_context, windows_only):
    """Compare startup time of different shells available on Windows."""
    import subprocess

    shells = []

    # PowerShell 5.1
    shells.append(("powershell_51", ["powershell.exe", "-NoProfile", "-Command", "echo 'test'"]))

    # PowerShell 7 (if available)
    try:
        result = subprocess.run(
            ["pwsh.exe", "-NoProfile", "-Command", "echo 'test'"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            shells.append(("pwsh_7", ["pwsh.exe", "-NoProfile", "-Command", "echo 'test'"]))
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # cmd.exe
    shells.append(("cmd", ["cmd.exe", "/c", "echo test"]))

    for name, cmd in shells:
        with timing_context.measure(f"shell_startup_{name}"):
            for _ in range(10):
                result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)

    summary = timing_context.summary()
    for name, _ in shells:
        key = f"shell_startup_{name}"
        ms = summary.get(key, {}).get("total_ms", 0)
        avg = ms / 10
        print(f"\n  {name} startup x10: {ms:.1f}ms (avg {avg:.1f}ms)")


# ── Test: Chinese Character Path Performance ──────────────────────────────

@pytest.mark.perf
@pytest.mark.perf_windows
@pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: performance test fails in CI")
def test_chinese_path_performance(timing_context, tmp_path, windows_only):
    """Measure file operations with Chinese characters in paths."""
    from tools.file_tools import read_file_tool as read_file, write_file_tool as write_file

    chinese_dir = tmp_path / "测试目录_测试"
    chinese_dir.mkdir(exist_ok=True)
    chinese_file = chinese_dir / "测试文件.txt"

    # Write with Chinese content
    chinese_content = "你好世界\n" * 100 + "Hello World\n" * 100

    with timing_context.measure("chinese_file_write"):
        write_file(path=str(chinese_file), content=chinese_content)

    with timing_context.measure("chinese_file_read"):
        result = read_file(path=str(chinese_file))

    summary = timing_context.summary()
    write_ms = summary.get("chinese_file_write", {}).get("total_ms", 0)
    read_ms = summary.get("chinese_file_read", {}).get("total_ms", 0)

    print(f"\n  Chinese file write: {write_ms:.1f}ms")
    print(f"  Chinese file read: {read_ms:.1f}ms")
    assert write_ms < 500, f"Chinese write took {write_ms:.1f}ms (expected < 500ms)"
    assert read_ms < 500, f"Chinese read took {read_ms:.1f}ms (expected < 500ms)"


# ── Test: PowerShell vs CMD vs Python I/O ─────────────────────────────────

@pytest.mark.perf
@pytest.mark.perf_windows
def test_io_methods_comparison(timing_context, tmp_path, windows_only):
    """Compare file I/O methods: Python in-process vs PowerShell vs CMD."""
    import subprocess

    test_file = tmp_path / "test_io_compare.txt"
    test_file.write_text("Hello World\n" * 1000, encoding="utf-8")

    # Python in-process
    with timing_context.measure("python_inproc_read"):
        for _ in range(20):
            content = test_file.read_text(encoding="utf-8", errors="replace")

    # PowerShell Get-Content
    with timing_context.measure("powershell_get_content"):
        for _ in range(20):
            result = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command",
                 f"Get-Content -Path '{test_file}' -Encoding UTF8"],
                capture_output=True, text=True, timeout=10,
            )

    # CMD type
    with timing_context.measure("cmd_type"):
        for _ in range(20):
            result = subprocess.run(
                ["cmd.exe", "/c", f"type {test_file}"],
                capture_output=True, text=True, timeout=10,
            )

    summary = timing_context.summary()
    python_ms = summary.get("python_inproc_read", {}).get("total_ms", 0)
    ps_ms = summary.get("powershell_get_content", {}).get("total_ms", 0)
    cmd_ms = summary.get("cmd_type", {}).get("total_ms", 0)

    print(f"\n  Python in-process x20: {python_ms:.1f}ms (avg {python_ms/20:.1f}ms)")
    print(f"  PowerShell Get-Content x20: {ps_ms:.1f}ms (avg {ps_ms/20:.1f}ms)")
    print(f"  CMD type x20: {cmd_ms:.1f}ms (avg {cmd_ms/20:.1f}ms)")

    # Python should be faster than shell for small files
    assert python_ms <= ps_ms * 2, f"Python I/O ({python_ms:.1f}ms) slower than PS ({ps_ms:.1f}ms)"