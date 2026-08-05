"""Tests for scripts/update_thirdparty.py.

These tests exercise the version-check/update logic with mocked GitHub
responses and temporary copies of the pinned files.  No real network requests
are made.
"""
from __future__ import annotations


import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.update_thirdparty as update_mod


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_project(tmp_path, monkeypatch):
    """Create a temporary project tree with the files the updater touches."""
    scripts_dir = tmp_path / "scripts"
    workflows_dir = tmp_path / ".github" / "workflows"
    tools_dir = tmp_path / "tools"
    scripts_dir.mkdir(parents=True)
    workflows_dir.mkdir(parents=True)
    tools_dir.mkdir(parents=True)

    (scripts_dir / "install.sh").write_text(
        'RIPGREP_VERSION="14.1.1"\n'
        'RTK_VERSION="0.43.0"\n'
    )
    (scripts_dir / "install.ps1").write_text(
        '$rgVersion = "14.1.1"\n'
        '$rtkVersion = "0.43.0"\n'
    )
    (tools_dir / "rtk_provision.py").write_text(
        'RTK_VERSION = "0.43.0"\n'
    )
    (workflows_dir / "tests.yml").write_text(
        "RG_VERSION=14.1.0\n"
        "RG_SHA256=1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599\n"
    )

    monkeypatch.setattr(update_mod, "PROJECT_ROOT", tmp_path)
    return tmp_path


def _run_main(*args):
    """Run the script's main with *args*, saving/restoring ``sys.argv``."""
    old_argv = sys.argv[:]
    try:
        sys.argv = ["update_thirdparty.py", *args]
        return update_mod.main()
    finally:
        sys.argv = old_argv


# ── Basic check-only behavior ─────────────────────────────────────────────────

def test_check_only_up_to_date(fake_project, monkeypatch):
    """--check-only exits 0 when the pinned version matches the latest."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "14.1.1"
    )
    rc = _run_main("--check-only", "ripgrep")
    assert rc == 0
    # No writes should have happened.
    assert 'RIPGREP_VERSION="14.1.1"' in (fake_project / "scripts" / "install.sh").read_text()


def test_check_only_outdated_exits_nonzero(fake_project, monkeypatch):
    """--check-only exits 1 when a newer version exists."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "15.2.0"
    )
    rc = _run_main("--check-only", "ripgrep")
    assert rc == 1
    assert 'RIPGREP_VERSION="14.1.1"' in (fake_project / "scripts" / "install.sh").read_text()


# ── Update behavior ───────────────────────────────────────────────────────────

def test_update_ripgrep_all_files(fake_project, monkeypatch):
    """Updating ripgrep touches install.sh, install.ps1, and tests.yml."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "15.2.0"
    )
    monkeypatch.setattr(
        update_mod, "_compute_sha256", lambda urls: "abcd" * 16
    )

    rc = _run_main("--yes", "ripgrep")
    assert rc == 0

    sh = (fake_project / "scripts" / "install.sh").read_text()
    ps1 = (fake_project / "scripts" / "install.ps1").read_text()
    yml = (fake_project / ".github" / "workflows" / "tests.yml").read_text()

    assert 'RIPGREP_VERSION="15.2.0"' in sh
    assert 'RIPGREP_VERSION="14.1.1"' not in sh

    assert '$rgVersion = "15.2.0"' in ps1
    assert '$rgVersion = "14.1.1"' not in ps1

    assert "RG_VERSION=15.2.0" in yml
    assert "RG_SHA256=" + ("abcd" * 16) in yml


def test_update_rtk_strips_leading_v(fake_project, monkeypatch):
    """rtk tags are ``vX.Y.Z`` but the pinned version is ``X.Y.Z``."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "v0.44.0"
    )

    rc = _run_main("--yes", "rtk")
    assert rc == 0

    sh = (fake_project / "scripts" / "install.sh").read_text()
    ps1 = (fake_project / "scripts" / "install.ps1").read_text()
    py = (fake_project / "tools" / "rtk_provision.py").read_text()

    assert 'RTK_VERSION="0.44.0"' in sh
    assert '$rtkVersion = "0.44.0"' in ps1
    assert 'RTK_VERSION = "0.44.0"' in py


