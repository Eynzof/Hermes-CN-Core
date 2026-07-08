"""Tests for the jobs.json read cache and atomic write path (P1 perf work).

Two behaviours are pinned here, both deterministically (no timing assertions):

  * **mtime/size-invalidated read cache** in ``load_jobs`` — an unchanged
    ``jobs.json`` is served from memory (no re-parse), while any real on-disk
    change (mtime OR size), a ``save_jobs`` write, or a repointed ``JOBS_FILE``
    is picked up. The returned list is always independent of the cached copy.
  * **atomic ``save_jobs`` writes** (``os.replace`` via ``utils.atomic_replace``)
    — a crash mid-write leaves the previous file intact with no tmp turds, and a
    successful save swaps the whole file into place.

See reports/perf/2026-07-06-cron-scheduler.md and
.plans/08-Cron-Lock-File-Overhead.md for the motivating hotspot.
"""

import orjson
import os
from unittest.mock import patch

import pytest

from cron import jobs as cron_jobs


@pytest.fixture
def tmp_cron_dir(tmp_path, monkeypatch):
    """Redirect cron storage to a temp dir and start from a clean read cache."""
    monkeypatch.setattr(cron_jobs, "CRON_DIR", tmp_path / "cron")
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", tmp_path / "cron" / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", tmp_path / "cron" / "output")
    cron_jobs._invalidate_jobs_cache()
    yield tmp_path
    cron_jobs._invalidate_jobs_cache()


class TestJobsReadCache:
    def test_jobs_cache_invalidation(self, tmp_cron_dir):
        """An unchanged ``(mtime_ns, size)`` is served from cache; changing the
        mtime invalidates it and forces a re-read."""
        jobs_file = cron_jobs.JOBS_FILE
        jobs_file.parent.mkdir(parents=True, exist_ok=True)

        # Two payloads of identical byte length but different ids, so the only
        # thing that can distinguish them via stat() is the mtime.
        payload_a = '{"jobs": [{"id": "alpha"}]}'
        payload_b = '{"jobs": [{"id": "bravo"}]}'
        assert len(payload_a) == len(payload_b)

        jobs_file.write_text(payload_a, encoding="utf-8")
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["alpha"]

        # Overwrite the BYTES with payload_b but restore the exact
        # (mtime_ns, size) the cache recorded: same length + pinned mtime => the
        # signature matches => cache HIT => the stale copy is returned and the
        # new bytes are (correctly, by design) ignored.
        sig = os.stat(jobs_file)
        jobs_file.write_text(payload_b, encoding="utf-8")
        os.utime(jobs_file, ns=(sig.st_mtime_ns, sig.st_mtime_ns))
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["alpha"], (
            "an unchanged (mtime, size) signature must serve the cached copy"
        )

        # Bump only the mtime => signature changes => cache INVALIDATED.
        newer = sig.st_mtime_ns + 5_000_000_000  # +5s, unambiguous on any FS
        os.utime(jobs_file, ns=(newer, newer))
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["bravo"], (
            "an mtime change must invalidate the cache and re-read from disk"
        )

    def test_size_change_invalidates_even_with_same_mtime(self, tmp_cron_dir):
        """A size change is caught even if the mtime is (artificially) unchanged."""
        jobs_file = cron_jobs.JOBS_FILE
        jobs_file.parent.mkdir(parents=True, exist_ok=True)

        jobs_file.write_text('{"jobs": [{"id": "x"}]}', encoding="utf-8")
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["x"]

        sig = os.stat(jobs_file)
        jobs_file.write_text('{"jobs": [{"id": "x"}, {"id": "y"}]}', encoding="utf-8")
        os.utime(jobs_file, ns=(sig.st_mtime_ns, sig.st_mtime_ns))  # pin mtime back
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["x", "y"], (
            "a size change must invalidate the cache even when mtime is unchanged"
        )

    def test_cache_hit_avoids_reparse(self, tmp_cron_dir, monkeypatch):
        """An unchanged file must not be re-parsed on subsequent loads."""
        cron_jobs.save_jobs([{"id": "j1"}])
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["j1"]  # prime

        calls = []
        real_load = json.load

        def counting_load(*a, **k):
            calls.append(1)
            return real_load(*a, **k)

        monkeypatch.setattr(cron_jobs.json, "load", counting_load)
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["j1"]
        assert calls == [], "an unchanged jobs.json must be served from cache, not re-parsed"

    def test_save_primes_cache(self, tmp_cron_dir, monkeypatch):
        """The first read straight after a save is a cache hit (no re-parse)."""
        cron_jobs.save_jobs([{"id": "seed"}])

        calls = []
        real_load = json.load
        monkeypatch.setattr(
            cron_jobs.json,
            "load",
            lambda *a, **k: (calls.append(1), real_load(*a, **k))[1],
        )
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["seed"]
        assert calls == [], "save_jobs must prime the read cache with the persisted content"

    def test_returned_list_is_independent_of_cache(self, tmp_cron_dir):
        """Mutating the list load_jobs() returned must never poison the cache."""
        cron_jobs.save_jobs([{"id": "a"}])
        first = cron_jobs.load_jobs()
        first.append({"id": "MUTANT"})
        first[0]["id"] = "CLOBBERED"
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["a"]

    def test_cache_is_per_path(self, tmp_path, monkeypatch):
        """Repointing JOBS_FILE (profile switch / test) is a natural cache miss."""
        dir_a = tmp_path / "a" / "cron"
        dir_b = tmp_path / "b" / "cron"

        monkeypatch.setattr(cron_jobs, "CRON_DIR", dir_a)
        monkeypatch.setattr(cron_jobs, "JOBS_FILE", dir_a / "jobs.json")
        monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", dir_a / "output")
        cron_jobs._invalidate_jobs_cache()
        cron_jobs.save_jobs([{"id": "in_a"}])
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["in_a"]

        monkeypatch.setattr(cron_jobs, "CRON_DIR", dir_b)
        monkeypatch.setattr(cron_jobs, "JOBS_FILE", dir_b / "jobs.json")
        monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", dir_b / "output")
        cron_jobs.save_jobs([{"id": "in_b"}])
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["in_b"], (
            "a different JOBS_FILE path must not return the previously cached store"
        )

    def test_missing_file_returns_empty_and_ignores_stale_cache(self, tmp_cron_dir):
        assert cron_jobs.load_jobs() == []
        cron_jobs.save_jobs([{"id": "z"}])
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["z"]
        cron_jobs.JOBS_FILE.unlink()
        # File gone => empty, regardless of what the cache last held.
        assert cron_jobs.load_jobs() == []


