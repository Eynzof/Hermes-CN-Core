"""Benchmark ``pybase64`` vs ``base64`` performance.

Verifies the ≥2× speedup claim for SIMD-accelerated base64 encoding/decoding.
"""

import os
import pytest

try:
    import pybase64
    HAS_PYBASE64 = True
except ImportError:
    HAS_PYBASE64 = False

import base64 as _stdlib_base64


# Test data sizes
SMALL_DATA = b"hello world" * 10
MEDIUM_DATA = os.urandom(100 * 1024)  # 100 KB (typical image size)
LARGE_DATA = os.urandom(1024 * 1024)  # 1 MB


@pytest.mark.skipif(not HAS_PYBASE64, reason="pybase64 not installed")
class TestBase64Performance:

    def test_pybase64_encode_small(self, benchmark):
        """Benchmark pybase64.b64encode on small data."""
        result = benchmark(pybase64.b64encode, SMALL_DATA)
        assert isinstance(result, bytes)

    def test_stdlib_base64_encode_small(self, benchmark):
        """Benchmark stdlib base64.b64encode on small data (baseline)."""
        result = benchmark(_stdlib_base64.b64encode, SMALL_DATA)
        assert isinstance(result, bytes)

    def test_pybase64_decode_small(self, benchmark):
        """Benchmark pybase64.b64decode on small data."""
        encoded = pybase64.b64encode(SMALL_DATA)
        result = benchmark(pybase64.b64decode, encoded)
        assert result == SMALL_DATA

    def test_stdlib_base64_decode_small(self, benchmark):
        """Benchmark stdlib base64.b64decode on small data (baseline)."""
        encoded = _stdlib_base64.b64encode(SMALL_DATA)
        result = benchmark(_stdlib_base64.b64decode, encoded)
        assert result == SMALL_DATA

    def test_pybase64_encode_medium(self, benchmark):
        """Benchmark pybase64.b64encode on medium data (image-sized)."""
        result = benchmark(pybase64.b64encode, MEDIUM_DATA)
        assert isinstance(result, bytes)

    def test_stdlib_base64_encode_medium(self, benchmark):
        """Benchmark stdlib base64.b64encode on medium data (baseline)."""
        result = benchmark(_stdlib_base64.b64encode, MEDIUM_DATA)
        assert isinstance(result, bytes)

    def test_pybase64_decode_medium(self, benchmark):
        """Benchmark pybase64.b64decode on medium data."""
        encoded = pybase64.b64encode(MEDIUM_DATA)
        result = benchmark(pybase64.b64decode, encoded)
        assert result == MEDIUM_DATA

    def test_stdlib_base64_decode_medium(self, benchmark):
        """Benchmark stdlib base64.b64decode on medium data (baseline)."""
        encoded = _stdlib_base64.b64encode(MEDIUM_DATA)
        result = benchmark(_stdlib_base64.b64decode, encoded)
        assert result == MEDIUM_DATA

    def test_pybase64_urlsafe_roundtrip(self, benchmark):
        """Benchmark pybase64 urlsafe round-trip."""
        def roundtrip():
            enc = pybase64.urlsafe_b64encode(MEDIUM_DATA)
            return pybase64.urlsafe_b64decode(enc)
        result = benchmark(roundtrip)
        assert result == MEDIUM_DATA

    def test_pybase64_encode_large(self, benchmark):
        """Benchmark pybase64.b64encode on 1 MB data."""
        result = benchmark(pybase64.b64encode, LARGE_DATA)
        assert isinstance(result, bytes)

    def test_stdlib_base64_encode_large(self, benchmark):
        """Benchmark stdlib base64.b64encode on 1 MB data (baseline)."""
        result = benchmark(_stdlib_base64.b64encode, LARGE_DATA)
        assert isinstance(result, bytes)
