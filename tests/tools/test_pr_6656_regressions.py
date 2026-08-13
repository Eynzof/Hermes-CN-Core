"""Regression tests for PR #6656 — skill uninstall + bundle hash + pairing lock.

Three independent fixes that were salvaged together:

1. ``uninstall_skill`` path traversal: ``install_path`` comes from a JSON
   file on disk; a malicious skill could write ``install_path: "../../"``
   and trigger ``shutil.rmtree`` against parent directories. Guarded with
   ``Path.resolve().is_relative_to(SKILLS_DIR.resolve())``.

2. ``bundle_content_hash`` / ``content_hash`` filename inclusion: the
   previous hash mixed only file CONTENTS, so swapping ``SKILL.md`` and
   ``scripts/run.sh`` contents between two paths produced the same digest.
   Now both functions prefix each entry with ``rel_path + \\x00`` and
   stay symmetric (one on disk, one on in-memory bundle).

3. ``PairingStore.list_pending`` TOCTOU: previously called
   ``_cleanup_expired`` (which writes the JSON file) without holding
   ``self._lock``, racing with ``generate_code`` / ``approve_code``.
"""
from __future__ import annotations


import orjson
from pathlib import Path
from unittest.mock import patch

import pytest

from tools.skills_hub import (
    SkillBundle,
    bundle_content_hash,
    uninstall_skill,
)
from tools.skills_guard import content_hash

# =============================================================================
# uninstall_skill: path traversal guard
# =============================================================================

class TestUninstallPathTraversal:
    """The ``install_path`` field in ``lock.json`` is attacker-controllable
    if a malicious skill is ever installed (or if the hub's lockfile is
    corrupted). The uninstall path must refuse anything that resolves
    outside ``SKILLS_DIR``.
    """

    @pytest.fixture
    def hub_setup(self, tmp_path, monkeypatch):
        """Build a hub directory tree with a malicious lock.json entry.

        ``HubLockFile`` binds its default ``path`` argument at def time
        against the module-level ``LOCK_FILE`` constant, so monkey-patching
        ``LOCK_FILE`` alone is not enough — we also need to rebind the
        function default. Patching ``HubLockFile.__init__.__defaults__``
        is the standard tool for this.
        """
        import tools.skills_hub as hub
        skills_dir = tmp_path / "skills"
        hub_dir = skills_dir / ".hub"
        hub_dir.mkdir(parents=True)
        lock_path = hub_dir / "lock.json"

        # tools.skills_hub exposes these paths via PEP 562 __getattr__ — they
        # are NOT real module attributes. monkeypatch.setattr would restore
        # them by writing the pre-test resolved value back as a REAL
        # attribute, permanently freezing the dynamic resolution for every
        # later test. Set/remove them in __dict__ directly instead.
        _MISSING = object()
        _pinned = {
            "SKILLS_DIR": skills_dir,
            "HUB_DIR": hub_dir,
            "LOCK_FILE": lock_path,
            "AUDIT_LOG": hub_dir / "audit.log",
        }
        _saved = {n: hub.__dict__.get(n, _MISSING) for n in _pinned}
        hub.__dict__.update(_pinned)
        # Rebind HubLockFile.__init__'s default `path=` arg so
        # `HubLockFile()` (no args) picks up the new lock path.
        monkeypatch.setattr(
            hub.HubLockFile.__init__,
            "__defaults__",
            (lock_path,),
        )

        # A real directory outside skills_dir that the traversal would
        # delete if the guard fails.
        victim = tmp_path / "do-not-delete"
        victim.mkdir()
        (victim / "important.txt").write_text("data")
        yield skills_dir, hub_dir, victim
        # Restore the exact pre-test module state (usually: names absent, so
        # PEP 562 __getattr__ dynamic resolution keeps working).
        for _n, _v in _saved.items():
            if _v is _MISSING:
                hub.__dict__.pop(_n, None)
            else:
                hub.__dict__[_n] = _v

    def _write_lock(self, hub_dir: Path, entries: dict) -> None:
        lock_path = hub_dir / "lock.json"
        lock_path.write_text(orjson.dumps({"version": 1, "installed": entries}).decode('utf-8'))

    def test_traversal_via_parent_segments_rejected(self, hub_setup):
        """install_path: "../do-not-delete" must NOT escape SKILLS_DIR."""
        skills_dir, hub_dir, victim = hub_setup
        self._write_lock(hub_dir, {
            "evil": {
                "install_path": "../do-not-delete",
                "source": "https://example.com",
                "version": "1.0",
            },
        })

        ok, msg = uninstall_skill("evil")

        assert ok is False
        assert (
            "outside" in msg
            or "resolves" in msg
            or "skills directory" in msg
            or "Unsafe install path" in msg
        )
        # The victim directory MUST still exist.
        assert victim.exists()
        assert (victim / "important.txt").exists()

    def test_absolute_path_rejected(self, hub_setup):
        """install_path that's an absolute path outside SKILLS_DIR must be refused."""
        skills_dir, hub_dir, victim = hub_setup
        self._write_lock(hub_dir, {
            "evil": {
                "install_path": str(victim),
                "source": "https://example.com",
                "version": "1.0",
            },
        })

        ok, msg = uninstall_skill("evil")

        # SKILLS_DIR / "<absolute>" still results in an absolute path,
        # which when resolved is outside skills_dir. Must be refused.
        assert ok is False
        assert victim.exists()

    def test_symlink_escape_rejected(self, tmp_path, hub_setup):
        """Symlinks inside SKILLS_DIR that point outside must be refused
        after realpath resolution."""
        skills_dir, hub_dir, victim = hub_setup
        # Create a "skill" that's actually a symlink to victim
        evil_link = skills_dir / "trapdoor"
        evil_link.symlink_to(victim)

        self._write_lock(hub_dir, {
            "trap": {
                "install_path": "trapdoor",
                "source": "https://example.com",
                "version": "1.0",
            },
        })

        ok, msg = uninstall_skill("trap")

        # realpath resolves the symlink → outside skills_dir → refused.
        assert ok is False
        assert victim.exists()
        assert (victim / "important.txt").exists()

    def test_legitimate_skill_uninstall_still_works(self, hub_setup):
        """The guard must NOT block a normal skill directory inside SKILLS_DIR."""
        skills_dir, hub_dir, _victim = hub_setup
        legit = skills_dir / "category" / "my-skill"
        legit.mkdir(parents=True)
        (legit / "SKILL.md").write_text("test")

        self._write_lock(hub_dir, {
            "my-skill": {
                "install_path": "category/my-skill",
                "source": "https://example.com",
                "trust_level": "community",
                "version": "1.0",
            },
        })

        ok, msg = uninstall_skill("my-skill")

        assert ok is True
        assert not legit.exists()

