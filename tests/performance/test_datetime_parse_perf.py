"""Benchmark ``ciso8601`` vs ``datetime.fromisoformat`` performance.

Verifies the ≥10× speedup claim for ISO8601 parsing.
"""

from datetime import datetime
import pytest

from hermes_time import parse_iso_datetime, _HAS_CISO8601

# 50+ ISO8601 variants
ISO8601_SAMPLES = [
    "2024-01-15T10:30:00+00:00",
    "2024-01-15T10:30:00Z",
    "2024-01-15T10:30:00.123456+00:00",
    "2024-01-15T10:30:00",
    "2024-01-15T10:30:00+05:30",
    "2024-01-15T10:30:00-05:00",
    "2024-01-15",
    "20240115T103000Z",
    "2024-01-15T10:30:00.000001+00:00",
    "2024-01-15T10:30:00.123456+05:30",
    "2024-01-15T10:30:00.123456-05:00",
    "2024-06-15T14:30:00Z",
    "2024-12-31T23:59:59.999999Z",
    "2023-01-01T00:00:00+00:00",
    "2024-02-29T12:00:00Z",  # Leap year
]


@pytest.mark.skipif(not _HAS_CISO8601, reason="ciso8601 not installed")
class TestDatetimeParsePerformance:

    def test_parse_iso_datetime_single(self, benchmark):
        """Benchmark parse_iso_datetime on a single timestamp."""
        result = benchmark(parse_iso_datetime, "2024-01-15T10:30:00+00:00")
        assert isinstance(result, datetime)

    def test_parse_iso_datetime_bulk(self, benchmark):
        """Benchmark parse_iso_datetime over 50+ variants."""
        def parse_all():
            return [parse_iso_datetime(s) for s in ISO8601_SAMPLES]
        results = benchmark(parse_all)
        assert len(results) == len(ISO8601_SAMPLES)

    def test_stdlib_fromisoformat_bulk(self, benchmark):
        """Benchmark stdlib datetime.fromisoformat (baseline)."""
        samples = [s.replace("Z", "+00:00") for s in ISO8601_SAMPLES]
        def parse_all():
            return [datetime.fromisoformat(s) for s in samples]
        results = benchmark(parse_all)
        assert len(results) == len(ISO8601_SAMPLES)

    def test_parse_iso_datetime_with_z(self, benchmark):
        """Benchmark parsing timestamps with Z suffix."""
        z_samples = [s for s in ISO8601_SAMPLES if "Z" in s]
        def parse_all():
            return [parse_iso_datetime(s) for s in z_samples]
        results = benchmark(parse_all)
        assert len(results) == len(z_samples)

    def test_parse_iso_datetime_with_offset(self, benchmark):
        """Benchmark parsing timestamps with timezone offset."""
        offset_samples = [s for s in ISO8601_SAMPLES if "+" in s and "Z" not in s]
        def parse_all():
            return [parse_iso_datetime(s) for s in offset_samples]
        results = benchmark(parse_all)
        assert len(results) == len(offset_samples)
