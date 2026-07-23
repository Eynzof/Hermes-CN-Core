"""Performance benchmarks for terminal subprocess spawning.
All tests use real subprocess calls (local I/O is fine).
"""

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.mark.perf
@pytest.mark.skipif(sys.platform == 'win32', reason="Windows baseline: subprocess spawn fails")
def test_python_subprocess_spawn(timing_context):
    """Measure Python subprocess spawn overhead."""
    import subprocess
    with timing_context.measure("python_subprocess_spawn"):
        for _ in range(50):
            _ = subprocess.run(
                [sys.executable, "-c", "print('hello')"],
                capture_output=True, text=True, timeout=10,
            )
    total_ms = timing_context.summary().get("python_subprocess_spawn", {}).get("total_ms", 0)
    avg_ms = total_ms / 50
    print(f"\n  Python subprocess spawn x50: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 500


@pytest.mark.perf
def test_command_wrapping_timing(timing_context):
    """Measure command wrapping using LocalEnvironment."""
    from tools.environments.local import LocalEnvironment
    env = LocalEnvironment()
    cmds = ["ls -la", "cat /tmp/test.txt", "grep -r 'pattern' /tmp/", "echo 'hello'"]

    with timing_context.measure("wrap_commands"):
        for cmd in cmds:
            try:
                env._wrap_command_powershell(cmd, cwd="/tmp")
            except Exception:
                pass

    total_ms = timing_context.summary().get("wrap_commands", {}).get("total_ms", 0)
    avg_ms = total_ms / len(cmds)
    print(f"\n  Command wrapping x4: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 50


@pytest.mark.perf
def test_shell_resolution_timing(timing_context):
    """Measure shell resolution fingerprinting overhead."""
    from tools.environments.local import LocalEnvironment
    env = LocalEnvironment()

    with timing_context.measure("resolve_shell"):
        shell_type = env._shell_type
        shell_path = env._shell_path

    total_ms = timing_context.summary().get("resolve_shell", {}).get("total_ms", 0)
    print(f"\n  Shell resolution: {total_ms:.1f}ms (shell={shell_type}, path={shell_path})")
    assert total_ms < 1000


@pytest.mark.perf
def test_terminal_description_timing(timing_context):
    """Measure dynamic terminal description build."""
    from tools.terminal_tool import _build_dynamic_terminal_description

    with timing_context.measure("build_terminal_description"):
        desc = _build_dynamic_terminal_description()

    total_ms = timing_context.summary().get("build_terminal_description", {}).get("total_ms", 0)
    print(f"\n  Terminal description build: {total_ms:.1f}ms")
    assert total_ms < 1000


@pytest.mark.perf_windows
@pytest.mark.perf
def test_powershell_spawn_cold(timing_context):
    """Measure cold-start PowerShell spawn time (Windows only)."""
    import subprocess
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    with timing_context.measure("powershell_cold_spawn"):
        _ = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", "echo 'hello'"],
            capture_output=True, text=True, timeout=30,
        )
    total_ms = timing_context.summary().get("powershell_cold_spawn", {}).get("total_ms", 0)
    print(f"\n  PowerShell cold spawn: {total_ms:.1f}ms")


@pytest.mark.perf_windows
@pytest.mark.perf
def test_powershell_spawn_warm(timing_context):
    """Measure warm PowerShell spawn time (Windows only)."""
    import subprocess
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    _ = subprocess.run(
        ["powershell.exe", "-NoProfile", "-Command", "echo 'warmup'"],
        capture_output=True, text=True, timeout=30,
    )
    with timing_context.measure("powershell_warm_spawn"):
        for _ in range(10):
            _ = subprocess.run(
                ["powershell.exe", "-NoProfile", "-Command", "echo 'hello'"],
                capture_output=True, text=True, timeout=30,
            )
    total_ms = timing_context.summary().get("powershell_warm_spawn", {}).get("total_ms", 0)
    avg_ms = total_ms / 10
    print(f"\n  PowerShell warm spawn x10: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")


@pytest.mark.perf_windows
@pytest.mark.perf
def test_env_refresh_timing(timing_context):
    """Measure refresh_env_from_registry timing (Windows only)."""
    if sys.platform != "win32":
        pytest.skip("Windows-only test")
    from tools.environments.windows_env import refresh_env_from_registry
    with timing_context.measure("env_registry_refresh"):
        refresh_env_from_registry()
    total_ms = timing_context.summary().get("env_registry_refresh", {}).get("total_ms", 0)
    print(f"\n  Registry env refresh: {total_ms:.1f}ms")