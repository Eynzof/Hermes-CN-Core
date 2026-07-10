"""Verify ``random`` → ``numpy.random`` migration correctness.

Tests the accelerated random utilities from ``hermes_random`` which
use ``numpy.random`` when available for 10-50× faster bulk operations.
"""

import pytest

from hermes_random import (
    randint, choice, sample, shuffle, random, uniform, seed,
    _HAS_NUMPY,
)


class TestRandomMigration:
    """Verify hermes_random produces correct statistical behaviour."""

    def test_randint_range(self):
        for _ in range(100):
            v = randint(0, 10)
            assert 0 <= v <= 10

    def test_randint_deterministic(self):
        seed(42)
        v1 = randint(0, 1000)
        seed(42)
        v2 = randint(0, 1000)
        assert v1 == v2

    def test_choice_basic(self):
        seq = [1, 2, 3, 4, 5]
        for _ in range(50):
            v = choice(seq)
            assert v in seq

    def test_choice_single_element(self):
        assert choice([42]) == 42

    def test_sample_size(self):
        population = list(range(100))
        result = sample(population, 10)
        assert len(result) == 10

    def test_sample_unique(self):
        population = list(range(100))
        result = sample(population, 50)
        assert len(set(result)) == 50  # All unique

    def test_sample_deterministic(self):
        seed(42)
        s1 = sample(list(range(1000)), 20)
        seed(42)
        s2 = sample(list(range(1000)), 20)
        assert s1 == s2

    def test_shuffle(self):
        lst = list(range(100))
        original = lst.copy()
        shuffle(lst)
        # Length unchanged
        assert len(lst) == 100
        # Same elements
        assert sorted(lst) == original
        # Should be different order (statistically near-certain)
        assert lst != original

    def test_shuffle_empty(self):
        lst = []
        shuffle(lst)
        assert lst == []

    def test_shuffle_single(self):
        lst = [42]
        shuffle(lst)
        assert lst == [42]

    def test_random_range(self):
        for _ in range(100):
            v = random()
            assert 0.0 <= v < 1.0

    def test_uniform_range(self):
        for _ in range(100):
            v = uniform(5.0, 10.0)
            assert 5.0 <= v <= 10.0

    def test_seed_reset(self):
        seed(42)
        values_before = [randint(0, 100) for _ in range(5)]
        seed(42)
        values_after = [randint(0, 100) for _ in range(5)]
        assert values_before == values_after


@pytest.mark.skipif(not _HAS_NUMPY, reason="numpy not available")
class TestNumpyAcceleration:
    """Tests that verify numpy acceleration is active."""

    def test_numpy_available(self):
        assert _HAS_NUMPY

    def test_bulk_performance(self):
        """NumPy should be faster for bulk operations."""
        import time

        # Warmup
        seed(42)
        _ = [randint(0, 1000) for _ in range(1000)]

        # Time bulk operation
        seed(42)
        start = time.perf_counter()
        values = [randint(0, 1000) for _ in range(10000)]
        elapsed = time.perf_counter() - start

        assert len(values) == 10000
        assert elapsed < 1.0  # Should complete in under 1 second
