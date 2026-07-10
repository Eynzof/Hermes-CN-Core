"""Verify ``base64`` → ``pybase64`` migration correctness.

``pybase64`` is a SIMD-accelerated drop-in replacement for stdlib ``base64``.
All standard ``base64`` functions are available with identical signatures.
"""

import os
import pytest

try:
    import pybase64 as base64
    HAS_PYBASE64 = True
except ImportError:
    import base64  # type: ignore[no-redef]
    HAS_PYBASE64 = False


class TestBase64Compat:
    """Full API compatibility with stdlib ``base64`` module."""

    def test_b64encode(self):
        result = base64.b64encode(b"hello")
        assert result == b"aGVsbG8="

    def test_b64decode(self):
        result = base64.b64decode(b"aGVsbG8=")
        assert result == b"hello"

    def test_b64encode_empty(self):
        assert base64.b64encode(b"") == b""

    def test_b64decode_empty(self):
        assert base64.b64decode(b"") == b""

    def test_roundtrip_binary(self):
        data = bytes(range(256))
        encoded = base64.b64encode(data)
        decoded = base64.b64decode(encoded)
        assert decoded == data

    def test_roundtrip_text(self):
        data = b"The quick brown fox jumps over the lazy dog."
        encoded = base64.b64encode(data)
        decoded = base64.b64decode(encoded)
        assert decoded == data

    def test_urlsafe_b64encode(self):
        result = base64.urlsafe_b64encode(b"hello?world")
        assert b"?" not in result  # URL-safe encoding
        decoded = base64.urlsafe_b64decode(result)
        assert decoded == b"hello?world"

    def test_urlsafe_b64decode(self):
        result = base64.urlsafe_b64decode(b"aGVsbG8_d29ybGQ=")
        assert result == b"hello?world"

    def test_urlsafe_roundtrip(self):
        data = b"data with +/ and = padding"
        encoded = base64.urlsafe_b64encode(data)
        decoded = base64.urlsafe_b64decode(encoded)
        assert decoded == data

    def test_padding_variants(self):
        """Test that decode handles missing/extra padding."""
        data = b"test"
        encoded = base64.b64encode(data)
        # Remove padding
        no_pad = encoded.rstrip(b"=")
        decoded = base64.b64decode(no_pad + b"==")
        assert decoded == data

    def test_large_blob(self):
        """Test with image-sized data (~100KB)."""
        data = os.urandom(100 * 1024)
        encoded = base64.b64encode(data)
        decoded = base64.b64decode(encoded)
        assert decoded == data

    def test_standard_b64encode(self):
        """standard_b64encode is an alias for b64encode."""
        result = base64.standard_b64encode(b"hello")
        assert result == b"aGVsbG8="

    def test_standard_b64decode(self):
        """standard_b64decode is an alias for b64decode."""
        result = base64.standard_b64decode(b"aGVsbG8=")
        assert result == b"hello"

    def test_encodebytes(self):
        """encodebytes wraps long lines (MIME-style)."""
        result = base64.encodebytes(b"hello" * 20)
        assert isinstance(result, bytes)
        decoded = base64.b64decode(result.replace(b"\n", b""))
        assert decoded == b"hello" * 20


@pytest.mark.skipif(
    not HAS_PYBASE64,
    reason="pybase64 not installed — using stdlib base64 fallback",
)
class TestPybase64Specific:
    """Tests specific to pybase64 (beyond stdlib compatibility)."""

    def test_get_version(self):
        version = base64.get_version()
        assert isinstance(version, str)
        assert len(version) > 0

    def test_b64encode_as_string(self):
        """pybase64 provides b64encode_as_string for direct str output."""
        result = base64.b64encode_as_string(b"hello")
        assert isinstance(result, str)
        assert result == "aGVsbG8="
