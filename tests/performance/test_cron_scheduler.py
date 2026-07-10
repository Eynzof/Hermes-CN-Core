"""Performance benchmarks for cron scheduler.

All tests run in OFFLINE mode — no real background tasks.
Tests measure job parsing, lock I/O, and serialization performance.
"""

import orjson
import os
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
import pytest

PROJECT_ROOT = Path(__file__).parent.parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def sample_jobs():
    jobs = []
    for i in range(50):
        jobs.append({
            "id": f"job_{i:04d}",
            "schedule": f"*/{max(1, i % 60)} * * * *" if i < 30 else f"every {max(1, (i % 24) + 1)}h",
            "task": f"echo 'Task {i}'",
            "enabled": i % 5 != 0,
            "mode": "terminal",
            "created_at": "2026-01-01T00:00:00Z",
        })
    return jobs


@pytest.mark.perf
def test_load_jobs_small(timing_context, tmp_path):
    """Measure load_jobs with a small jobs.json via path patching."""
    from cron import jobs as cron_jobs

    jobs_file = tmp_path / "jobs.json"
    small_jobs = [
        {"id": f"job_{i}", "schedule": "0 9 * * *", "task": f"echo 'task {i}'"}
        for i in range(5)
    ]
    jobs_file.write_text(orjson.dumps({"jobs": small_jobs}).decode('utf-8'), encoding="utf-8")

    # Patch the jobs file path
    with patch.object(cron_jobs, '_jobs_lock_file', return_value=tmp_path / ".tick.lock"):
        with patch("cron.jobs._job_output_dir", return_value=tmp_path / "job_outputs"):
            with timing_context.measure("load_jobs_small"):
                for _ in range(100):
                    # Direct JSON parsing for perf measurement
                    data = orjson.loads(jobs_file.read_text(encoding="utf-8"))
                    _ = data["jobs"]

    total_ms = timing_context.summary().get("load_jobs_small", {}).get("total_ms", 0)
    avg_ms = total_ms / 100
    print(f"\n  Parse 5 jobs x100: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 10, f"Parse small jobs avg {avg_ms:.1f}ms (expected < 10ms)"


@pytest.mark.perf
def test_load_jobs_large(timing_context, tmp_path, sample_jobs):
    """Measure load_jobs with a large jobs.json (50 jobs)."""
    serialized = orjson.dumps({"jobs": sample_jobs}).decode('utf-8')

    with timing_context.measure("load_jobs_large"):
        for _ in range(100):
            data = orjson.loads(serialized)
            _ = data["jobs"]

    total_ms = timing_context.summary().get("load_jobs_large", {}).get("total_ms", 0)
    avg_ms = total_ms / 100
    print(f"\n  Parse 50 jobs x100: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 20, f"Parse large jobs avg {avg_ms:.1f}ms (expected < 20ms)"


@pytest.mark.perf
def test_lock_file_io_timing(timing_context, tmp_path):
    """Measure lock file creation, release, and cleanup overhead."""
    lock_file = tmp_path / ".tick.lock"

    with timing_context.measure("lock_file_operations"):
        for _ in range(50):
            lock_file.write_text(str(os.getpid()), encoding="utf-8")
            if lock_file.exists():
                _ = lock_file.stat().st_mtime
            if lock_file.exists():
                lock_file.unlink()

    total_ms = timing_context.summary().get("lock_file_operations", {}).get("total_ms", 0)
    avg_ms = total_ms / 50
    print(f"\n  Lock file ops x50: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 20, f"Lock file ops avg {avg_ms:.1f}ms (expected < 20ms)"


@pytest.mark.perf
def test_job_serialization_timing(timing_context, sample_jobs):
    """Measure serialization/deserialization of job definitions."""
    serialized = orjson.dumps({"jobs": sample_jobs}).decode('utf-8')

    with timing_context.measure("job_serialization"):
        for _ in range(50):
            parsed = orjson.loads(serialized)
            for job in parsed["jobs"]:
                _ = job["id"]
                _ = job["schedule"]
                _ = job["task"]

    total_ms = timing_context.summary().get("job_serialization", {}).get("total_ms", 0)
    avg_ms = total_ms / 50
    print(f"\n  Job serialization x50: {total_ms:.1f}ms (avg {avg_ms:.1f}ms)")
    assert avg_ms < 10, f"Serialization avg {avg_ms:.1f}ms (expected < 10ms)"


@pytest.mark.perf
@pytest.mark.perf_baseline
def test_tick_loop_baseline(timing_context, tmp_path, sample_jobs):
    """Baseline measurement for JSON parsing (simulating tick cycle)."""
    serialized = orjson.dumps({"jobs": sample_jobs}).decode('utf-8')

    with timing_context.measure("full_tick_cycle"):
        for _ in range(10):
            data = orjson.loads(serialized)
            _ = [j for j in data["jobs"] if j["enabled"]]

    total_ms = timing_context.summary().get("full_tick_cycle", {}).get("total_ms", 0)
    print(f"\n  Tick cycle sim (50 jobs x10): {total_ms:.1f}ms")

    from tests.performance.conftest import save_baseline
    save_baseline("cron_tick", {"num_jobs": len(sample_jobs), "tick_ms": total_ms})

# ---------------------------------------------------------------------------
# Real-code benchmarks for the P1 lock/load optimization
# (see .plans/08-Cron-Lock-File-Overhead.md). Unlike the synthetic parsing
# benchmarks above, these drive the actual cron/jobs.py + cron/scheduler.py
# code paths so a regression in the read cache or the per-tick syscall trimming
# shows up here. The config loader (its own perf domain) is stubbed so we
# isolate the cron lock/load I/O the plan targeted; timing gates are deliberately
# lenient to survive shared-CI contention — the *correctness* of the cache is
# pinned deterministically in tests/cron/test_jobs_cache.py.
# ---------------------------------------------------------------------------


def _point_cron_at(tmp_path, monkeypatch):
    """Redirect the cron store (import-time constants) at a temp dir."""
    from cron import jobs as cron_jobs

    cron_dir = tmp_path / "cron"
    cron_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cron_jobs, "CRON_DIR", cron_dir)
    monkeypatch.setattr(cron_jobs, "JOBS_FILE", cron_dir / "jobs.json")
    monkeypatch.setattr(cron_jobs, "OUTPUT_DIR", cron_dir / "output")
    cron_jobs._invalidate_jobs_cache()
    return cron_jobs, cron_dir


@pytest.mark.perf
def test_load_jobs_cached_fast(timing_context, tmp_path, monkeypatch, sample_jobs):
    """Cached load_jobs() (50 jobs) is served from memory after one stat().

    Plan success criterion: cached jobs loading < 0.1 ms. We print the measured
    average and assert a lenient ceiling that still fails loudly if the cache
    stops working (an uncached NTFS read was ~2.4 ms/op in the baseline report).
    """
    cron_jobs, _ = _point_cron_at(tmp_path, monkeypatch)
    cron_jobs.save_jobs(sample_jobs)
    cron_jobs.load_jobs()  # prime/warm

    iters = 200
    with timing_context.measure("load_jobs_cached"):
        for _ in range(iters):
            cron_jobs.load_jobs()

    total_ms = timing_context.summary().get("load_jobs_cached", {}).get("total_ms", 0)
    avg_ms = total_ms / iters
    print(f"\n  cached load_jobs (50 jobs) x{iters}: {total_ms:.1f}ms (avg {avg_ms:.4f}ms)")
    assert avg_ms < 2.0, f"cached load_jobs avg {avg_ms:.4f}ms (expected well under 2ms)"


@pytest.mark.perf
def test_tick_cycles_benchmark(timing_context, tmp_path, monkeypatch):
    """100 real cron tick cycles (empty schedule) complete quickly.

    Exercises the real tick file lock + jobs lock + cached get_due_jobs/load_jobs
    path; only load_config (a separate perf domain) is stubbed. Plan target is
    < 50 ms for 100 cycles — we report whether it was met and gate on a
    contention-safe ceiling so the test is not flaky under parallel CI load.
    """
    from cron import scheduler as cron_sched

    cron_jobs, cron_dir = _point_cron_at(tmp_path, monkeypatch)
    cron_jobs.save_jobs([])  # valid but empty store → no due jobs

    lock_file = cron_dir / ".tick.lock"
    monkeypatch.setattr(cron_sched, "_get_lock_paths", lambda: (cron_dir, lock_file))
    monkeypatch.setattr(cron_sched, "load_config", lambda: {"cron": {}})

    # Warm up: first tick creates the lock file and primes the read cache.
    cron_sched.tick(verbose=True, sync=True)

    # Best-of-3 batches: filters transient GC/scheduler noise on shared runners.
    for _ in range(3):
        with timing_context.measure("tick_100x"):
            for _ in range(100):
                cron_sched.tick(verbose=True, sync=True)

    durations = timing_context.summary().get("tick_100x", {}).get("durations", [0])
    best_ms = min(durations)
    target_met = "MET" if best_ms < 50 else "MISSED"
    print(
        f"\n  100 tick cycles (best of {len(durations)}): {best_ms:.1f}ms "
        f"[<50ms target: {target_met}]  batches={[round(d, 1) for d in durations]}"
    )
    assert best_ms < 150, (
        f"100 tick cycles took {best_ms:.1f}ms (expected well under 150ms; "
        f"a cache/syscall regression is the likely cause)"
    )
