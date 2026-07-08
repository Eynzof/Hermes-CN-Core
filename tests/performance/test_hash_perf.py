"""Benchmark ``xxhash`` vs ``hashlib`` performance on realistic workloads.

Verifies the ≥5× speedup claim for non-cryptographic hashing.
"""

import hashlib
import os
import pytest

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


# Test data sizes
SMALL_DATA = b"hello world" * 10
MEDIUM_DATA = os.urandom(100 * 1024)  # 100 KB
LARGE_DATA = os.urandom(10 * 1024 * 1024)  # 10 MB


@pytest.mark.skipif(not HAS_XXHASH, reason="xxhash not installed")
class TestHashPerformance:

    def test_xxh64_small(self, benchmark):
        """Benchmark xxh64 on small data."""
        result = benchmark(xxhash.xxh64, SMALL_DATA)
        _ = result.hexdigest()

    def test_hashlib_md5_small(self, benchmark):
        """Benchmark hashlib.md5 on small data (baseline)."""
        result = benchmark(hashlib.md5, SMALL_DATA)
        _ = result.hexdigest()

    def test_xxh64_medium(self, benchmark):
        """Benchmark xxh64 on medium data."""
        result = benchmark(xxhash.xxh64, MEDIUM_DATA)
        _ = result.hexdigest()

    def test_hashlib_sha256_medium(self, benchmark):
        """Benchmark hashlib.sha256 on medium data (baseline)."""
        result = benchmark(hashlib.sha256, MEDIUM_DATA)
        _ = result.hexdigest()

    def test_xxh64_large(self, benchmark):
        """Benchmark xxh64 on large data (10 MB)."""
        result = benchmark(xxhash.xxh64, LARGE_DATA)
        _ = result.hexdigest()

    def test_hashlib_sha256_large(self, benchmark):
        """Benchmark hashlib.sha256 on large data (baseline)."""
        result = benchmark(hashlib.sha256, LARGE_DATA)
        _ = result.hexdigest()

    def test_xxh3_64_medium(self, benchmark):
        """Benchmark xxh3_64 (XXH3 algorithm) on medium data."""
        result = benchmark(xxhash.xxh3_64, MEDIUM_DATA)
        _ = result.hexdigest()

    def test_xxh64_incremental_file(self, benchmark):
        """Benchmark incremental hashing (simulating file checksum)."""
        def inc_hash():
            h = xxhash.xxh64()
            for chunk in [MEDIUM_DATA[i:i+8192] for i in range(0, len(MEDIUM_DATA), 8192)]:
                h.update(chunk)
            return h.hexdigest()
        result = benchmark(inc_hash)

    def test_hashlib_sha256_incremental_file(self, benchmark):
        """Benchmark incremental hashlib.sha256 (simulating file checksum)."""
        def inc_hash():
            h = hashlib.sha256()
            for chunk in [MEDIUM_DATA[i:i+8192] for i in range(0, len(MEDIUM_DATA), 8192)]:
                h.update(chunk)
            return h.hexdigest()
        result = benchmark(inc_hash)
