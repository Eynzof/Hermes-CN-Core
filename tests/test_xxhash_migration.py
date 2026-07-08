"""Verify ``hashlib`` (non-crypto) → ``xxhash`` migration correctness.

``xxhash`` provides 5-10× faster non-cryptographic hashing and is used
for checksums, cache keys, and content deduplication.
"""

import os
import pytest

try:
    import xxhash
    HAS_XXHASH = True
except ImportError:
    HAS_XXHASH = False


@pytest.mark.skipif(not HAS_XXHASH, reason="xxhash not installed")
class TestXxhashMigration:
    """Verify xxhash produces deterministic, consistent hashes."""

    def test_xxh64_deterministic(self):
        h1 = xxhash.xxh64(b"hello world").hexdigest()
        h2 = xxhash.xxh64(b"hello world").hexdigest()
        assert h1 == h2

    def test_xxh64_different_inputs(self):
        h1 = xxhash.xxh64(b"hello").hexdigest()
        h2 = xxhash.xxh64(b"world").hexdigest()
        assert h1 != h2

    def test_xxh64_empty(self):
        result = xxhash.xxh64(b"").hexdigest()
        assert isinstance(result, str)
        assert len(result) == 16  # 64-bit = 16 hex chars

    def test_xxh64_intdigest(self):
        result = xxhash.xxh64(b"test").intdigest()
        assert isinstance(result, int)
        assert result > 0

    def test_xxh64_incremental(self):
        h = xxhash.xxh64()
        h.update(b"hello")
        h.update(b" ")
        h.update(b"world")
        incremental = h.hexdigest()

        direct = xxhash.xxh64(b"hello world").hexdigest()
        assert incremental == direct

    def test_xxh64_large_data(self):
        data = os.urandom(1024 * 1024)  # 1 MB
        h = xxhash.xxh64(data)
        result = h.hexdigest()
        assert isinstance(result, str)
        assert len(result) == 16

    def test_xxh3_64(self):
        """xxh3_64 is an improved 64-bit hash (XXH3 algorithm)."""
        result = xxhash.xxh3_64(b"test").hexdigest()
        assert isinstance(result, str)
        assert len(result) == 16

    def test_xxh3_64_deterministic(self):
        h1 = xxhash.xxh3_64(b"test data").hexdigest()
        h2 = xxhash.xxh3_64(b"test data").hexdigest()
        assert h1 == h2

    def test_xxh128(self):
        """xxh128 provides a 128-bit hash."""
        result = xxhash.xxh128(b"test").hexdigest()
        assert isinstance(result, str)
        assert len(result) == 32  # 128-bit = 32 hex chars

    def test_hash_truncation(self):
        """Verify truncation for short IDs (matching [:16] pattern)."""
        full = xxhash.xxh64(b"some-identifier").hexdigest()
        truncated = full[:16]
        assert len(truncated) == 16

        shorter = full[:12]
        assert len(shorter) == 12

    def test_replace_hashlib_md5_equivalent_length(self):
        """xxh64 hexdigest()[:12] matches the pattern used for hashlib.md5()[:12]."""
        value = b"some content for hashing"
        xx_result = xxhash.xxh64(value).hexdigest()[:12]
        assert len(xx_result) == 12
        assert isinstance(xx_result, str)

    def test_replace_hashlib_sha256_equivalent_length(self):
        """xxh64 hexdigest()[:16] matches the pattern used for hashlib.sha256()[:16]."""
        value = b"some content for hashing"
        xx_result = xxhash.xxh64(value).hexdigest()[:16]
        assert len(xx_result) == 16
        assert isinstance(xx_result, str)
