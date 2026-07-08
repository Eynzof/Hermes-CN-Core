"""Zstandard and LZ4 compression utilities for Hermes.

Provides drop-in replacements for common compression patterns,
offering better compression ratios AND faster decompression than gzip/zlib.

Usage:
    from hermes_compress import zstd_compress, zstd_decompress
    data = zstd_compress(b"large data...")
    original = zstd_decompress(data)
"""

import os as _os
from typing import Optional

_HAS_ZSTD = False
_HAS_LZ4 = False

if not _os.environ.get("HERMES_DISABLE_ZSTD_REPLACEMENT"):
    try:
        import zstandard as _zstd
        _HAS_ZSTD = True
    except ImportError:
        pass

if not _os.environ.get("HERMES_DISABLE_LZ4_REPLACEMENT"):
    try:
        import lz4.frame as _lz4frame
        _HAS_LZ4 = True
    except ImportError:
        pass


def zstd_compress(data: bytes, level: int = 6) -> bytes:
    """Compress *data* using zstandard (zstd).

    Falls back to gzip if zstandard is not available.
    """
    if _HAS_ZSTD:
        ctx = _zstd.ZstdCompressor(level=level)
        return ctx.compress(data)
    import gzip
    return gzip.compress(data, compresslevel=level)


def zstd_decompress(data: bytes) -> bytes:
    """Decompress *data* using zstandard (zstd).

    Falls back to gzip if zstandard is not available.
    """
    if _HAS_ZSTD:
        ctx = _zstd.ZstdDecompressor()
        return ctx.decompress(data)
    import gzip
    return gzip.decompress(data)


def lz4_compress(data: bytes, level: int = 6) -> bytes:
    """Compress *data* using LZ4 (fastest decompression).

    Falls back to gzip if LZ4 is not available.
    """
    if _HAS_LZ4:
        return _lz4frame.compress(data, compression_level=level)
    import gzip
    return gzip.compress(data, compresslevel=level)


def lz4_decompress(data: bytes) -> bytes:
    """Decompress *data* using LZ4.

    Falls back to gzip if LZ4 is not available.
    """
    if _HAS_LZ4:
        return _lz4frame.decompress(data)
    import gzip
    return gzip.decompress(data)


def open_for_read(path: str) -> "tuple[bytes, str]":
    """Read a file, auto-detecting compression from the extension.

    Supports ``.zst`` (zstandard), ``.lz4`` (LZ4), ``.gz`` (gzip),
    and uncompressed files.

    Returns ``(data, detected_format)``.
    """
    if path.endswith(".zst"):
        with open(path, "rb") as f:
            return zstd_decompress(f.read()), "zstd"
    elif path.endswith(".lz4"):
        with open(path, "rb") as f:
            return lz4_decompress(f.read()), "lz4"
    elif path.endswith(".gz"):
        import gzip
        with gzip.open(path, "rb") as f:
            return f.read(), "gzip"
    else:
        with open(path, "rb") as f:
            return f.read(), "none"
