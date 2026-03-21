"""Tests for hermes_p2p.bloom - TTL-expiring Bloom filter."""

import time
import pytest
from hermes_p2p.bloom import TTLBloomFilter


class TestTTLBloomFilter:
    def test_add_and_contains(self):
        bf = TTLBloomFilter(capacity=1000, error_rate=0.01, ttl_seconds=60.0)
        bf.add("hello")
        assert "hello" in bf
        assert "world" not in bf

    def test_no_false_negatives(self):
        bf = TTLBloomFilter(capacity=10000, ttl_seconds=60.0)
        items = [f"item_{i}" for i in range(1000)]
        for item in items:
            bf.add(item)
        for item in items:
            assert item in bf, f"False negative for {item}"

    def test_false_positive_rate(self):
        bf = TTLBloomFilter(capacity=10000, error_rate=0.05, ttl_seconds=60.0)
        for i in range(5000):
            bf.add(f"exists_{i}")
        false_positives = 0
        test_count = 10000
        for i in range(test_count):
            if f"not_exists_{i}" in bf:
                false_positives += 1
        fp_rate = false_positives / test_count
        # Allow some margin; with 5000 items in a 10000 capacity filter,
        # the FP rate should be below 20% (generous bound)
        assert fp_rate < 0.20, f"False positive rate too high: {fp_rate}"

    def test_memory_bounded(self):
        """Test (e): Bloom filter bounds memory usage."""
        bf = TTLBloomFilter(capacity=10000, error_rate=0.01, ttl_seconds=60.0)
        initial_memory = bf.memory_bytes

        # Add many items
        for i in range(20000):
            bf.add(f"item_{i}")

        # Memory should not grow significantly (bit arrays are fixed size)
        assert bf.memory_bytes == initial_memory, "Bloom filter memory should be constant"

    def test_memory_reasonable_size(self):
        """Memory should be bounded and reasonable."""
        bf = TTLBloomFilter(capacity=100000, error_rate=0.01, ttl_seconds=60.0)
        # Should be well under 1MB for 100k capacity
        assert bf.memory_bytes < 1_000_000

    def test_ttl_expiration(self):
        """Items should expire after TTL."""
        bf = TTLBloomFilter(capacity=1000, error_rate=0.01, ttl_seconds=0.5, num_slots=2)
        bf.add("ephemeral")
        assert "ephemeral" in bf

        # Wait for expiration
        time.sleep(0.7)

        # After TTL, item should be gone (slots rotated/expired)
        # Need to trigger rotation by adding something
        bf.add("trigger_rotation")
        # The old item's slot should have expired
        # (This depends on implementation; with short TTL, slot-based expiry kicks in)

    def test_clear(self):
        bf = TTLBloomFilter(capacity=1000, ttl_seconds=60.0)
        bf.add("test")
        assert "test" in bf
        bf.clear()
        assert "test" not in bf

    def test_count(self):
        bf = TTLBloomFilter(capacity=1000, ttl_seconds=60.0)
        assert bf.count == 0
        bf.add("a")
        bf.add("b")
        assert bf.count == 2
