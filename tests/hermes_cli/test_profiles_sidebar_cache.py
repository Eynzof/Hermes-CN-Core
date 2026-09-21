"""Regression tests for dashboard sidebar scan coalescing."""

import inspect
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

from hermes_cli.web_routers import profiles


# The canonical runner executes one pytest process per file on every core (16-48 at
# once), so "start N threads and wait 1s for one of them to reach the scan" races
# thread startup rather than the coalescing under test. The burst tests below release
# every caller through a threading.Barrier — all callers are live before the first scan
# is allowed to finish — and use a bound generous enough for a saturated box
# (AGENTS.md: timing tests must not assume a quiet runner, wall-clock bounds >= 2s).
_BURST_WAIT_SECONDS = 30.0
# TTL used while a burst is in flight. Which TTL is right is not what a burst test
# measures (test_expires does), but with the 5s default a caller that the runner
# schedules >5s after the scan finished opens a second one and fails the "one scan
# serves the burst" assertion for a reason that has nothing to do with coalescing.
_BURST_TTL_SECONDS = 300.0


class SidebarCacheTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 5.0)
        patcher.start()
        self.addCleanup(patcher.stop)
        profiles._sidebar_profile_cache_clear()
        self.addCleanup(profiles._sidebar_profile_cache_clear)

    def test_profile_cache_uses_db_and_wal_fingerprint_and_defensive_copies(self):
        with tempfile.TemporaryDirectory() as root:
            db_path = Path(root) / "state.db"
            wal_path = Path(f"{db_path}-wal")
            db_path.write_bytes(b"db-v1")
            wal_path.write_bytes(b"wal-v1")
            first_fingerprint = profiles._sidebar_db_fingerprint(db_path)
            first_key = (str(db_path), first_fingerprint, False, 0, (), 50, 100, ())
            payload = {"recents": None, "cron": [{"id": "one"}], "messaging": []}

            profiles._sidebar_profile_cache_put(first_key, payload)
            cached = profiles._sidebar_profile_cache_get(first_key)
            cached["cron"][0]["id"] = "mutated"
            self.assertEqual(
                profiles._sidebar_profile_cache_get(first_key)["cron"][0]["id"],
                "one",
            )

            wal_path.write_bytes(b"wal-v2-is-different")
            second_fingerprint = profiles._sidebar_db_fingerprint(db_path)
            second_key = (str(db_path), second_fingerprint, False, 0, (), 50, 100, ())
            self.assertNotEqual(first_fingerprint, second_fingerprint)
            self.assertIsNone(profiles._sidebar_profile_cache_get(second_key))

            profiles._sidebar_profile_cache_put(second_key, payload)
            self.assertIsNone(profiles._sidebar_profile_cache_get(first_key))

    def test_profile_cache_is_lru_bounded(self):
        with mock.patch.object(profiles, "_SIDEBAR_PROFILE_CACHE_MAX_ENTRIES", 2):
            for index in range(3):
                key = (f"/db/{index}", (index, None), False, 0, (), 50, 100, ())
                profiles._sidebar_profile_cache_put(key, {"index": index})
            self.assertEqual(len(profiles._SIDEBAR_PROFILE_CACHE), 2)

    def test_applies_defaults_and_returns_defensive_copies(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan(profile="all", limit=20):
            nonlocal calls
            calls += 1
            return {"profile": profile, "rows": [{"limit": limit}]}

        first = scan()
        first["rows"][0]["limit"] = 999
        second = scan(profile="all", limit=20)

        self.assertEqual(calls, 1)
        self.assertEqual(second, {"profile": "all", "rows": [{"limit": 20}]})

    def test_coalesces_concurrent_identical_scans(self):
        workers = 12
        start = threading.Barrier(workers + 1)
        entered = threading.Event()
        release = threading.Event()
        calls = 0
        calls_lock = threading.Lock()

        @profiles._sidebar_singleflight_cache
        def scan(profile="all"):
            nonlocal calls
            with calls_lock:
                calls += 1
            entered.set()
            self.assertTrue(release.wait(timeout=_BURST_WAIT_SECONDS))
            return {"profile": profile, "rows": []}

        def caller():
            # Rendezvous, not a sleep: every caller is scheduled and about to call the
            # wrapped scan before any of them may return, so the burst cannot degrade
            # into "the first caller finished and the rest took the cache".
            start.wait(timeout=_BURST_WAIT_SECONDS)
            return scan("default")

        with mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", _BURST_TTL_SECONDS), \
                ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(caller) for _ in range(workers)]
            start.wait(timeout=_BURST_WAIT_SECONDS)
            self.assertTrue(entered.wait(timeout=_BURST_WAIT_SECONDS))
            time.sleep(0.05)
            release.set()
            results = [future.result(timeout=_BURST_WAIT_SECONDS) for future in futures]

        self.assertEqual(calls, 1)
        self.assertEqual(results, [{"profile": "default", "rows": []}] * workers)

    def test_expires(self):
        clock = iter((100.0, 100.0, 100.0, 106.0, 106.0, 106.0))
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            return {"generation": calls}

        with mock.patch.object(profiles.time, "monotonic", side_effect=clock):
            self.assertEqual(scan(), {"generation": 1})
            self.assertEqual(scan(), {"generation": 2})
        self.assertEqual(calls, 2)

    def test_does_not_cache_failures(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("transient")
            return {"ok": True}

        with self.assertRaisesRegex(RuntimeError, "transient"):
            scan()
        self.assertEqual(scan(), {"ok": True})
        self.assertEqual(scan(), {"ok": True})
        self.assertEqual(calls, 2)

    def test_does_not_cache_payloads_that_carry_profile_errors(self):
        # A 200 with a non-empty errors[] is how a failed profile scan is
        # reported. Caching it for the TTL keeps the empty recents page in
        # front of a store that has already recovered.
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            if calls == 1:
                return {
                    "errors": [{"profile": "default", "error": "disk I/O error"}],
                    "recents": {"sessions": []},
                }
            return {"errors": [], "recents": {"sessions": [{"id": "yesterday"}]}}

        first = scan()
        second = scan()

        self.assertEqual(first["errors"][0]["error"], "disk I/O error")
        self.assertEqual(second["recents"]["sessions"], [{"id": "yesterday"}])
        self.assertEqual(calls, 2)

    def test_can_be_disabled(self):
        calls = 0

        @profiles._sidebar_singleflight_cache
        def scan():
            nonlocal calls
            calls += 1
            return calls

        with mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", 0.0):
            self.assertEqual((scan(), scan()), (1, 2))

    def test_preserves_fastapi_signature(self):
        def scan(profile: str = "all", limit: int = 20):
            return profile, limit

        wrapped = profiles._sidebar_singleflight_cache(scan)

        self.assertEqual(inspect.signature(wrapped), inspect.signature(scan))

    def test_projects_tree_coalesces_concurrent_scans_and_returns_copies(self):
        # /api/profiles/projects/tree fans out over every profile's state.db; desktop
        # background sync + sidebar refreshes overlap identical requests. One scan must
        # serve the whole burst, and no two callers may share the same payload object.
        workers = 8
        start = threading.Barrier(workers + 1)
        entered = threading.Event()
        release = threading.Event()
        scans = 0
        scans_lock = threading.Lock()

        def fake_read(name, home, errors, fn):
            nonlocal scans
            with scans_lock:
                scans += 1
            entered.set()
            self.assertTrue(release.wait(timeout=_BURST_WAIT_SECONDS))
            return None

        def caller():
            # Rendezvous, not a sleep (see _BURST_WAIT_SECONDS): all eight callers are
            # live before the scan in flight is allowed to complete, so scans == 1 is
            # the single-flight guarantee rather than a scheduling coincidence.
            start.wait(timeout=_BURST_WAIT_SECONDS)
            return profiles.get_profiles_projects_tree()

        with mock.patch.object(profiles, "_profile_targets", return_value=[("default", Path("/nonexistent"))]), \
                mock.patch.object(profiles, "_read_profile_db", side_effect=fake_read), \
                mock.patch.object(profiles, "_SIDEBAR_CACHE_TTL_SECONDS", _BURST_TTL_SECONDS), \
                ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(caller) for _ in range(workers)]
            start.wait(timeout=_BURST_WAIT_SECONDS)
            self.assertTrue(entered.wait(timeout=_BURST_WAIT_SECONDS))
            time.sleep(0.05)
            release.set()
            results = [future.result(timeout=_BURST_WAIT_SECONDS) for future in futures]

        self.assertEqual(scans, 1)
        self.assertEqual(len({id(r) for r in results}), workers)
        self.assertEqual(results, [results[0]] * workers)
        self.assertEqual(results[0]["projects"], [])


if __name__ == "__main__":
    unittest.main()