# =============================================================================
# Bundle / disk hash symmetry + filename inclusion
# =============================================================================

class TestBundleHashFilenameSensitivity:
    """Hashes must change when filenames are swapped, even if combined
    contents stay identical. ``bundle_content_hash`` (in-memory) and
    ``content_hash`` (on-disk) must stay symmetric — they're used to
    detect skill drift between an installed bundle and its source.
    """

    def _make_bundle(self, files: dict) -> SkillBundle:
        return SkillBundle(
            name="test",
            files=files,
            source="test",
            identifier="test/test",
            trust_level="community",
        )

    def test_filename_swap_changes_hash(self):
        """Swapping content between SKILL.md and scripts/run.sh must
        produce a different hash. Without the filename in the hash,
        these two bundles would have looked identical."""
        a = self._make_bundle({"SKILL.md": "hello", "scripts/run.sh": "world"})
        b = self._make_bundle({"SKILL.md": "world", "scripts/run.sh": "hello"})
        assert bundle_content_hash(a) != bundle_content_hash(b)


    def test_bundle_and_disk_hash_match(self, tmp_path):
        """Symmetry contract: the same skill, expressed as a SkillBundle
        and as a directory tree, must produce the same digest. If this
        fails, ``check_for_skill_updates`` will flag every clean
        install as drifted."""
        skill_dir = tmp_path / "skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("hello")
        (skill_dir / "scripts").mkdir()
        (skill_dir / "scripts" / "run.sh").write_text("world")

        bundle = self._make_bundle({
            "SKILL.md": "hello",
            "scripts/run.sh": "world",
        })

        assert bundle_content_hash(bundle) == content_hash(skill_dir)

# =============================================================================
# PairingStore.list_pending: must hold the lock
# =============================================================================

class TestListPendingLock:
    """list_pending writes via _cleanup_expired. Without the lock,
    a concurrent generate_code or approve_code can race against the
    write, potentially clobbering a pending approval."""

    def test_list_pending_returns_correct_data(self, tmp_path):
        """End-to-end smoke: even with the lock held, basic operation works."""
        from gateway.pairing import PairingStore
        with patch("gateway.pairing.PAIRING_DIR", tmp_path):
            store = PairingStore()
            store.generate_code("telegram", "user1", "Alice")
            pending = store.list_pending("telegram")
        assert len(pending) == 1
        assert pending[0]["user_id"] == "user1"