class TestLockFileAtomicity:
    def test_lock_file_atomicity(self, tmp_cron_dir):
        """save_jobs replaces jobs.json atomically via ``os.replace``.

        A crash while writing the temp file must not touch the live file and
        must leave no ``.jobs_*.tmp`` leftover; a successful save swaps the whole
        file into place at ``jobs.json``.
        """
        jobs_file = cron_jobs.JOBS_FILE

        cron_jobs.save_jobs([{"id": "a", "prompt": "first"}])
        original = jobs_file.read_text(encoding="utf-8")
        assert orjson.loads(original)["jobs"][0]["id"] == "a"

        # 1. All-or-nothing: a failure mid-write leaves the previous file intact.
        def boom(*a, **k):
            raise RuntimeError("disk full")

        with patch.object(cron_jobs.json, "dump", boom):
            with pytest.raises(RuntimeError, match="disk full"):
                cron_jobs.save_jobs([{"id": "b", "prompt": "second"}])
        assert jobs_file.read_text(encoding="utf-8") == original, "live file corrupted by a failed write"
        assert list(jobs_file.parent.glob(".jobs_*.tmp")) == [], "atomic write leaked a tmp file"

        # 2. A successful overwrite goes through os.replace targeting jobs.json.
        seen = {}
        real_replace = os.replace

        def spy_replace(src, dst, *a, **k):
            seen["dst"] = os.fspath(dst)
            return real_replace(src, dst, *a, **k)

        with patch.object(os, "replace", spy_replace):
            cron_jobs.save_jobs([{"id": "c", "prompt": "third"}])
        assert os.path.realpath(seen["dst"]) == os.path.realpath(str(jobs_file))
        assert orjson.loads(jobs_file.read_text(encoding="utf-8"))["jobs"][0]["id"] == "c"
        assert list(jobs_file.parent.glob(".jobs_*.tmp")) == []

    def test_failed_write_invalidates_cache(self, tmp_cron_dir):
        """A failed save drops the read cache so the next load re-reads the
        last good file rather than serving a would-be-written value."""
        cron_jobs.save_jobs([{"id": "good"}])
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["good"]

        def boom(*a, **k):
            raise RuntimeError("disk full")

        with patch.object(cron_jobs.json, "dump", boom):
            with pytest.raises(RuntimeError):
                cron_jobs.save_jobs([{"id": "never"}])

        # Cache was invalidated on failure; the on-disk (last good) value wins.
        assert [j["id"] for j in cron_jobs.load_jobs()] == ["good"]
