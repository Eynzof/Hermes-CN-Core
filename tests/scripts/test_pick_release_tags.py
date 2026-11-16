"""The CN release-tag picker reads origin and keeps numeric runtime order."""
import json
from pathlib import Path
import subprocess


SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "sandbox" / "pick-release-tags.sh"


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True,
    )


def _pick(repo, *args, check=True):
    return subprocess.run(
        ["bash", str(SCRIPT), *args, "--repo", str(repo)],
        check=check, capture_output=True, text=True,
    )


def _origin_with_tags(root, tags):
    origin = root / "origin.git"
    _git(root, "init", "--bare", "-b", "main", str(origin))
    seed = root / "seed"
    _git(root, "init", "-b", "main", str(seed))
    _git(seed, "config", "user.name", "fixture")
    _git(seed, "config", "user.email", "fixture@example.invalid")
    for tag in tags:
        _git(seed, "commit", "--allow-empty", "-m", tag)
        _git(seed, "tag", tag)
    _git(seed, "push", str(origin), "main", "refs/tags/*:refs/tags/*")
    return origin


def _tagless_clone(root, origin):
    clone = root / "tagless"
    _git(root, "clone", "--no-local", "--no-tags", str(origin), str(clone))
    return clone


def test_picking_reads_origin_and_selects_numeric_cn_tags(tmp_path):
    origin = _origin_with_tags(tmp_path, [
        "runtime-v0.19.0-cn.7", "runtime-v0.20.0-cn.9",
        "runtime-v0.20.0-cn.10", "v2026.8.1",
        "runtime-v0.20.0-cn.11-rc1", "backup/main",
    ])
    clone = _tagless_clone(tmp_path, origin)

    result = _pick(clone, "--count", "2")

    assert json.loads(result.stdout) == ["runtime-v0.19.0-cn.7", "runtime-v0.20.0-cn.10"]
    # Only the selected refs are materialized in a tagless checkout.
    assert _git(clone, "tag", "--list").stdout.split() == [
        "runtime-v0.19.0-cn.7", "runtime-v0.20.0-cn.10",
    ]


def test_one_slot_selects_latest_cn_release(tmp_path):
    origin = _origin_with_tags(
        tmp_path, ["runtime-v0.9.0-cn.99", "runtime-v0.20.0-cn.10"],
    )
    clone = _tagless_clone(tmp_path, origin)

    result = _pick(clone, "--count", "1")

    assert json.loads(result.stdout) == ["runtime-v0.20.0-cn.10"]


def test_missing_cn_tags_explains_expected_scheme(tmp_path):
    origin = _origin_with_tags(tmp_path, ["v2026.8.1"])
    clone = _tagless_clone(tmp_path, origin)

    result = _pick(clone, check=False)

    assert result.returncode != 0
    assert "runtime-vX.Y.Z-cn.N" in result.stderr
    assert "release tags found on origin" in result.stderr
