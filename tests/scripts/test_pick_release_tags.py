"""Exercise CN release selection against a real, isolated tag inventory."""
import json
from pathlib import Path
import subprocess

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/sandbox/pick-release-tags.sh"


@pytest.fixture
def release_repo(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Test", "-c",
         "user.email=test@example.invalid", "commit", "--allow-empty", "-qm", "fixture"],
        check=True,
    )
    return tmp_path


def pick(repo, *tags, count=5):
    for tag in tags:
        subprocess.run(["git", "-C", str(repo), "tag", tag], check=True)
    return subprocess.run(
        ["bash", str(SCRIPT), "--repo", str(repo), "--count", str(count)],
        capture_output=True, text=True,
    )


def test_cn_tags_are_numeric_and_exclude_other_channels(release_repo):
    result = pick(
        release_repo, "runtime-v0.19.0-cn.7", "runtime-v0.20.0-cn.9",
        "runtime-v0.20.0-cn.10", "v2026.8.1", "runtime-v0.20.0-cn.11-rc1",
        "backup/main", count=2,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["runtime-v0.19.0-cn.7", "runtime-v0.20.0-cn.10"]


def test_one_slot_selects_latest_cn_release(release_repo):
    result = pick(release_repo, "runtime-v0.9.0-cn.99", "runtime-v0.20.0-cn.10", count=1)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ["runtime-v0.20.0-cn.10"]


def test_missing_cn_tags_explains_expected_scheme(release_repo):
    result = pick(release_repo, "v2026.8.1")
    assert result.returncode != 0
    assert "runtime-vX.Y.Z-cn.N" in result.stderr