def test_update_ripgrep_skip_shasum(fake_project, monkeypatch):
    """--skip-shasum leaves the workflow checksum untouched."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "15.2.0"
    )
    monkeypatch.setattr(
        update_mod, "_compute_sha256", lambda urls: pytest.fail("Should not download")
    )

    rc = _run_main("--yes", "--skip-shasum", "ripgrep")
    assert rc == 0

    yml = (fake_project / ".github" / "workflows" / "tests.yml").read_text()
    assert "RG_VERSION=15.2.0" in yml
    assert "RG_SHA256=1c9297be4a084eea7ecaedf93eb03d058d6faae29bbc57ecdaf5063921491599" in yml


# ── Tool filtering ────────────────────────────────────────────────────────────

def test_tool_filter_does_not_touch_other_tool(fake_project, monkeypatch):
    """Passing ``rtk`` on the CLI should not touch ripgrep files."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "v0.44.0"
    )

    rc = _run_main("--yes", "rtk")
    assert rc == 0

    sh = (fake_project / "scripts" / "install.sh").read_text()
    assert 'RIPGREP_VERSION="14.1.1"' in sh


# ── Mirror helpers ────────────────────────────────────────────────────────────

def test_iter_mirrors_direct_first_then_default():
    """Direct GitHub is tried first, followed by configured mirrors."""
    mirrors = list(update_mod._iter_mirrors(include_direct=True))
    assert mirrors[0] == ""
    assert "https://ghproxy.com/" in mirrors


def test_iter_mirrors_custom_and_env(monkeypatch):
    """Custom mirrors and env var are inserted before the default list."""
    monkeypatch.setenv("HERMES_THIRDPARTY_MIRROR", "https://env.mirror/")
    mirrors = list(
        update_mod._iter_mirrors(
            include_direct=False,
            custom_mirror="https://custom.mirror/",
        )
    )
    assert mirrors == [
        "https://custom.mirror/",
        "https://env.mirror/",
        *[
            m
            for m in update_mod._DEFAULT_CHINA_MIRRORS
            if m not in ("https://custom.mirror/", "https://env.mirror/")
        ],
    ]


def test_mirror_url_prefixes_github_url():
    """_mirror_url prepends the mirror prefix to a github.com URL."""
    assert (
        update_mod._mirror_url("https://github.com/foo/bar/releases/download/x", "https://ghproxy.com")
        == "https://ghproxy.com/https://github.com/foo/bar/releases/download/x"
    )


# ── Tag cleaning / pattern matching ───────────────────────────────────────────

def test_apply_tag_pattern_extracts_version():
    """A regex can pull the version out of a tag like ``prefix-v1.2.3``."""
    assert update_mod._apply_tag_pattern("cli-v2.0.0", r"^cli-v([\d.]+)$") == "2.0.0"


def test_apply_tag_pattern_returns_none_on_mismatch():
    assert update_mod._apply_tag_pattern("nope", r"^v([\d.]+)$") is None


# ── Partial failure handling ──────────────────────────────────────────────────

def test_sha256_failure_returns_warning_rc(fake_project, monkeypatch):
    """If the SHA256 cannot be computed the script warns but still updates versions."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "15.2.0"
    )
    monkeypatch.setattr(update_mod, "_compute_sha256", lambda urls: None)

    rc = _run_main("--yes", "ripgrep")
    assert rc == 1

    sh = (fake_project / "scripts" / "install.sh").read_text()
    assert 'RIPGREP_VERSION="15.2.0"' in sh


# ── CLI integration via subprocess ────────────────────────────────────────────

def test_script_importable_and_check_only_up_to_date(fake_project, monkeypatch):
    """The module can be run as ``__main__`` with mocked dependencies."""
    monkeypatch.setattr(
        update_mod, "_get_latest_tag", lambda repo, pattern, mirrors: "0.43.0"
    )
    # rtk does not need SHA256, so this combination exercises the happy path.
    rc = _run_main("--check-only", "rtk")
    assert rc == 0
