"""Verify ``datetime.fromisoformat`` → ``ciso8601`` migration correctness.

Uses the ``parse_iso_datetime`` wrapper from ``hermes_time`` which uses
``ciso8601`` when available, falling back to ``datetime.fromisoformat``.
"""

from datetime import datetime, timezone, timedelta
import pytest

from hermes_time import parse_iso_datetime, _HAS_CISO8601

# ── ISO 8601 variants ───────────────────────────────────────────────────

# (input_string, year, month, day, hour, minute, second, microsecond, tz_offset)
ISO8601_CASES = [
    ("2024-01-15T10:30:00+00:00", 2024, 1, 15, 10, 30, 0, 0, timedelta(0)),
    ("2024-01-15T10:30:00Z", 2024, 1, 15, 10, 30, 0, 0, timedelta(0)),
    ("2024-01-15T10:30:00.123456+00:00", 2024, 1, 15, 10, 30, 0, 123456, timedelta(0)),
    ("2024-01-15T10:30:00", 2024, 1, 15, 10, 30, 0, 0, None),
    ("2024-01-15T10:30:00+05:30", 2024, 1, 15, 10, 30, 0, 0, timedelta(hours=5, minutes=30)),
    ("2024-01-15T10:30:00-05:00", 2024, 1, 15, 10, 30, 0, 0, timedelta(hours=-5)),
    ("2024-01-15", 2024, 1, 15, 0, 0, 0, 0, None),
    ("20240115T103000Z", 2024, 1, 15, 10, 30, 0, 0, timedelta(0)),
    ("2024-01-15T10:30:00.000001+00:00", 2024, 1, 15, 10, 30, 0, 1, timedelta(0)),
    ("2024-01-15T10:30:00.000000+00:00", 2024, 1, 15, 10, 30, 0, 0, timedelta(0)),
]


class TestCiso8601Migration:
    """Verify parse_iso_datetime handles all ISO 8601 variants."""

    def test_parse_variants(self):
        for input_str, y, M, d, h, m, s, us, offset in ISO8601_CASES:
            dt = parse_iso_datetime(input_str)
            assert dt.year == y, f"{input_str!r}: year {dt.year} != {y}"
            assert dt.month == M, f"{input_str!r}: month {dt.month} != {M}"
            assert dt.day == d, f"{input_str!r}: day {dt.day} != {d}"
            assert dt.hour == h, f"{input_str!r}: hour {dt.hour} != {h}"
            assert dt.minute == m, f"{input_str!r}: minute {dt.minute} != {m}"
            assert dt.second == s, f"{input_str!r}: second {dt.second} != {s}"
            assert dt.microsecond == us, f"{input_str!r}: microsecond {dt.microsecond} != {us}"
            if offset is None:
                assert dt.tzinfo is None, f"{input_str!r}: expected naive, got {dt.tzinfo}"
            else:
                assert dt.tzinfo is not None, f"{input_str!r}: expected tz-aware"
                assert dt.utcoffset() == offset, f"{input_str!r}: offset {dt.utcoffset()} != {offset}"

    def test_parse_returns_datetime(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00Z")
        assert isinstance(dt, datetime)

    def test_parse_timezone_aware(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00+00:00")
        assert dt.tzinfo is not None

    def test_parse_timezone_naive(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00")
        assert dt.tzinfo is None

    def test_parse_utc_equivalent(self):
        dt_z = parse_iso_datetime("2024-01-15T10:30:00Z")
        dt_offset = parse_iso_datetime("2024-01-15T10:30:00+00:00")
        assert dt_z == dt_offset

    def test_parse_positive_offset(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00+05:30")
        assert dt.utcoffset() == timedelta(hours=5, minutes=30)

    def test_parse_negative_offset(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00-05:00")
        assert dt.utcoffset() == timedelta(hours=-5)

    def test_parse_with_microseconds(self):
        dt = parse_iso_datetime("2024-01-15T10:30:00.123456+00:00")
        assert dt.microsecond == 123456

    def test_parse_compact(self):
        """Compact ISO 8601 format (no separators)."""
        dt = parse_iso_datetime("20240115T103000Z")
        assert dt.year == 2024
        assert dt.month == 1
        assert dt.day == 15
        assert dt.hour == 10
        assert dt.minute == 30
        assert dt.second == 0

    def test_error_invalid_date(self):
        with pytest.raises(Exception):
            parse_iso_datetime("not-a-date")

    def test_error_empty_string(self):
        with pytest.raises(Exception):
            parse_iso_datetime("")


@pytest.mark.skipif(not _HAS_CISO8601, reason="ciso8601 not installed")
class TestCiso8601Specific:
    """Tests that verify ciso8601 is actually being used."""

    def test_ciso8601_imported(self):
        assert _HAS_CISO8601
        import ciso8601
        assert ciso8601 is not None
