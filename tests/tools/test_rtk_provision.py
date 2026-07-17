"""Unit tests for ``tools/rtk_provision.py``.

Tests cover:
- ``_rtk_binary_name`` — platform-specific binary names
- ``_rtk_available`` — cached availability detection (fails gracefully)
- ``_find_rtk`` — search order: managed -> legacy -> PATH
"""

from __future__ import annotations

import platform
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from tools.rtk_provision import (
    _IS_WINDOWS,
    _find_rtk,
    _rtk_available,
    _rtk_binary_name,
    _rtk_managed_dir,
    _rtk_managed_path,
)


# =========================================================================
# _rtk_binary_name
# =========================================================================


class TestRtkBinaryName:
    def test_returns_exe_on_windows(self):
        """On Windows, the binary name should be ``rtk.exe``."""
        with patch("tools.rtk_provision._IS_WINDOWS", True):
            assert _rtk_binary_name() == "rtk.exe"

    def test_returns_rtk_on_unix(self):
        """On non-Windows, the binary name should be ``rtk``."""
        with patch("tools.rtk_provision._IS_WINDOWS", False):
            assert _rtk_binary_name() == "rtk"


# =========================================================================
# _find_rtk  — use mock on Path.exists at the class level
# =========================================================================


class TestFindRtk:
    def test_managed_path_found(self):
        """When managed binary exists and runs, it is returned first."""
        managed = Path("/fake/managed/rtk")
        with (
            patch("tools.rtk_provision._rtk_managed_path", return_value=managed),
            patch.object(Path, "exists", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            mock_run.return_value = MagicMock()
            result = _find_rtk()
            assert result == str(managed)
            mock_run.assert_called_once_with(
                [str(managed), "--version"],
                capture_output=True,
                check=True,
                timeout=5,
            )

    def test_managed_path_fails_falls_to_legacy(self):
        """When managed binary fails, fall back to legacy path."""
        managed = Path("/fake/managed/rtk")
        legacy = Path("/fake/legacy/rtk")
        with (
            patch("tools.rtk_provision._rtk_managed_path", return_value=managed),
            patch("tools.rtk_provision._rtk_legacy_path", return_value=legacy),
            patch.object(Path, "exists", return_value=True),
            patch("subprocess.run") as mock_run,
        ):
            # First call (managed) raises, second (legacy) succeeds
            mock_run.side_effect = [
                Exception("managed failed"),
                MagicMock(),
            ]
            result = _find_rtk()
            assert result == str(legacy)

    def test_all_paths_fail_returns_none(self):
        """When no rtk binary works, return None."""
        managed = Path("/fake/managed/rtk")
        legacy = Path("/fake/legacy/rtk")
        with (
            patch("tools.rtk_provision._rtk_managed_path", return_value=managed),
            patch("tools.rtk_provision._rtk_legacy_path", return_value=legacy),
            patch.object(Path, "exists", return_value=True),
            patch("shutil.which", return_value=None),
            patch("subprocess.run", side_effect=Exception("all fail")),
        ):
            result = _find_rtk()
            assert result is None

    def test_falls_back_to_path(self):
        """When no managed/legacy path works, check PATH via shutil.which."""
        managed = Path("/fake/managed/rtk")
        legacy = Path("/fake/legacy/rtk")
        path_binary = "/usr/local/bin/rtk"
        with (
            patch("tools.rtk_provision._rtk_managed_path", return_value=managed),
            patch("tools.rtk_provision._rtk_legacy_path", return_value=legacy),
            patch.object(Path, "exists", return_value=True),
            patch("shutil.which", return_value=path_binary),
            patch("subprocess.run") as mock_run,
        ):
            # Managed fails, legacy fails, PATH succeeds
            mock_run.side_effect = [
                Exception("managed failed"),
                Exception("legacy failed"),
                MagicMock(),
            ]
            result = _find_rtk()
            assert result == path_binary
            assert mock_run.call_count == 3


# =========================================================================
# _rtk_available
# =========================================================================


class TestRtkAvailable:
    def test_returns_false_when_not_found(self):
        """When _find_rtk returns None, _rtk_available should be False."""
        with patch("tools.rtk_provision._find_rtk", return_value=None):
            _rtk_available.cache_clear()
            assert _rtk_available() is False

    def test_returns_true_when_found(self):
        """When _find_rtk returns a path, _rtk_available should be True."""
        with patch("tools.rtk_provision._find_rtk", return_value="/usr/bin/rtk"):
            _rtk_available.cache_clear()
            assert _rtk_available() is True

    def test_result_cached(self):
        """Result should be cached (lru_cache)."""
        _rtk_available.cache_clear()
        with patch("tools.rtk_provision._find_rtk", return_value="/usr/bin/rtk") as mock_find:
            # First call should hit _find_rtk
            assert _rtk_available() is True
            assert mock_find.call_count == 1

            # Second call should use cache
            assert _rtk_available() is True
            assert mock_find.call_count == 1

    def test_cache_cleared_between_tests(self):
        """Ensure cache is cleared for each test run."""
        _rtk_available.cache_clear()
        assert _rtk_available.cache_info().currsize == 0
